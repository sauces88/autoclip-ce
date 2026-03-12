"""
Step 8: 封面制作 — 火山引擎 Seedream 图片生成

流程：
  1. 加载已有 step8_cover.json（断点续传）
  2. 对每个 clip：
     a. FFmpeg 截取视频第 3 秒截图作为参考图
     b. 上传截图到 MinIO 获取公网 URL
     c. 用 clip title + content/outline 构造 prompt
     d. 调用火山引擎 Seedream API（图生图）生成封面
     e. 下载生成的图片，保存为 {clip_name}_cover.png
     f. 将封面插入视频开头（修复竖屏适配）
  3. 每处理完一个 clip 立即写入 step8_cover.json

失败处理：不降级，直接 raise 中断流程。
"""
import json
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# 火山引擎 Seedream API
SEEDREAM_API_URL = os.getenv("VOLCENGINE_SEEDREAM_API_URL", "https://ark.cn-beijing.volces.com/api/v3/images/generations")
SEEDREAM_MODEL = os.getenv("VOLCENGINE_SEEDREAM_MODEL", "doubao-seedream-5-0-260128")


def _parse_time_value(val) -> float:
    """将 start_time/end_time 解析为浮点秒数"""
    if val is None:
        return 0.0
    s = str(val).strip()
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", s)
    if m:
        h, mi, sec, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return h * 3600 + mi * 60 + sec + ms / 1000.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ─────────────────── 视频尺寸 ───────────────────

def _get_video_dimensions(video_path: Path) -> Tuple[int, int]:
    """获取视频宽高"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.decode("utf-8", errors="replace").strip().split(",")
            if len(parts) >= 2:
                w, h = int(parts[0]), int(parts[1])
                if w > 0 and h > 0:
                    return w, h
    except Exception:
        pass
    return 1920, 1080


def _pick_seedream_size(width: int, height: int) -> str:
    """
    根据截图实际宽高生成 Seedream size 参数（方式 2：WxH 像素值）。

    约束：
      - 总像素 [921600, 16777216]
      - 宽高比 [1/16, 16]
    如果底图总像素不足 921600，等比放大到刚好满足。
    如果超过 16777216，等比缩小。
    """
    MIN_PIXELS = 921600    # 1280x720
    MAX_PIXELS = 16777216  # 4096x4096

    w, h = width, height
    total = w * h

    if total < MIN_PIXELS:
        # 等比放大
        scale = (MIN_PIXELS / total) ** 0.5
        w = int(w * scale) + 1
        h = int(h * scale) + 1
    elif total > MAX_PIXELS:
        # 等比缩小
        scale = (MAX_PIXELS / total) ** 0.5
        w = int(w * scale)
        h = int(h * scale)

    # 确保宽高比在 [1/16, 16]
    ratio = w / h if h > 0 else 1
    if ratio > 16:
        h = max(1, w // 16)
    elif ratio < 1 / 16:
        w = max(1, h // 16)

    return f"{w}x{h}"


# ─────────────────── FFmpeg 截图 ───────────────────

def _extract_mid_frame(video_path: Path, sec: float, out_path: Path) -> bool:
    """用 FFmpeg 提取视频指定秒的截图"""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(max(0.0, sec)),
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        logger.warning(f"提取截图失败: {e}")
        return False


# ─────────────────── 火山引擎 Seedream 封面生成 ───────────────────

def _build_cover_prompt(title: str, content_summary: str = "") -> str:
    """
    构造封面生成 prompt：以原始截图为背景，叠加渐变遮罩和文字排版。
    风格参考 xhs-cover-skill 的 text_overlay_only 模式。
    """
    # 将标题按标点断行（优先在中间附近的标点处拆分）
    title_display = title
    if len(title) > 8:
        best_pos = -1
        best_dist = len(title)
        mid = len(title) // 2
        for sep in ("！", "？", "，", "：", "、", ".", "!", "?", ","):
            idx = title.find(sep)
            while idx != -1:
                if 4 <= idx + 1 <= len(title) - 2:
                    dist = abs(idx + 1 - mid)
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = idx + 1
                idx = title.find(sep, idx + 1)
        if best_pos > 0:
            title_display = title[:best_pos] + " " + title[best_pos:]
        else:
            title_display = title[:mid] + " " + title[mid:]

    subtitle_part = ""
    if content_summary:
        subtitle = content_summary[:60].rstrip("，。、！？")
        subtitle_part = (
            f"Below the gold divider, show one line of smaller white text: '{subtitle}'. "
            f"Only render this subtitle once, do not duplicate any text. "
        )

    return (
        f"Use the original photo as full background, keep it completely intact. "
        f"Apply a smooth cinematic gradient overlay on the lower half only: "
        f"from fully transparent in the middle to deep black at the very bottom. "
        f"In the lower-left area, place the Chinese title '{title_display}' "
        f"in large bold white sans-serif font, left-aligned, with soft shadow behind text. "
        f"The last key phrase of the title must be rendered in bright warm gold color "
        f"(not white), making it visually pop as the emphasis. "
        f"Below the title, add one short thin gold horizontal line as divider. "
        f"{subtitle_part}"
        f"At the very bottom edge, add a thin decorative gradient bar from red through gold to red. "
        f"Style: clean editorial Xiaohongshu cover. "
        f"Important: do not duplicate any text, do not add illustrations or cartoon elements, "
        f"do not replace or modify the background photo, do not add any watermark or logo."
    )


def _generate_cover_with_seedream(
    screenshot_url: str,
    title: str,
    content_summary: str = "",
    size: str = "2K",
) -> str:
    """
    调用火山引擎 Seedream API 生成封面图（图生图）。

    Args:
        screenshot_url: 参考截图的公网 URL
        title: clip 标题
        content_summary: 内容摘要
        size: 图片尺寸（WxH 像素值或 1K/2K/4K）

    Returns:
        生成图片的 URL

    Raises:
        RuntimeError: API key 缺失或调用失败
    """
    api_key = os.getenv("VOLCENGINE_SEEDREAM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "VOLCENGINE_SEEDREAM_API_KEY 未配置，无法生成封面。"
            "请在 .env 中设置 VOLCENGINE_SEEDREAM_API_KEY"
        )

    prompt = _build_cover_prompt(title, content_summary)

    logger.info(f"调用 Seedream 生成封面: {title[:50]}... size={size}")

    resp = requests.post(
        SEEDREAM_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": SEEDREAM_MODEL,
            "prompt": prompt,
            "image": screenshot_url,
            "sequential_image_generation": "disabled",
            "response_format": "url",
            "size": size,
            "stream": False,
            "watermark": False
        },
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Seedream API 调用失败: HTTP {resp.status_code}, {resp.text[:300]}"
        )

    data = resp.json()
    # 提取生成的图片 URL
    # 标准 OpenAI 兼容格式: data.data[0].url
    images = data.get("data", [])
    if not images:
        raise RuntimeError(f"Seedream 返回无图片数据: {json.dumps(data, ensure_ascii=False)[:300]}")

    image_url = images[0].get("url", "")
    if not image_url:
        raise RuntimeError(f"Seedream 返回图片 URL 为空: {json.dumps(images[0], ensure_ascii=False)[:300]}")

    logger.info(f"Seedream 封面生成成功: {image_url[:80]}...")
    return image_url


def _download_image(url: str) -> bytes:
    """下载图片并返回 bytes"""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


# ─────────────────── 封面插入视频 ───────────────────

def _prepend_cover_to_video(cover_path: Path, video_path: Path, cover_sec: float = 0.5) -> bool:
    """
    将封面图作为静止画面（cover_sec 秒）插入视频开头，直接覆盖原视频文件。
    封面图会缩放到视频分辨率，正确适配竖屏。
    """
    try:
        w, h = _get_video_dimensions(video_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "output.mp4"
            # 修复竖屏适配：使用 scale + pad 确保封面图与视频分辨率完全一致
            fc = (
                f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[cov];"
                f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vid];"
                f"[cov][vid]concat=n=2:v=1:a=0[vout];"
                f"anullsrc=r=44100:cl=stereo,atrim=duration={cover_sec}[sil];"
                f"[sil][1:a]concat=n=2:v=0:a=1[aout]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-t", str(cover_sec),
                "-i", str(cover_path),
                "-i", str(video_path),
                "-filter_complex", fc,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                str(out),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[-400:]
                logger.error(f"封面插入视频失败: {err}")
                return False
            shutil.copy2(str(out), str(video_path))
            logger.info(f"封面已插入视频开头: {video_path.name}")
            return True
    except Exception as e:
        logger.error(f"封面插入视频异常: {e}")
        return False


# ─────────────────── step8 JSON 工具 ───────────────────

def _load_step8_json(path: Path) -> Dict[str, Any]:
    """加载已有的 step8_cover.json"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"加载 step8_cover.json 失败，将重新开始: {e}")
    return {"status": "processing", "clips": [], "errors": []}


def _save_step8_json(path: Path, data: Dict[str, Any]) -> None:
    """保存 step8_cover.json"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────── 主入口 ───────────────────

class CoverGenerator:
    def __init__(
        self,
        clips_metadata_path: Path,
        output_dir: Path,
        metadata_dir: Path,
    ):
        self.clips_metadata_path = clips_metadata_path
        self.output_dir = output_dir  # 与字幕视频同一目录
        self.metadata_dir = metadata_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_video_path(self, clip: Dict) -> Optional[Path]:
        """优先用烧录后的字幕视频，否则用原始 clip"""
        p = clip.get("subtitle_video_path")
        if p and Path(p).exists():
            return Path(p)
        for key in ("video_path", "clip_path", "output_path"):
            p = clip.get(key)
            if p and Path(p).exists():
                return Path(p)
        clip_id = str(clip.get("id", ""))
        title = clip.get("generated_title", clip.get("title", f"clip_{clip_id}"))
        safe = re.sub(r'[\\/:*?"<>|]', "_", str(title))
        clips_dir = self.output_dir.parent / "clips"
        for candidate in [
            clips_dir / f"{clip_id}_{safe}.mp4",
            clips_dir / f"{safe}.mp4",
            clips_dir / f"clip_{clip_id}.mp4",
            clips_dir / f"{clip_id}.mp4",
        ]:
            if candidate.exists():
                return candidate
        if clips_dir.exists():
            for f in clips_dir.glob(f"{clip_id}_*.mp4"):
                return f
        return None

    def process(self) -> Dict[str, Any]:
        with open(self.clips_metadata_path, "r", encoding="utf-8") as f:
            clips = json.load(f)

        if not clips:
            return {"status": "skipped", "reason": "no clips"}

        # step8 JSON 断点续传
        step8_json_path = self.metadata_dir / "step8_cover.json"
        step8_data = _load_step8_json(step8_json_path)
        done_ids = {c["clip_id"] for c in step8_data["clips"]}
        if done_ids:
            logger.info(f"step8 断点续传: 已有 {len(done_ids)} 个 clip 封面记录，将跳过")

        results: Dict[str, Any] = {"covers": [], "errors": []}

        for clip in clips:
            clip_id = str(clip.get("id", "unknown"))
            title = clip.get("generated_title", clip.get("title", f"clip_{clip_id}"))
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", str(title))

            # 断点续传：跳过已处理的 clip
            if clip_id in done_ids:
                logger.info(f"clip {clip_id} 已有封面记录，跳过")
                continue

            video_path = self._get_video_path(clip)
            if not video_path:
                logger.warning(f"找不到 clip {clip_id} 的视频文件，跳过封面")
                error_info = {"clip_id": clip_id, "error": "video not found"}
                results["errors"].append(error_info)
                step8_data["errors"].append(error_info)
                _save_step8_json(step8_json_path, step8_data)
                continue

            # 构造内容摘要
            content_summary = clip.get("content", "") or clip.get("outline", "") or ""
            if isinstance(content_summary, list):
                content_summary = "；".join(str(x) for x in content_summary[:5])
            if len(content_summary) > 200:
                content_summary = content_summary[:200] + "..."

            with tempfile.TemporaryDirectory() as tmpdir:
                screenshot_path = Path(tmpdir) / "screenshot.jpg"

                # 1. FFmpeg 截取随机 1~10 秒截图作为参考图
                rand_sec = round(random.uniform(1.0, 10.0), 1)
                has_screenshot = _extract_mid_frame(video_path, rand_sec, screenshot_path)
                if not has_screenshot:
                    has_screenshot = _extract_mid_frame(video_path, 1.0, screenshot_path)
                if not has_screenshot:
                    raise RuntimeError(f"clip {clip_id} 无法截取视频截图")

                # 2. 上传截图到 MinIO 获取公网 URL
                from backend.utils.minio_upload_client import upload_file
                screenshot_url = upload_file(screenshot_path)
                logger.info(f"截图已上传: {screenshot_url[:80]}...")

                # 3. 根据视频尺寸选择 size
                vid_w, vid_h = _get_video_dimensions(video_path)
                size = _pick_seedream_size(vid_w, vid_h)

                # 4. 调用 Seedream 生成封面
                prompt_used = (
                    f"标题: {title}, "
                    f"内容: {content_summary[:100] if content_summary else '无'}"
                )
                image_url = _generate_cover_with_seedream(
                    screenshot_url=screenshot_url,
                    title=str(title),
                    content_summary=content_summary,
                    size=size,
                )

                # 5. 下载并保存封面图
                image_bytes = _download_image(image_url)
                cover_output = self.output_dir / f"{safe_name}_cover.png"
                cover_output.parent.mkdir(parents=True, exist_ok=True)
                cover_output.write_bytes(image_bytes)
                logger.info(f"封面已保存: {cover_output.name} ({len(image_bytes)} bytes)")

            clip["cover_path"] = str(cover_output)
            results["covers"].append(str(cover_output))

            # 6. 将封面图插入字幕视频开头
            cover_inserted = False
            sub_video = clip.get("subtitle_video_path")
            if sub_video and Path(sub_video).exists():
                cover_inserted = _prepend_cover_to_video(cover_output, Path(sub_video))

            # 7. 写入 step8 JSON（每个 clip 完成后立即写入）
            step8_data["clips"].append({
                "clip_id": clip_id,
                "cover_path": str(cover_output),
                "cover_inserted": cover_inserted,
                "prompt_used": prompt_used,
            })
            _save_step8_json(step8_json_path, step8_data)

        # 更新 metadata
        with open(self.clips_metadata_path, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=2)

        # 标记完成
        step8_data["status"] = "done"
        _save_step8_json(step8_json_path, step8_data)

        logger.info(
            f"Step 8 完成: {len(results['covers'])} 个封面, "
            f"{len(results['errors'])} 个错误"
        )
        results["status"] = "done"
        return results


def run_step8_cover(
    clips_metadata_path: Path,
    output_dir: Path,
    metadata_dir: Path,
) -> Dict[str, Any]:
    """
    Step 8 入口：封面制作

    Args:
        clips_metadata_path: clips_metadata.json 路径
        output_dir: 封面输出目录（与字幕视频同一目录，即 clips_with_subs/）
        metadata_dir: 元数据目录
    """
    gen = CoverGenerator(
        clips_metadata_path=clips_metadata_path,
        output_dir=output_dir,
        metadata_dir=metadata_dir,
    )
    return gen.process()
