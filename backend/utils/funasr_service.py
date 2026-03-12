"""
FunASR 转录服务（同步版，适配 Celery pipeline）

基于 video-ai-editor 项目迁移，关键特性：
1. 30秒分段转录（避免时间戳漂移）
2. 字符级时间戳（精准字幕对齐）
3. paraformer-zh 模型（中文识别最优）
4. 静音偏移检测（修正 FunASR VAD 跳过静音导致的时间漂移）
5. Python WAV 切片（比每段调 FFmpeg 更快）
"""
import json
import logging
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional, Dict, List, Callable

logger = logging.getLogger(__name__)


class FunASRService:
    """
    FunASR 转录服务（同步版）

    使用阿里开源的 FunASR Paraformer 模型进行中文语音识别，
    支持字符级时间戳，适合口播视频字幕生成。
    """

    def __init__(self, segment_length: int = 30):
        self._model = None
        self.segment_length = segment_length  # 30秒分段

    def _load_model(self):
        """加载 FunASR 模型（延迟加载，首次调用时初始化）"""
        if self._model is None:
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"Loading FunASR paraformer-zh model on {device}...")
                from funasr import AutoModel

                self._model = AutoModel(
                    model="paraformer-zh",
                    disable_update=True,
                    device=device,
                )
                logger.info("FunASR model loaded successfully")
            except ImportError:
                logger.error("FunASR not installed. Please install: pip install funasr modelscope")
                raise RuntimeError("FunASR not installed. Install with: pip install funasr modelscope")
            except Exception as e:
                logger.error(f"Failed to load FunASR model: {e}")
                raise
        return self._model

    # ─────────────── 主入口 ───────────────

    def transcribe(
        self,
        video_path: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict:
        """
        转录视频/音频文件，返回字符级时间戳。

        Args:
            video_path: 视频（或音频）文件路径
            progress_callback: 可选的进度回调 (percent, message)

        Returns:
            {
                "full_text": "完整转录文本",
                "characters": [
                    {"char": "大", "start": 0.88, "end": 1.12},
                    ...
                ],
                "duration": 217.97,
                "segment_count": 8
            }
        """
        if progress_callback:
            progress_callback(5, "检查视频文件...")

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        if not self._check_audio_track(video_path):
            raise RuntimeError("视频文件没有音频轨道")

        if progress_callback:
            progress_callback(8, "获取视频时长...")

        duration = self._get_duration(video_path)
        if duration < 0.5:
            logger.warning(f"视频太短 ({duration:.2f}s)，跳过转录")
            return {"full_text": "", "characters": [], "duration": duration, "segment_count": 0}

        if progress_callback:
            progress_callback(10, "提取完整音频...")

        # 提取完整 WAV（只调一次 FFmpeg）
        full_wav_path = None
        try:
            full_wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            full_wav_path = full_wav_tmp.name
            full_wav_tmp.close()

            self._extract_audio(video_path, 0, duration, full_wav_path)
            logger.info(f"Full WAV extracted: {os.path.getsize(full_wav_path)} bytes")

            if progress_callback:
                progress_callback(13, "加载 FunASR 模型...")

            model = self._load_model()

            num_segments = int(duration // self.segment_length) + (
                1 if duration % self.segment_length > 0.5 else 0
            )
            num_segments = max(1, num_segments)
            logger.info(f"Splitting audio into {num_segments} segments of {self.segment_length}s")

            if progress_callback:
                progress_callback(15, f"开始转录 (共 {num_segments} 段)...")

            # 逐段转录
            all_characters: List[Dict] = []
            for i in range(num_segments):
                seg_start = i * self.segment_length
                seg_dur = min(self.segment_length, duration - seg_start)
                if seg_dur < 0.3:
                    break

                pct = 15 + 70 * (i / num_segments)
                if progress_callback:
                    progress_callback(pct, f"转录第 {i+1}/{num_segments} 段...")

                # Python 切片 WAV
                seg_wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                seg_wav_path = seg_wav_tmp.name
                seg_wav_tmp.close()

                try:
                    self._slice_wav(full_wav_path, seg_wav_path, seg_start, seg_dur)
                    seg_chars = self._transcribe_wav_segment(seg_wav_path, seg_start, model)
                    all_characters.extend(seg_chars)
                    logger.info(f"Segment {i+1}/{num_segments}: {len(seg_chars)} chars")
                finally:
                    try:
                        os.remove(seg_wav_path)
                    except OSError:
                        pass

            if progress_callback:
                progress_callback(90, "处理转录结果...")

            full_text = "".join(c["char"] for c in all_characters)

            if progress_callback:
                progress_callback(100, "转录完成！")

            return {
                "full_text": full_text,
                "characters": all_characters,
                "duration": duration,
                "segment_count": num_segments,
            }

        finally:
            if full_wav_path:
                try:
                    os.remove(full_wav_path)
                except OSError:
                    pass

    # ─────────────── WAV 切片 ───────────────

    def _slice_wav(self, full_wav_path: str, output_path: str, start_time: float, duration: float):
        """用纯 Python 从完整 WAV 中切片（比每段调 FFmpeg 快得多）"""
        with wave.open(full_wav_path, "rb") as src:
            sample_rate = src.getframerate()
            n_channels = src.getnchannels()
            sampwidth = src.getsampwidth()
            total_frames = src.getnframes()

            start_frame = int(start_time * sample_rate)
            num_frames = int(duration * sample_rate)

            start_frame = min(start_frame, total_frames)
            num_frames = min(num_frames, total_frames - start_frame)

            src.setpos(start_frame)
            frames = src.readframes(num_frames)

        with wave.open(output_path, "wb") as dst:
            dst.setnchannels(n_channels)
            dst.setsampwidth(sampwidth)
            dst.setframerate(sample_rate)
            dst.writeframes(frames)

    # ─────────────── 单段转录 ───────────────

    def _transcribe_wav_segment(
        self,
        wav_path: str,
        segment_offset: float,
        model,
    ) -> List[Dict]:
        """转录单个 WAV 段，返回字符级时间戳列表。"""
        if not os.path.exists(wav_path):
            logger.warning(f"WAV file not found: {wav_path}")
            return []

        wav_size = os.path.getsize(wav_path)
        if wav_size == 0:
            logger.warning("WAV file is empty")
            return []

        # 检查音频时长
        try:
            with wave.open(wav_path, "rb") as wf:
                n_frames = wf.getnframes()
                sr = wf.getframerate()
                audio_dur = n_frames / sr if sr > 0 else 0
                if audio_dur < 0.5:
                    logger.warning(f"Audio segment too short: {audio_dur:.3f}s")
                    return []
        except wave.Error as e:
            logger.error(f"Invalid WAV file: {e}")
            return []

        # 检测开头静音偏移（仅第一段）
        silence_offset = 0.0
        if segment_offset == 0:
            silence_offset = self._detect_silence_offset(wav_path)
            if silence_offset > 0:
                logger.info(f"Segment 0: silence offset = {silence_offset:.3f}s")

        # FunASR 转录
        try:
            result = model.generate(
                input=wav_path,
                return_raw_text=True,
                timestamp_granularity="character",
            )
        except ValueError as e:
            if "window size" in str(e).lower() or "nperseg" in str(e).lower():
                logger.warning(f"Audio segment too short for FunASR: {e}")
                return []
            raise
        except Exception as e:
            err = str(e).lower()
            if "window" in err and "size" in err:
                logger.warning(f"FunASR processing error (possibly too short): {e}")
                return []
            raise

        # 解析结果
        characters: List[Dict] = []
        for item in result:
            if "timestamp" in item and "text" in item:
                text = item["text"].replace(" ", "")
                timestamps = item["timestamp"]
                for char, ts in zip(text, timestamps):
                    if char.strip():
                        characters.append({
                            "char": char,
                            "start": round(segment_offset + silence_offset + ts[0] / 1000, 2),
                            "end": round(segment_offset + silence_offset + ts[1] / 1000, 2),
                        })

        return characters

    # ─────────────── 静音偏移检测 ───────────────

    def _detect_silence_offset(self, wav_path: str) -> float:
        """
        检测音频开头的静音时长。

        FunASR 的 VAD 会跳过静音段，导致时间戳从第一个有效语音重新计时。
        此方法检测被跳过的静音，用于修正时间戳。
        """
        cmd = [
            "ffmpeg",
            "-i", wav_path,
            "-af", "silencedetect=noise=-40dB:d=0.2",
            "-f", "null", "-",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=10, encoding="utf-8", errors="replace")
            matches = re.findall(r"silence_end:\s*([\d.]+)", r.stderr)
            if matches:
                # 取前 5 秒内的最后一个 silence_end
                silences = [float(s) for s in matches if float(s) <= 5.0]
                if silences:
                    offset = silences[-1]
                    logger.info(f"Silence offset: {offset:.3f}s ({len(silences)} segments)")
                    return offset
        except subprocess.TimeoutExpired:
            logger.warning("Silence detection timed out")
        except Exception as e:
            logger.warning(f"Silence detection failed: {e}")
        return 0.0

    # ─────────────── 音频提取 ───────────────

    def _extract_audio(self, video_path: str, start: float, duration: float, output_path: str):
        """用 FFmpeg 从视频中提取 16kHz mono PCM WAV（FunASR 要求）"""
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-ss", str(start),
            "-t", str(duration),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-loglevel", "warning",
            output_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {r.stderr[-500:]}")

    # ─────────────── 辅助方法 ───────────────

    def _check_audio_track(self, video_path: str) -> bool:
        """检查视频是否包含音频轨道"""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            video_path,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30, encoding="utf-8", errors="replace")
            if r.stdout.strip():
                return True
            # 降级检查 codec
            cmd2 = [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            r2 = subprocess.run(cmd2, capture_output=True, timeout=30, encoding="utf-8", errors="replace")
            return bool(r2.stdout.strip())
        except Exception as e:
            logger.warning(f"Audio check failed: {e}, assuming audio exists")
            return True

    def _get_duration(self, video_path: str) -> float:
        """用 ffprobe 获取视频时长（秒）"""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                data = json.loads(r.stdout)
                return float(data["format"]["duration"])
        except Exception as e:
            logger.warning(f"ffprobe duration failed: {e}")
        return 0.0


# 全局单例（模型只加载一次）
_funasr_service: Optional[FunASRService] = None


def get_funasr_service() -> FunASRService:
    """获取全局 FunASR 服务实例"""
    global _funasr_service
    if _funasr_service is None:
        _funasr_service = FunASRService()
    return _funasr_service
