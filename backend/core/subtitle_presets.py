"""
字幕样式预设
每个预设定义 FFmpeg libass 字幕滤镜参数
颜色格式：ASS AABBGGRR（如白色 &H00FFFFFF，半透明黑底 &H80000000）
"""
from typing import Dict, Any

# hex #RRGGBB → ASS &H00BBGGRR（透明度=0，即不透明）
def _hex_to_ass(hex_color: str, alpha: int = 0) -> str:
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"

# hex + alpha（0-255，0=不透明，255=全透明）转 ASS 背景色
def _hex_alpha_to_ass(hex_with_alpha: str) -> str:
    """支持 #RRGGBBAA 格式，AA=透明度（FF=完全透明）"""
    s = hex_with_alpha.lstrip("#")
    if len(s) == 8:
        r, g, b = int(s[0:2],16), int(s[2:4],16), int(s[4:6],16)
        a = int(s[6:8], 16)
        return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"
    return _hex_to_ass(hex_with_alpha)


SUBTITLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "classic": {
        "label": "经典",
        "color": "#ffffff",
        "outline_color": "#000000",
        "back_color": None,
        "outline": 2,
        "shadow": 1,
        "bold": 1,
    },
    "douyin": {
        "label": "抖音",
        "color": "#ffffff",
        "outline_color": "#000000",
        "back_color": None,
        "outline": 3,
        "shadow": 0,
        "bold": 1,
    },
    "xiaohongshu": {
        "label": "小红书",
        "color": "#ffffff",
        "outline_color": "#ffdd00",
        "back_color": None,
        "outline": 2,
        "shadow": 1,
        "bold": 1,
    },
    "warm": {
        "label": "暖白",
        "color": "#fff5e4",
        "outline_color": "#8b6914",
        "back_color": None,
        "outline": 2,
        "shadow": 1,
        "bold": 0,
    },
    "documentary": {
        "label": "纪录片",
        "color": "#f0e6c8",
        "outline_color": "#000000",
        "back_color": "#00000080",  # RRGGBBAA
        "outline": 1,
        "shadow": 0,
        "bold": 0,
    },
    "neon": {
        "label": "霓虹",
        "color": "#00ffcc",
        "outline_color": "#ff00aa",
        "back_color": None,
        "outline": 3,
        "shadow": 2,
        "bold": 1,
    },
    "news": {
        "label": "新闻",
        "color": "#ffffff",
        "outline_color": "#000000",
        "back_color": "#1a3a6eff",  # RRGGBBAA，AA=FF=不透明
        "outline": 1,
        "shadow": 0,
        "bold": 1,
    },
    "ted": {
        "label": "TED",
        "color": "#ffffff",
        "outline_color": "#000000",
        "back_color": "#e62b1eff",
        "outline": 1,
        "shadow": 0,
        "bold": 1,
    },
    "bilibili": {
        "label": "B站",
        "color": "#00aeec",
        "outline_color": "#000000",
        "back_color": None,
        "outline": 1,
        "shadow": 1,
        "bold": 0,
    },
    "youtube": {
        "label": "YouTube",
        "color": "#ffffff",
        "outline_color": "#000000",
        "back_color": "#000000bf",  # bf = 75% 不透明
        "outline": 0,
        "shadow": 0,
        "bold": 0,
    },
    "clean_black": {
        "label": "简约黑",
        "color": "#000000",
        "outline_color": "#ffffff",
        "back_color": "#ffffffd0",
        "outline": 1,
        "shadow": 0,
        "bold": 0,
    },
    "karaoke": {
        "label": "卡拉OK",
        "color": "#ffff00",
        "outline_color": "#ff6600",
        "back_color": None,
        "outline": 3,
        "shadow": 2,
        "bold": 1,
    },
}


def get_ffmpeg_force_style(preset_id: str, width: int, height: int) -> str:
    """
    返回 ffmpeg subtitles 滤镜的 force_style 字符串（不含FontSize/MarginV/PlayRes/WrapStyle）。
    调用方负责在前面拼接这些尺寸参数。
    """
    preset = SUBTITLE_PRESETS.get(preset_id, SUBTITLE_PRESETS["classic"])

    parts = []
    parts.append(f"Bold={preset.get('bold', 1)}")
    parts.append(f"PrimaryColour={_hex_to_ass(preset['color'])}")

    outline_color = preset.get("outline_color") or "#000000"
    parts.append(f"OutlineColour={_hex_to_ass(outline_color)}")

    back_color = preset.get("back_color")
    if back_color:
        parts.append(f"BackColour={_hex_alpha_to_ass(back_color)}")
    else:
        parts.append("BackColour=&H00000000")

    parts.append(f"Outline={preset.get('outline', 2)}")
    parts.append(f"Shadow={preset.get('shadow', 1)}")

    return ",".join(parts)
