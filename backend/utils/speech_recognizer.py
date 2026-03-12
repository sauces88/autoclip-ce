"""
语音识别工具 — Seed ASR AUC（录音文件识别标准版）

流程：
  1. 计算视频文件 MD5
  2. 查 DB asr_cache: video_md5 → utterances_url（命中则跳过 ASR）
  3. 未命中 → FFmpeg 提取音频 → MinIO 上传 → Seed ASR 识别
  4. 保存 utterances JSON → MinIO 上传 → 写 DB 缓存（video_md5 为键）
  5. 保存本地 asr_raw_utterances.json（供 Step 7 使用）
  6. 词典纠错 → 生成 SRT
"""

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── 公共枚举/配置（保持向后兼容） ────────────────────────

class SpeechRecognitionMethod(str, Enum):
    BCUT_ASR       = "bcut_asr"
    WHISPER_LOCAL  = "whisper_local"
    OPENAI_API     = "openai_api"
    AZURE_SPEECH   = "azure_speech"
    GOOGLE_SPEECH  = "google_speech"
    ALIYUN_SPEECH  = "aliyun_speech"
    VOLCENGINE_ASR = "volcengine_asr"


class LanguageCode(str, Enum):
    CHINESE_SIMPLIFIED  = "zh"
    CHINESE_TRADITIONAL = "zh-TW"
    ENGLISH             = "en"
    ENGLISH_US          = "en-US"
    ENGLISH_UK          = "en-GB"
    JAPANESE            = "ja"
    KOREAN              = "ko"
    FRENCH              = "fr"
    GERMAN              = "de"
    SPANISH             = "es"
    RUSSIAN             = "ru"
    ARABIC              = "ar"
    PORTUGUESE          = "pt"
    ITALIAN             = "it"
    AUTO                = "auto"


@dataclass
class SpeechRecognitionConfig:
    method: SpeechRecognitionMethod = SpeechRecognitionMethod.VOLCENGINE_ASR
    language: LanguageCode = LanguageCode.AUTO
    model: str = "base"
    timeout: int = 0
    output_format: str = "srt"
    enable_timestamps: bool = True
    enable_punctuation: bool = True
    enable_speaker_diarization: bool = False
    enable_fallback: bool = False
    fallback_method: SpeechRecognitionMethod = SpeechRecognitionMethod.VOLCENGINE_ASR


class SpeechRecognitionError(Exception):
    pass


# ── 音频工具函数 ──────────────────────────────────────────

def _extract_audio(video_path: Path, out_path: Path,
                   start: float = 0.0, duration: float = 0.0) -> None:
    """从视频/音频提取 MP3（16kHz 单声道 32kbps）。"""
    cmd = ["ffmpeg", "-y"]
    if start > 0:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video_path)]
    if duration > 0:
        cmd += ["-t", str(duration)]
    cmd += ["-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "32k", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise SpeechRecognitionError("音频提取失败")


# ── SRT 转换工具 ──────────────────────────────────────────

def _ms2t(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_r = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_r:03d}"


def _utterances_to_srt_lines(
    utterances: List[Dict],
    time_offset_ms: int = 0,
    start_idx: int = 1,
    prev_end_ms: int = 0,
) -> Tuple[List[str], int, int]:
    """
    将 utterances 转为 SRT 行列表。
    直接使用火山引擎 ASR 原始 utterance 断句（不做 word token 重分组）。
    返回 (srt_lines, next_srt_idx, last_end_ms)
    """
    lines: List[str] = []
    idx = start_idx
    prev_end = prev_end_ms

    # 直接用 ASR utterance 原文作为 SRT 条目
    for utt in utterances:
        utt_text = utt.get("text", "").strip()
        if not utt_text:
            continue
        utt_s = utt.get("start_time", 0) + time_offset_ms
        utt_e = utt.get("end_time", 0) + time_offset_ms
        adj = max(prev_end, utt_s)
        lines.extend([str(idx), f"{_ms2t(adj)} --> {_ms2t(utt_e)}", utt_text, ""])
        idx += 1
        prev_end = utt_e

    if utterances:
        logger.info(f"ASR 直出模式: {len(utterances)} 条 utterance → {idx - start_idx} 条 SRT")

    # --- 以下为 word token 重分组逻辑（暂时注释，观察火山引擎直出效果） ---
    # TARGET_CHARS = 14
    # asr_tokens: List[Dict] = []
    # for utt in utterances:
    #     words = utt.get("words", [])
    #     if words:
    #         for w in words:
    #             w_text = w.get("text", "").strip()
    #             if not w_text:
    #                 continue
    #             asr_tokens.append({
    #                 "text": w_text,
    #                 "start": w["start_time"] + time_offset_ms,
    #                 "end":   w["end_time"]   + time_offset_ms,
    #             })
    #     else:
    #         utt_text = utt.get("text", "").strip()
    #         if not utt_text:
    #             continue
    #         utt_s = utt["start_time"] + time_offset_ms
    #         utt_e = utt["end_time"]   + time_offset_ms
    #         n = len(utt_text)
    #         for j, ch in enumerate(utt_text):
    #             asr_tokens.append({
    #                 "text":  ch,
    #                 "start": int(utt_s + j / n * (utt_e - utt_s)),
    #                 "end":   int(utt_s + (j + 1) / n * (utt_e - utt_s)),
    #             })
    # if not asr_tokens:
    #     return lines, idx, prev_end
    # groups: List[List[Dict]] = []
    # cur_group: List[Dict] = []
    # cur_len = 0
    # for tok in asr_tokens:
    #     tok_len = len(tok["text"])
    #     if cur_group and cur_len + tok_len > TARGET_CHARS:
    #         groups.append(cur_group)
    #         cur_group = []
    #         cur_len = 0
    #     cur_group.append(tok)
    #     cur_len += tok_len
    # if cur_group:
    #     groups.append(cur_group)
    # def emit(text, s, e):
    #     nonlocal idx, prev_end
    #     adj = max(prev_end, s)
    #     lines.extend([str(idx), f"{_ms2t(adj)} --> {_ms2t(e)}", text, ""])
    #     idx += 1
    #     prev_end = e
    # for group in groups:
    #     if not group:
    #         continue
    #     g_text  = "".join(t["text"] for t in group)
    #     g_start = group[0]["start"]
    #     g_end   = group[-1]["end"]
    #     emit(g_text, g_start, g_end)

    return lines, idx, prev_end


# ── DB 缓存工具 ──────────────────────────────────────────

def _compute_file_md5(file_path: Path) -> str:
    """计算文件 MD5（分块读取，适合大文件）。"""
    import hashlib
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _lookup_asr_cache(video_md5: str) -> Optional[str]:
    """查 DB asr_cache，返回 utterances_url 或 None。"""
    try:
        from backend.core.database import SessionLocal
        from backend.models.asr_cache import ASRCache
        db = SessionLocal()
        try:
            row = db.query(ASRCache).filter(ASRCache.video_md5 == video_md5).first()
            if row:
                return row.utterances_url
            return None
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"查询 ASR 缓存失败: {e}")
        return None


def _save_asr_cache(video_md5: str, utterances_url: str) -> None:
    """写 DB asr_cache。"""
    try:
        from backend.core.database import SessionLocal
        from backend.models.asr_cache import ASRCache
        db = SessionLocal()
        try:
            row = ASRCache(video_md5=video_md5, utterances_url=utterances_url)
            db.add(row)
            db.commit()
            logger.info(f"ASR 缓存已写入 DB: md5={video_md5}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"写入 ASR 缓存失败: {e}")


def _download_json_from_url(url: str) -> List[Dict]:
    """从 URL 下载 JSON 并解析。"""
    import requests
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


# ── 词典纠错 ──────────────────────────────────────────────

def _load_correction_dict() -> Dict[str, str]:
    """加载 backend/data/词典.txt，返回 {错误: 正确} 替换表。"""
    dict_path = Path(__file__).parent.parent / "data" / "词典.txt"
    replacements: Dict[str, str] = {}
    if not dict_path.exists():
        return replacements
    try:
        for line in dict_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "→" in line:
                wrong, correct = line.split("→", 1)
                wrong, correct = wrong.strip(), correct.strip()
                if wrong and correct:
                    replacements[wrong] = correct
    except Exception as e:
        logger.warning(f"加载词典失败: {e}")
    return replacements


_DICT_REPLACEMENTS: Dict[str, str] = _load_correction_dict()


def _apply_dict(text: str) -> str:
    """用词典替换表修正文本中的已知错误。"""
    for wrong, correct in _DICT_REPLACEMENTS.items():
        text = text.replace(wrong, correct)
    return text


# ── 主识别器类 ────────────────────────────────────────────

class SpeechRecognizer:
    """语音识别器（Seed ASR AUC 录音文件识别标准版）"""

    def __init__(self, config: Optional[SpeechRecognitionConfig] = None):
        self.config = config or SpeechRecognitionConfig()
        self.available_methods = self._check_available_methods()

    def _check_available_methods(self) -> Dict[SpeechRecognitionMethod, bool]:
        volc_ok = bool(
            os.getenv("VOLCENGINE_ASR_APP_ID") and os.getenv("VOLCENGINE_ASR_ACCESS_TOKEN")
        )
        return {m: (m == SpeechRecognitionMethod.VOLCENGINE_ASR and volc_ok)
                for m in SpeechRecognitionMethod}

    def get_available_methods(self) -> Dict[SpeechRecognitionMethod, bool]:
        return self.available_methods.copy()

    def get_supported_languages(self) -> List[LanguageCode]:
        return list(LanguageCode)

    def get_whisper_models(self) -> List[str]:
        return ["tiny", "base", "small", "medium", "large"]

    def generate_subtitle(self, video_path: Path,
                          output_path: Optional[Path] = None,
                          config: Optional[SpeechRecognitionConfig] = None,
                          metadata_dir: Optional[Path] = None) -> Path:
        if not video_path.exists():
            raise SpeechRecognitionError(f"视频文件不存在: {video_path}")
        config = config or self.config
        if output_path is None:
            output_path = video_path.parent / f"{video_path.stem}.{config.output_format}"
        return self._generate_subtitle_seed_asr(video_path, output_path, metadata_dir)

    def _generate_subtitle_seed_asr(self, video_path: Path, output_path: Path,
                                     metadata_dir: Optional[Path] = None) -> Path:
        """Seed ASR AUC：上传 MinIO → DB 缓存查询 → 异步识别 → 生成 SRT"""
        if not self.available_methods.get(SpeechRecognitionMethod.VOLCENGINE_ASR, False):
            raise SpeechRecognitionError(
                "火山引擎ASR不可用，请在 .env 中配置 "
                "VOLCENGINE_ASR_APP_ID 和 VOLCENGINE_ASR_ACCESS_TOKEN"
            )

        logger.info(f"开始 Seed ASR 识别: {video_path.name}")

        # 检查本地 utterances 缓存（最快路径）
        cached_utterances = None
        if metadata_dir:
            raw_path = metadata_dir / "asr_raw_utterances.json"
            if raw_path.exists() and raw_path.stat().st_size > 100:
                try:
                    cached_utterances = json.loads(raw_path.read_text(encoding="utf-8"))
                    logger.info(f"复用本地 asr_raw_utterances.json（{len(cached_utterances)} 条），跳过上传和 ASR")
                except Exception as e:
                    logger.warning(f"读取本地缓存 utterances 失败，将重新识别: {e}")
                    cached_utterances = None

        if cached_utterances:
            utterances = cached_utterances
        else:
            # 1. 计算视频文件 MD5（缓存键）
            video_md5 = _compute_file_md5(video_path)
            logger.info(f"视频 MD5: {video_md5}")

            # 2. 查 DB 缓存
            cached_url = _lookup_asr_cache(video_md5)
            if cached_url:
                logger.info(f"DB 缓存命中，下载 utterances: {cached_url[:80]}...")
                try:
                    utterances = _download_json_from_url(cached_url)
                    logger.info(f"从 DB 缓存恢复 {len(utterances)} 条 utterance")
                except Exception as e:
                    logger.warning(f"下载缓存 utterances 失败，重新 ASR: {e}")
                    utterances = None
            else:
                utterances = None

            # 3. 未命中 → 提取音频 → 上传 → ASR
            if utterances is None:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp = Path(tmpdir)

                    full_audio = tmp / "full_audio.mp3"
                    _extract_audio(video_path, full_audio)
                    logger.info(f"音频提取完成: {full_audio.stat().st_size / 1024 / 1024:.1f}MB")

                    from backend.utils.minio_upload_client import upload_file
                    audio_url = upload_file(full_audio)
                    logger.info(f"音频已上传: {audio_url}")

                    from backend.utils.seed_asr import transcribe
                    utterances = transcribe(audio_url)

                    if not utterances:
                        raise SpeechRecognitionError("Seed ASR 未识别到有效内容")

                    logger.info(f"ASR 识别完成: {len(utterances)} 条 utterance")

                    # 4. 保存 utterances JSON → MinIO → 写 DB 缓存
                    try:
                        utt_json_path = tmp / "utterances.json"
                        utt_json_path.write_text(
                            json.dumps(utterances, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        utt_url = upload_file(utt_json_path)
                        _save_asr_cache(video_md5, utt_url)
                        logger.info(f"utterances 已上传并缓存: {utt_url[:80]}...")
                    except Exception as e:
                        logger.warning(f"上传/缓存 utterances 失败（不影响主流程）: {e}")

            # 6. 保存本地 asr_raw_utterances.json（无论走 ASR 还是 DB 缓存都要保存）
            if metadata_dir:
                metadata_dir.mkdir(parents=True, exist_ok=True)
                raw_path = metadata_dir / "asr_raw_utterances.json"
                raw_path.write_text(
                    json.dumps(utterances, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(f"原始 utterances 已保存: {raw_path}")

        # 7. 词典纠错 → 按 word token 分组 → 生成 SRT
        logger.info(f"开始词典纠错（{len(utterances)} 条 utterance）...")
        for utt in utterances:
            utt["text"] = _apply_dict(utt.get("text", ""))
            for w in utt.get("words", []):
                w["text"] = _apply_dict(w.get("text", ""))
        logger.info("词典纠错完成，开始 word token 分组 + SRT 生成...")

        srt_lines, srt_idx, _ = _utterances_to_srt_lines(utterances)
        logger.info(f"SRT 生成完成: {srt_idx - 1} 条字幕行")

        if not srt_lines:
            raise SpeechRecognitionError("Seed ASR 未生成有效字幕")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(srt_lines), encoding="utf-8")
        logger.info(f"字幕生成完成: {output_path}，共 {srt_idx - 1} 条")
        return output_path


# ── 便捷函数（保持向后兼容） ─────────────────────────────

def generate_subtitle_for_video(
    video_path: Path,
    output_path: Optional[Path] = None,
    method: str = "auto",
    language: str = "auto",
    model: str = "base",
    enable_fallback: bool = False,
    metadata_dir: Optional[Path] = None,
) -> Path:
    config = SpeechRecognitionConfig(
        method=SpeechRecognitionMethod.VOLCENGINE_ASR,
        language=LanguageCode(language) if language != "auto" else LanguageCode.AUTO,
        model=model,
    )
    return SpeechRecognizer(config).generate_subtitle(
        video_path, output_path, config, metadata_dir=metadata_dir
    )


def get_available_speech_recognition_methods() -> Dict[str, bool]:
    return {
        m.value: (m == SpeechRecognitionMethod.VOLCENGINE_ASR and
                  bool(os.getenv("VOLCENGINE_ASR_APP_ID") and os.getenv("VOLCENGINE_ASR_ACCESS_TOKEN")))
        for m in SpeechRecognitionMethod
    }


def get_supported_languages() -> List[str]:
    return [lang.value for lang in LanguageCode]


def get_whisper_models() -> List[str]:
    return ["tiny", "base", "small", "medium", "large"]
