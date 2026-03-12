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
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 中文字体路径（粗体优先）
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑粗体 (Windows)
    "C:/Windows/Fonts/simhei.ttf",   # 黑体 (Windows)
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",  # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/PingFang.ttc",  # macOS
]

# 火山引擎 Seedream API
SEEDREAM_API_URL = os.getenv("VOLCENGINE_SEEDREAM_API_URL", "https://ark.cn-beijing.volces.com/api/v3/images/generations")
SEEDREAM_MODEL = os.getenv("VOLCENGINE_SEEDREAM_MODEL", "doubao-seedream-5-0-260128")

# cover_prompt.txt 模板路径
_COVER_PROMPT_TEMPLATE_PATH = Path(__file__).parent.parent / "prompt" / "cover_prompt.txt"


def _generate_cover_prompt_via_llm(
    title: str,
    content_summary: str = "",
    width: int = 0,
    height: int = 0,
    cover_title: str = "",
    cover_subtitle: str = "",
) -> Optional[Dict[str, Any]]:
    """
    用 LLM 生成 Seedream prompt + 小红书发帖文案。

    Args:
        title: clip 原始标题
        content_summary: 内容摘要
        width: 视频/截图宽度
        height: 视频/截图高度
        cover_title: 封面上要渲染的大标题（前端 input 传入）
        cover_subtitle: 封面上要渲染的副标题（前端 input 传入）

    Returns:
        dict: {title, content_polished, tags, seedream_prompt} 或 None（失败时）
    """
    try:
        if not _COVER_PROMPT_TEMPLATE_PATH.exists():
            logger.warning(f"cover_prompt.txt 不存在: {_COVER_PROMPT_TEMPLATE_PATH}")
            return None

        template = _COVER_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

        # 封面标题/副标题默认用 clip 原始数据
        if not cover_title:
            cover_title = title
        if not cover_subtitle:
            cover_subtitle = content_summary[:50] if content_summary else ""

        # 判断画面比例
        if width > 0 and height > 0:
            ratio = width / height
            if ratio > 1.2:
                aspect_ratio = f"横版 ({width}x{height})"
            elif ratio < 0.8:
                aspect_ratio = f"竖版 ({width}x{height})"
            else:
                aspect_ratio = f"近正方形 ({width}x{height})"
        else:
            aspect_ratio = "未知比例"

        # 替换模板变量
        prompt = (
            template
            .replace("{cover_title}", cover_title)
            .replace("{cover_subtitle}", cover_subtitle)
            .replace("{title}", title)
            .replace("{content_summary}", content_summary or "无")
            .replace("{aspect_ratio}", aspect_ratio)
        )

        from backend.core.llm_manager import get_llm_manager
        llm = get_llm_manager()

        def _safe_log(msg: str):
            """强制 UTF-8 输出，绕过 Windows GBK 限制"""
            import sys
            for h in logging.root.handlers + logger.handlers:
                if hasattr(h, 'stream') and hasattr(h.stream, 'reconfigure'):
                    try:
                        h.stream.reconfigure(encoding='utf-8')
                    except Exception:
                        pass
            logger.info(msg)

        _safe_log(f"{'='*60}")
        _safe_log(f"[LLM cover] prompt (len={len(prompt)}):")
        _safe_log(prompt)
        _safe_log(f"{'='*60}")

        raw_response = llm.call_with_retry(prompt, max_retries=2)

        _safe_log(f"[LLM cover] raw response:")
        _safe_log(raw_response or "(empty)")
        _safe_log(f"{'='*60}")

        if not raw_response:
            logger.warning("LLM 返回空响应")
            return None

        result = llm.parse_json_response(raw_response)

        _safe_log(f"[LLM cover] parsed JSON:")
        _safe_log(json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result))

        # 校验必要字段
        if not isinstance(result, dict):
            logger.warning(f"LLM 返回非 dict: {type(result)}")
            return None

        required_keys = {"title", "content_polished", "tags", "seedream_prompt"}
        if not required_keys.issubset(result.keys()):
            missing = required_keys - set(result.keys())
            logger.warning(f"LLM 返回缺少字段: {missing}")
            return None

        # 确保 tags 是列表
        if isinstance(result["tags"], str):
            result["tags"] = [t.strip() for t in result["tags"].split(",") if t.strip()]

        logger.info(f"[LLM 封面文案] 最终结果: title={result['title']}, tags={result['tags']}")
        logger.info(f"[LLM 封面文案] seedream_prompt: {result['seedream_prompt']}")
        return result

    except Exception as e:
        logger.warning(f"LLM 生成封面文案失败，将使用默认 prompt: {e}")
        return None


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
    MIN_PIXELS = 3686400   # 1920x1920 (Seedream API 最低要求)
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
    构造封面生成 prompt：只做背景美化，不加任何文字。
    文字由 PIL 后续精确叠加。
    """
    return (
        "Use the original photo as full background, keep it completely intact. "
        "Apply a smooth cinematic gradient overlay on the lower 40% of the image: "
        "from fully transparent in the middle to deep rich black at the very bottom. "
        "At the very bottom edge, add a thin decorative gradient bar from red through gold to red. "
        "Style: clean cinematic look, moody atmosphere. "
        "Important: do NOT add any text, titles, watermarks, logos, or written characters of any kind. "
        "Do NOT add illustrations or cartoon elements. "
        "Do NOT replace or modify the background photo content. "
        "Only apply the gradient overlay and bottom decorative bar."
    )


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    """获取中文字体，按候选列表依次尝试"""
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("未找到中文字体，使用默认字体")
    return ImageFont.load_default()


def _wrap_text_by_pixels(
    text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw
) -> list:
    """
    按实际像素宽度自动换行。优先在标点处断行，否则按像素宽度强制断。
    """
    separators = set("，。！？、：；,!?:;")
    lines = []
    remaining = text.strip()

    while remaining:
        # 如果整行能放下，直接加入
        bbox = draw.textbbox((0, 0), remaining, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            lines.append(remaining)
            break

        # 逐字符找断点
        best_break = 0
        last_sep_break = 0
        for i in range(1, len(remaining) + 1):
            chunk = remaining[:i]
            bbox = draw.textbbox((0, 0), chunk, font=font)
            chunk_w = bbox[2] - bbox[0]
            if chunk_w <= max_width:
                best_break = i
                if i < len(remaining) and remaining[i - 1] in separators:
                    last_sep_break = i
            else:
                break

        # 优先在标点处断行
        if last_sep_break >= 3:
            cut = last_sep_break
        elif best_break >= 1:
            cut = best_break
        else:
            cut = 1  # 至少放一个字

        lines.append(remaining[:cut])
        remaining = remaining[cut:]

    return lines


def _overlay_text_on_cover(
    image_bytes: bytes,
    title: str,
    content_summary: str = "",
) -> bytes:
    """
    用 PIL 在 Seedream 生成的背景图上叠加精确中文文字。
    基于像素宽度自动换行，文字超出时自动缩小字号。

    布局（从底部向上）：
      - 底部装饰条（由 Seedream 生成）
      - 副标题（小字白色）
      - 金色分割线
      - 标题（大字白色，末尾关键词金色）
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size

    # 颜色
    white = (255, 255, 255, 255)
    gold = (255, 200, 50, 255)
    shadow_color = (0, 0, 0, 180)
    subtitle_color = (200, 200, 200, 230)

    # 边距
    margin_x = int(w * 0.06)
    margin_bottom = int(h * 0.05)
    max_text_width = w - margin_x * 2  # 文字可用最大宽度
    min_top_y = int(h * 0.35)  # 文字不能超过图片 35% 以上（保护人脸区域）

    # ─── 自适应字号：如果文字太多放不下，自动缩小 ───
    title_font_size = max(36, int(h * 0.042))
    sub_font_size = max(20, int(h * 0.024))

    for _attempt in range(5):
        title_font = _get_font(title_font_size)
        sub_font = _get_font(sub_font_size)

        # 创建临时 draw 用于测量
        tmp_img = Image.new("RGBA", (w, h))
        tmp_draw = ImageDraw.Draw(tmp_img)

        # 预测总高度
        title_lines = _wrap_text_by_pixels(title, title_font, max_text_width, tmp_draw)
        line_spacing = int(title_font_size * 0.35)
        total_height = margin_bottom

        # 副标题高度
        if content_summary:
            subtitle = content_summary[:50].rstrip("，。、！？")
            sb = tmp_draw.textbbox((0, 0), subtitle, font=sub_font)
            total_height += (sb[3] - sb[1]) + int(sub_font_size * 0.5)

        # 分割线高度
        total_height += int(h * 0.025)

        # 标题高度
        for line in title_lines:
            bb = tmp_draw.textbbox((0, 0), line, font=title_font)
            total_height += (bb[3] - bb[1]) + line_spacing

        # 检查是否溢出
        if h - total_height >= min_top_y:
            break  # 不溢出，可以用当前字号

        # 缩小字号重试
        title_font_size = max(24, int(title_font_size * 0.85))
        sub_font_size = max(16, int(sub_font_size * 0.85))

    # ─── 正式绘制 ───
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    title_lines = _wrap_text_by_pixels(title, title_font, max_text_width, draw)
    cursor_y = h - margin_bottom

    # 1. 副标题
    if content_summary:
        subtitle = content_summary[:50].rstrip("，。、！？")
        sub_lines = _wrap_text_by_pixels(subtitle, sub_font, max_text_width, draw)
        for sl in reversed(sub_lines):
            sb = draw.textbbox((0, 0), sl, font=sub_font)
            sl_h = sb[3] - sb[1]
            cursor_y -= sl_h
            draw.text((margin_x + 2, cursor_y + 2), sl, font=sub_font, fill=shadow_color)
            draw.text((margin_x, cursor_y), sl, font=sub_font, fill=subtitle_color)
            cursor_y -= int(sub_font_size * 0.3)

    # 2. 金色分割线
    cursor_y -= int(h * 0.008)
    line_len = int(w * 0.22)
    draw.line(
        [(margin_x, cursor_y), (margin_x + line_len, cursor_y)],
        fill=gold, width=max(2, int(h * 0.0025))
    )
    cursor_y -= int(h * 0.02)

    # 3. 标题（多行，从下往上绘制）
    line_spacing = int(title_font_size * 0.35)

    for i in range(len(title_lines) - 1, -1, -1):
        line = title_lines[i]
        bb = draw.textbbox((0, 0), line, font=title_font)
        lh = bb[3] - bb[1]
        cursor_y -= lh

        # 溢出保护：不画到 min_top_y 以上
        if cursor_y < min_top_y:
            break

        is_last_line = (i == len(title_lines) - 1)
        fill_color = gold if (is_last_line and len(title_lines) > 1) else white

        # 阴影
        draw.text((margin_x + 2, cursor_y + 2), line, font=title_font, fill=shadow_color)
        draw.text((margin_x, cursor_y), line, font=title_font, fill=fill_color)

        cursor_y -= line_spacing

    # 合成
    img = Image.alpha_composite(img, overlay)
    img = img.convert("RGB")

    buf = BytesIO()
    img.save(buf, format="PNG", quality=95)
    return buf.getvalue()


def _generate_cover_with_seedream(
    screenshot_url: str,
    title: str,
    content_summary: str = "",
    size: str = "2K",
    custom_prompt: Optional[str] = None,
) -> str:
    """
    调用火山引擎 Seedream API 生成封面图（图生图）。

    Args:
        screenshot_url: 参考截图的公网 URL
        title: clip 标题
        content_summary: 内容摘要
        size: 图片尺寸（WxH 像素值或 1K/2K/4K）
        custom_prompt: 自定义 prompt（由 LLM 生成），为 None 时使用默认 prompt

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

    prompt = custom_prompt if custom_prompt else _build_cover_prompt(title, content_summary)

    seedream_params = {
        "model": SEEDREAM_MODEL,
        "prompt": prompt,
        "image": screenshot_url,
        "sequential_image_generation": "disabled",
        "response_format": "url",
        "size": size,
        "stream": False,
        "watermark": False,
    }

    logger.info(f"{'='*60}")
    logger.info(f"[Seedream API] 请求参数：")
    logger.info(f"  API URL: {SEEDREAM_API_URL}")
    logger.info(f"  model: {SEEDREAM_MODEL}")
    logger.info(f"  size: {size}")
    logger.info(f"  image: {screenshot_url}")
    logger.info(f"  prompt (来源: {'LLM自定义' if custom_prompt else '硬编码默认'}):")
    logger.info(f"  {prompt}")
    logger.info(f"{'='*60}")

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

                # 4. 用 LLM 生成小红书风格文案 + Seedream prompt
                llm_result = _generate_cover_prompt_via_llm(
                    str(title), content_summary, vid_w, vid_h,
                    cover_title=str(title), cover_subtitle=content_summary[:50] if content_summary else "",
                )

                if llm_result:
                    seedream_prompt = llm_result["seedream_prompt"]
                    xhs_title = llm_result["title"]
                    xhs_content = llm_result["content_polished"]
                    xhs_tags = llm_result["tags"]
                else:
                    seedream_prompt = None  # 使用默认 prompt
                    xhs_title = ""
                    xhs_content = ""
                    xhs_tags = []

                prompt_used = seedream_prompt or _build_cover_prompt(str(title), content_summary)

                image_url = _generate_cover_with_seedream(
                    screenshot_url=screenshot_url,
                    title=str(title),
                    content_summary=content_summary,
                    size=size,
                    custom_prompt=seedream_prompt,
                )

                # 5. 下载 Seedream 生成的封面（文字已由 Seedream 渲染，不再用 PIL 叠字）
                raw_bytes = _download_image(image_url)
                cover_output = self.output_dir / f"{safe_name}_cover.png"
                cover_output.parent.mkdir(parents=True, exist_ok=True)
                cover_output.write_bytes(raw_bytes)
                logger.info(f"封面已保存: {cover_output.name} ({len(raw_bytes)} bytes)")

            clip["cover_path"] = str(cover_output)
            results["covers"].append(str(cover_output))

            # 6. 将封面图插入字幕视频开头
            cover_inserted = False
            sub_video = clip.get("subtitle_video_path")
            if sub_video and Path(sub_video).exists():
                cover_inserted = _prepend_cover_to_video(cover_output, Path(sub_video))

            # 7. 写入 step8 JSON（每个 clip 完成后立即写入，含 xhs 字段）
            step8_data["clips"].append({
                "clip_id": clip_id,
                "cover_path": str(cover_output),
                "cover_inserted": cover_inserted,
                "prompt_used": prompt_used,
                "xhs_title": xhs_title,
                "xhs_content": xhs_content,
                "xhs_tags": xhs_tags,
                "seedream_prompt": seedream_prompt or prompt_used,
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
