"""
Step 7: 字幕生成 + 纠错 + 烧录

新流程（基于 ASR utterances，优先）：
  1. 加载 asr_raw_utterances.json + clips_metadata.json
  2. 对每个 clip：按时间范围提取 utterances，调整时间戳为 clip 相对时间
  3. 上下文感知的语气词过滤
  4. 词典替换
  5. 按 word 时间戳做字幕断句（~14 字/条）
  6. LLM 纠错
  7. 生成 SRT + FFmpeg libass 烧录
  8. 更新 clips_metadata.json
  9. 输出 step7_subtitle.json（支持断点续传）

降级流程（asr_raw_utterances.json 不存在时）：
  从全局 SRT 提取 → 简单合并 → 纠错 → 烧录
"""
import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────── 语气词 / 口头禅集合 ───────────────────

# 独立出现时删除整条 utterance 的短语气词
SHORT_FILLER_WORDS = {"嗯", "呃", "啊", "哎", "诶", "额", "唉", "哦", "噢", "欸", "嘿", "嘛", "呀"}

# 独立出现时删除的单字应答
SINGLE_CHAR_RESPONSES = {"对", "是", "好", "行", "嗯", "啊", "哦", "噢", "OK", "ok", "Ok"}

# 口头禅短语（整句匹配，不含标点，因为匹配前会去标点）
VERBAL_TICS = {"对吧", "是吧", "好吧", "对不对", "是不是", "你知道吗", "就是说", "怎么说呢", "好ok", "对对吧", "是的", "好的", "对的"}



# 所有语气词字符（用于从句子中过滤独立语气字符 — 仅降级模式使用）
FILLER_CHARS = set("嗯啊哎诶呃额唉哦噢呀欸嗨嘿")

# 句中夹杂的语气字符（不含"嗯"，因为"嗯"可作正常用字）
INLINE_FILLER_CHARS = set("啊哎诶呃额唉哦噢呀欸嗨嘿")


# ─────────────────── 词典加载 ───────────────────

def _load_correction_dict() -> tuple:
    """加载 backend/data/词典.txt，返回 (replacements, vocab)。"""
    dict_path = Path(__file__).parent.parent / "data" / "词典.txt"
    replacements: Dict[str, str] = {}
    vocab: List[str] = []
    if not dict_path.exists():
        logger.warning(f"词典文件不存在: {dict_path}")
        return replacements, vocab
    try:
        for line in dict_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "→" in line:
                parts = line.split("→", 1)
                wrong, correct = parts[0].strip(), parts[1].strip()
                if wrong and correct:
                    replacements[wrong] = correct
            else:
                vocab.append(line)
    except Exception as e:
        logger.warning(f"加载词典失败: {e}")
    logger.info(f"词典加载完成: {len(replacements)} 条替换映射, {len(vocab)} 个标准词汇")
    return replacements, vocab


_REPLACEMENTS, _VOCAB = _load_correction_dict()


# ─────────────────── SRT 时间工具 ───────────────────

def _parse_srt_time(ts: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", ts.strip())
    if not m:
        return 0.0
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + mi * 60 + s + ms / 1000.0


def _parse_time_value(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip()
    if re.match(r"\d+:\d+:\d+[,.]", s):
        s = s.replace(".", ",")
        return _parse_srt_time(s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _seconds_to_srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ms2t(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_r = divmod(rem, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms_r:03d}"


# ─────────────────── SRT 解析（降级路径用） ───────────────────

def _parse_srt(srt_path: Path) -> List[Dict]:
    """解析 SRT 文件 → [{start, end, text}, ...]"""
    entries = []
    try:
        content = srt_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = srt_path.read_text(encoding="gbk")
        except Exception as e:
            logger.error(f"读取SRT失败: {e}")
            return entries

    for block in re.split(r"\n\s*\n", content.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            int(lines[0].strip())
        except ValueError:
            continue
        tm = re.match(r"(.+?)\s*-->\s*(.+)", lines[1])
        if not tm:
            continue
        entries.append({
            "start": _parse_srt_time(tm.group(1)),
            "end": _parse_srt_time(tm.group(2)),
            "text": "\n".join(lines[2:]).strip(),
        })
    return entries


def _extract_clip_entries(all_entries: List[Dict], clip_start: float, clip_end: float) -> List[Dict]:
    """从全局 SRT 中筛选 clip 时间段内的条目（降级路径用）"""
    result = []
    for e in all_entries:
        if e["end"] <= clip_start or e["start"] >= clip_end:
            continue
        result.append({
            "start": max(0.0, e["start"] - clip_start),
            "end": min(clip_end - clip_start, e["end"] - clip_start),
            "text": e["text"],
        })
    return result


# ─────────────────── utterances 提取与过滤 ───────────────────

def _extract_clip_utterances(
    all_utterances: List[Dict],
    clip_start_ms: int,
    clip_end_ms: int,
) -> List[Dict]:
    """
    从全局 utterances 中提取属于 clip 时间范围的 utterances，
    并将时间戳调整为 clip 相对时间（0 起点）。
    """
    result = []
    for utt in all_utterances:
        utt_start = utt.get("start_time", 0)
        utt_end = utt.get("end_time", 0)

        # 跳过完全不在 clip 范围内的
        if utt_end <= clip_start_ms or utt_start >= clip_end_ms:
            continue

        # 深拷贝并调整时间戳
        new_utt = {
            "text": utt.get("text", ""),
            "start_time": max(0, utt_start - clip_start_ms),
            "end_time": utt_end - clip_start_ms,
            "words": [],
        }
        for w in utt.get("words", []):
            w_start = w.get("start_time", 0)
            w_end = w.get("end_time", 0)
            if w_end <= clip_start_ms or w_start >= clip_end_ms:
                continue
            new_utt["words"].append({
                "text": w.get("text", ""),
                "start_time": max(0, w_start - clip_start_ms),
                "end_time": w_end - clip_start_ms,
            })
        result.append(new_utt)

    return result


def _is_filler_utterance(utt: Dict, clip_relative_start_ms: int = 0) -> bool:
    """
    判断一条 utterance 是否为应删除的语气词/口头禅。
    仅当 utterance 整条文本匹配时才删除，保留出现在有意义句子中的情况。
    """
    text = utt.get("text", "").strip()
    if not text:
        return True

    # 去掉标点后判断
    clean = re.sub(r'[，。？！、；：,.?!;:\s]', '', text)
    if not clean:
        return True

    # 独立短语气词
    if clean in SHORT_FILLER_WORDS:
        return True

    # 独立短应答（单字 + OK 等）
    if clean in SINGLE_CHAR_RESPONSES:
        return True

    # 口头禅短语
    if clean in VERBAL_TICS:
        return True

    return False


def _strip_filler_phrases(text: str) -> str:
    """
    去除句中被标点隔开的独立语气词/口头禅。
    例: "你的 insight 非常重要，好" → "你的 insight 非常重要"
    例: "十二月十九号这个 SUV，好ok，这个新的政策" → "十二月十九号这个 SUV，这个新的政策"
    """
    # 按标点切分，保留分隔符: [content, delim, content, delim, ...]
    tokens = re.split(r'([，。！？、；：,;:!?]+)', text)

    # 配对: [(content, delim), ...]
    pairs = []
    i = 0
    while i < len(tokens):
        content = tokens[i]
        delim = tokens[i + 1] if i + 1 < len(tokens) else ""
        pairs.append((content, delim))
        i += 2

    # 过滤掉内容为语气词的段
    filtered = []
    for content, delim in pairs:
        clean = re.sub(r'\s+', '', content)
        if not clean:
            continue
        if clean in SHORT_FILLER_WORDS or clean in SINGLE_CHAR_RESPONSES or clean in VERBAL_TICS:
            continue
        filtered.append((content, delim))

    if not filtered:
        return ""

    # 重新拼接（最后一段不加尾部分隔符）
    result = ""
    for j, (content, delim) in enumerate(filtered):
        result += content
        if j < len(filtered) - 1:
            result += delim
    return result.strip()


# ─────────────────── 词典替换 ───────────────────

def _apply_dict_replacements(text: str) -> str:
    for wrong, correct in _REPLACEMENTS.items():
        text = text.replace(wrong, correct)
    return text


# ─────────────────── 长句拆分（按标点） ───────────────────

_PUNCT_BREAK = set('，。！？、；：,;:!?')  # 可断标点
_MAX_LINE_CHARS = 20                        # 超过此长度的句子才拆分


def _split_long_entry(entry: Dict) -> List[Dict]:
    """
    将一条长字幕按标点拆分为多条。
    短于 _MAX_LINE_CHARS 的直接返回不动。
    """
    text = entry.get("text", "")
    if len(text) <= _MAX_LINE_CHARS:
        return [entry]

    t0, t1 = entry["start"], entry["end"]
    n = len(text)

    # 找所有标点断点（断在标点之后）
    breakpoints: List[int] = []
    for i, ch in enumerate(text):
        if ch in _PUNCT_BREAK and 0 < i < n - 1:
            breakpoints.append(i + 1)

    if not breakpoints:
        return [entry]

    # 贪心分行：每行尽量接近 _MAX_LINE_CHARS，在最近的标点处断
    segments: List[Dict] = []
    start_pos = 0
    while start_pos < n:
        remaining = n - start_pos
        if remaining <= _MAX_LINE_CHARS:
            seg_text = text[start_pos:]
            seg_s = t0 + start_pos / n * (t1 - t0)
            segments.append({"start": seg_s, "end": t1, "text": seg_text})
            break

        # 找 [start_pos, start_pos + _MAX_LINE_CHARS] 范围内最后一个断点
        best = -1
        for bp in breakpoints:
            if bp <= start_pos:
                continue
            if bp - start_pos <= _MAX_LINE_CHARS:
                best = bp
            else:
                break

        if best > start_pos:
            seg_text = text[start_pos:best]
            seg_s = t0 + start_pos / n * (t1 - t0)
            seg_e = t0 + best / n * (t1 - t0)
            segments.append({"start": seg_s, "end": seg_e, "text": seg_text})
            start_pos = best
        else:
            # 范围内无断点，找下一个断点
            next_bp = None
            for bp in breakpoints:
                if bp > start_pos:
                    next_bp = bp
                    break
            if next_bp:
                seg_text = text[start_pos:next_bp]
                seg_s = t0 + start_pos / n * (t1 - t0)
                seg_e = t0 + next_bp / n * (t1 - t0)
                segments.append({"start": seg_s, "end": seg_e, "text": seg_text})
                start_pos = next_bp
            else:
                seg_text = text[start_pos:]
                seg_s = t0 + start_pos / n * (t1 - t0)
                segments.append({"start": seg_s, "end": t1, "text": seg_text})
                break

    return segments


def _split_long_entries(entries: List[Dict]) -> List[Dict]:
    """对所有长句进行标点拆分，短句保持不动。"""
    result: List[Dict] = []
    split_count = 0
    for e in entries:
        parts = _split_long_entry(e)
        if len(parts) > 1:
            split_count += 1
        result.extend(parts)
    if split_count:
        logger.info(f"长句拆分: {len(entries)} 条中 {split_count} 条被拆 → 共 {len(result)} 条")
    return result


def _segment_by_words(
    utterances: List[Dict],
    target_chars: int = 14,
) -> List[Dict]:
    """
    ASR utterance → 字幕条目。
    火山引擎返回的 utterance 是自然句子，只需对长句按标点拆分。
    返回 [{start, end, text}, ...]（时间单位: 秒）。
    """
    raw_entries: List[Dict] = []
    for utt in utterances:
        utt_text = utt.get("text", "").strip()
        if not utt_text:
            continue
        raw_entries.append({
            "start": utt.get("start_time", 0) / 1000.0,
            "end": utt.get("end_time", 0) / 1000.0,
            "text": utt_text,
        })

    if not raw_entries:
        return []

    return _split_long_entries(raw_entries)


# ─────────────────── 文本纠错 ───────────────────

def _remove_filler_words(text: str) -> str:
    """去除语气词 + 清理标点（降级路径用）"""
    if not text:
        return text
    cleaned = "".join(ch for ch in text if ch not in FILLER_CHARS)
    cleaned = re.sub(r'[，。、；：！？,.]{2,}', lambda m: m.group()[-1], cleaned)
    cleaned = re.sub(r'^[，。、；：！？,.\s]+', '', cleaned)
    cleaned = re.sub(r'[，。、；：！？,.]+$', '', cleaned)
    return cleaned.strip()


def _rule_correct_entries(entries: List[Dict]) -> List[Dict]:
    """规则纠错 + 词典替换，过滤空条目（降级路径用）"""
    result = []
    for e in entries:
        corrected_text = _remove_filler_words(e["text"])
        if not corrected_text:
            continue
        corrected_text = _apply_dict_replacements(corrected_text)
        result.append({**e, "text": corrected_text})
    return result


def _correct_entries_with_llm(entries: List[Dict]) -> List[Dict]:
    """LLM 批量纠正同音错别字（每批 40 条）"""
    if not entries:
        return entries

    # 通过环境变量控制是否启用 LLM 纠错
    import os
    if os.getenv("SUBTITLE_LLM_CORRECTION", "true").lower() != "true":
        logger.info("SUBTITLE_LLM_CORRECTION=false，跳过LLM纠错")
        return entries

    try:
        from backend.core.llm_manager import get_llm_manager
        manager = get_llm_manager()
    except Exception as e:
        logger.warning(f"无法获取LLM管理器，跳过AI纠错: {e}")
        return entries

    BATCH_SIZE = 40
    corrected = [dict(e) for e in entries]

    for batch_start in range(0, len(entries), BATCH_SIZE):
        batch = entries[batch_start:batch_start + BATCH_SIZE]
        sub_lines = "\n".join(f"[{i}] {e['text']}" for i, e in enumerate(batch))

        vocab_hint = ""
        if _VOCAB:
            sample = _VOCAB[:60]
            vocab_hint = f"\n\n领域专业词汇（请确保这些词汇拼写正确）：\n{', '.join(sample)}"

        prompt = f"""你是中文ASR字幕纠错专家。通读以下字幕，修复同音错别字，不改变核心含义，不删除内容。{vocab_hint}

字幕（[序号] 文本）：
{sub_lines}

规则：
1. 修复ASR常见同音错别字（如"的话"误识为"地话"，"在一起"误识为"再一起"等）
2. 专有名词、缩写参照上方词汇表保持正确拼写
3. 不添加、不删除字幕内容

只输出需要纠正的条目，JSON数组格式：
[{{"id": 序号整数, "corrected_text": "修正后文字"}}]
如无需纠正输出：[]
只输出JSON数组，不要任何其他文字。"""

        try:
            response = manager.call_with_retry(prompt, max_retries=2)
            match = re.search(r'\[.*?\]', response, re.DOTALL)
            if not match:
                continue
            corrections = json.loads(match.group())
            for c in corrections:
                if not isinstance(c, dict):
                    continue
                local_idx = c.get("id")
                new_text = c.get("corrected_text", "").strip()
                if local_idx is None or not new_text:
                    continue
                try:
                    global_idx = int(local_idx) + batch_start
                    if 0 <= global_idx < len(corrected):
                        corrected[global_idx]["text"] = new_text
                        logger.debug(f"LLM纠错 [{global_idx}]: '{entries[global_idx]['text']}' → '{new_text}'")
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            logger.warning(f"LLM纠错批次({batch_start})失败: {e}")

    return corrected


# ─────────────────── 视频尺寸 / 字幕样式 ───────────────────

def _get_video_dimensions(video_path: Path) -> Tuple[int, int]:
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(video_path),
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=10)
        if r.returncode == 0:
            parts = r.stdout.decode("utf-8", errors="replace").strip().split(",")
            if len(parts) >= 2:
                w, h = int(parts[0]), int(parts[1])
                if w > 0 and h > 0:
                    return w, h
    except Exception as e:
        logger.debug(f"ffprobe获取尺寸失败: {e}")
    return 1920, 1080


def _subtitle_style(width: int, height: int) -> Dict:
    is_portrait = height > width
    margin_h = max(20, round(min(width, height) * 0.04))
    if is_portrait:
        font_size = max(24, min(52, round(width * 0.04)))
        margin_v = max(20, round(height * 0.025))
    else:
        font_size = max(32, min(72, round(height * 0.04)))
        margin_v = max(24, round(height * 0.025))
    # 固定每行最大中文字符数（避免像素计算偏差）
    max_chars = 18 if is_portrait else 28
    return {
        "font_size": font_size,
        "margin_v": margin_v,
        "max_chars": max_chars,
        "play_res_x": width,
        "play_res_y": height,
        "is_portrait": is_portrait,
    }


# ─────────────────── 字幕换行 / SRT 生成 ───────────────────

def _smart_wrap(text: str, max_chars: int) -> str:
    """长行在标点处循环断句；无合适标点则不断（保持单行）。
    支持一行拆成多行（如句子中有2~3个标点）。
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text

    break_chars = set("，。！？、；,;!? ")

    # 收集所有标点断点位置（断在标点后面）
    breakpoints = []
    for i, ch in enumerate(text):
        if ch in break_chars and 0 < i < len(text) - 1:
            breakpoints.append(i + 1)

    if not breakpoints:
        return text

    # 贪心分行：从头扫描，每行尽量接近 max_chars，在最近的标点处断
    lines = []
    start = 0
    while start < len(text):
        remaining = text[start:]
        if len(remaining) <= max_chars:
            lines.append(remaining.strip())
            break

        # 找 [start, start+max_chars] 范围内最后一个断点
        best = -1
        for bp in breakpoints:
            if bp <= start:
                continue
            if bp - start <= max_chars:
                best = bp
            else:
                break

        if best > start:
            lines.append(text[start:best].strip())
            start = best
        else:
            # 范围内无断点，保留到下一个断点或全部剩余
            next_bp = None
            for bp in breakpoints:
                if bp > start:
                    next_bp = bp
                    break
            if next_bp:
                lines.append(text[start:next_bp].strip())
                start = next_bp
            else:
                lines.append(remaining.strip())
                break

    return "\n".join(line for line in lines if line)


def _entries_to_srt_wrapped(entries: List[Dict], max_chars: int) -> str:
    lines = []
    for i, e in enumerate(entries, 1):
        wrapped_text = _smart_wrap(e["text"], max_chars)
        lines.append(str(i))
        lines.append(f"{_seconds_to_srt_time(e['start'])} --> {_seconds_to_srt_time(e['end'])}")
        lines.append(wrapped_text)
        lines.append("")
    return "\n".join(lines)


# ─────────────────── FFmpeg 字幕烧录 ───────────────────

def _get_video_duration(video_path: Path) -> Optional[float]:
    """用 ffprobe 获取视频时长"""
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", str(video_path),
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=10)
        if r.returncode == 0:
            stdout = r.stdout.decode("utf-8", errors="replace")
            data = json.loads(stdout)
            duration = float(data["format"]["duration"])
            logger.debug(f"视频时长: {duration:.2f}s ({video_path.name})")
            return duration
        else:
            logger.warning(f"ffprobe 获取时长失败, returncode={r.returncode}")
    except Exception as e:
        logger.warning(f"获取视频时长异常: {e}")
    return None


def _burn_subtitles(
    video_path: Path,
    srt_content: str,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    preset_id: str = "classic",
    style_overrides: Optional[Dict] = None,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
) -> bool:
    style = _subtitle_style(width, height)
    # 应用 font_size_ratio（从 style_overrides 中提取并移除，不传给 preset）
    font_size = style['font_size']
    if style_overrides and 'font_size_ratio' in style_overrides:
        ratio = style_overrides.pop('font_size_ratio')
        font_size = max(16, round(font_size * ratio))
    try:
        from backend.core.subtitle_presets import SUBTITLE_PRESETS, _hex_to_ass, _hex_alpha_to_ass
        preset = dict(SUBTITLE_PRESETS.get(preset_id, SUBTITLE_PRESETS["classic"]))
        if style_overrides:
            for k, v in style_overrides.items():
                if v is not None:
                    preset[k] = v
        parts = [f"Bold={preset.get('bold', 1)}"]
        parts.append(f"PrimaryColour={_hex_to_ass(preset['color'])}")
        outline_color = preset.get('outline_color') or '#000000'
        parts.append(f"OutlineColour={_hex_to_ass(outline_color)}")
        back_color = preset.get('back_color')
        if back_color:
            parts.append(f"BackColour={_hex_alpha_to_ass(back_color)}")
            parts.append("BorderStyle=4")
        else:
            parts.append("BackColour=&H00000000")
            parts.append("BorderStyle=1")
        parts.append(f"Outline={preset.get('outline', 2)}")
        parts.append(f"Shadow={preset.get('shadow', 1)}")
        force_style = (
            f"FontSize={font_size},"
            f"MarginV={style['margin_v']},"
            f"PlayResX={style['play_res_x']},"
            f"PlayResY={style['play_res_y']},"
            "WrapStyle=1,"
            + ",".join(parts)
        )
    except Exception:
        force_style = (
            f"FontSize={font_size},"
            f"MarginV={style['margin_v']},"
            f"PlayResX={style['play_res_x']},"
            f"PlayResY={style['play_res_y']},"
            "WrapStyle=1,"
            "Bold=1,"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "BackColour=&H00000000,"
            "Outline=1,"
            "Shadow=1"
        )
    logger.info(
        f"字幕样式: {'竖屏' if style['is_portrait'] else '横屏'} "
        f"{width}×{height}, FontSize={font_size}, "
        f"max_chars={style['max_chars']}, "
        f"preset={preset_id}, overrides={style_overrides}"
    )
    logger.info(f"force_style: {force_style}")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_video = Path(tmpdir) / "video.mp4"
            tmp_srt = Path(tmpdir) / "subtitle.srt"
            tmp_out = Path(tmpdir) / "output.mp4"
            shutil.copy2(str(video_path), str(tmp_video))
            tmp_srt.write_text(srt_content, encoding="utf-8")
            subtitle_filter = f"subtitles='{tmp_srt.name}':force_style='{force_style}'"
            cmd = ["ffmpeg", "-y"]
            # 开头裁剪：-ss 放在 -i 前面（输入级 seek）
            if trim_start is not None and trim_start > 0:
                cmd.extend(["-ss", str(trim_start)])
            cmd.extend(["-i", str(tmp_video)])
            cmd.extend(["-vf", subtitle_filter])
            cmd.extend(["-c:a", "copy"])
            # 结尾裁剪：-t 指定输出时长
            if trim_end is not None:
                if trim_start and trim_start > 0:
                    duration = trim_end - trim_start
                else:
                    duration = trim_end
                cmd.extend(["-t", str(duration)])
            cmd.append(str(tmp_out))
            logger.info(f"烧录命令 trim_start={trim_start} trim_end={trim_end}: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, timeout=300, cwd=tmpdir)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")[-600:]
                if "libass" in stderr.lower() or "no such filter" in stderr.lower():
                    logger.warning("FFmpeg 不支持 libass，跳过字幕烧录")
                else:
                    logger.error(f"字幕烧录失败: {stderr}")
                return False
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(tmp_out), str(output_path))
            logger.info(f"字幕烧录成功: {output_path.name}")
            return True
    except subprocess.TimeoutExpired:
        logger.error("字幕烧录超时")
        return False
    except Exception as e:
        logger.error(f"字幕烧录异常: {e}")
        return False


# ─────────────────── 合并 + 标点断句（降级路径 fallback） ───────────────────

def _reboundary_simple(
    entries: List[Dict],
    gap_threshold: float = 0.3,
    **_kwargs,
) -> List[Dict]:
    """合并相邻短条目，再按标点重新断句（降级路径 fallback）"""
    if not entries:
        return entries

    # 先合并间隔小的相邻条目（保留自然句子完整性）
    merged: List[Dict] = []
    i = 0
    while i < len(entries):
        e = dict(entries[i])
        while i + 1 < len(entries):
            gap = entries[i + 1]["start"] - e["end"]
            if gap < gap_threshold:
                i += 1
                e["end"] = entries[i]["end"]
                e["text"] = e["text"] + entries[i]["text"]
            else:
                break
        merged.append(e)
        i += 1

    # 对长句按标点拆分
    return _split_long_entries(merged)


# ─────────────────── step7 JSON 工具 ───────────────────

def _load_step7_json(path: Path) -> Dict[str, Any]:
    """加载已有的 step7_subtitle.json"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"加载 step7_subtitle.json 失败，将重新开始: {e}")
    return {"status": "processing", "clips": [], "errors": []}


def _save_step7_json(path: Path, data: Dict[str, Any]) -> None:
    """保存 step7_subtitle.json"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────── 主入口 ───────────────────

class SubtitleBurnGenerator:
    def __init__(
        self,
        clips_metadata_path: Path,
        input_srt_path: Optional[Path],
        output_dir: Path,
        metadata_dir: Path,
        asr_utterances_path: Optional[Path] = None,
    ):
        self.clips_metadata_path = clips_metadata_path
        self.input_srt_path = input_srt_path
        self.output_dir = output_dir
        self.metadata_dir = metadata_dir
        self.asr_utterances_path = asr_utterances_path or (metadata_dir / "asr_raw_utterances.json")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_clip_video_path(self, clip: Dict) -> Optional[Path]:
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
        """主处理流程：优先 utterances 模式，降级到 SRT 模式"""
        if not self.clips_metadata_path.exists():
            logger.warning(f"clips_metadata.json 不存在: {self.clips_metadata_path}")
            return {"status": "skipped", "reason": "clips_metadata.json not found"}

        with open(self.clips_metadata_path, "r", encoding="utf-8") as f:
            clips = json.load(f)

        if not clips:
            return {"status": "skipped", "reason": "no clips"}

        # step7 JSON 断点续传
        step7_json_path = self.metadata_dir / "step7_subtitle.json"
        step7_data = _load_step7_json(step7_json_path)
        done_ids = {c["clip_id"] for c in step7_data["clips"]}
        if done_ids:
            logger.info(f"step7 断点续传: 已有 {len(done_ids)} 个 clip 记录，将跳过")

        # 优先使用 ASR utterances 模式
        if self.asr_utterances_path.exists():
            logger.info(f"使用 ASR utterances 模式: {self.asr_utterances_path}")
            results = self._process_with_utterances(clips, step7_data, done_ids, step7_json_path)
        else:
            logger.info("asr_raw_utterances.json 不存在，降级到全局 SRT 模式")
            results = self._process_with_srt(clips, step7_data, done_ids, step7_json_path)

        # 更新 metadata
        with open(self.clips_metadata_path, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=2)

        # 标记完成
        step7_data["status"] = "done"
        _save_step7_json(step7_json_path, step7_data)

        logger.info(
            f"Step 7 完成: {len(results['subtitles'])} 个字幕, "
            f"{len(results['errors'])} 个错误"
        )
        results["status"] = "done"
        return results

    # ─────────────── ASR Utterances 模式（新流程） ───────────────

    def _process_with_utterances(
        self,
        clips: List[Dict],
        step7_data: Dict[str, Any],
        done_ids: set,
        step7_json_path: Path,
    ) -> Dict[str, Any]:
        """基于 asr_raw_utterances.json 的新流程"""
        with open(self.asr_utterances_path, "r", encoding="utf-8") as f:
            all_utterances = json.load(f)

        logger.info(f"已加载 {len(all_utterances)} 条原始 utterances")

        results: Dict[str, Any] = {"subtitles": [], "errors": []}

        for clip in clips:
            clip_id = str(clip.get("id", "unknown"))
            title = clip.get("generated_title", clip.get("title", f"clip_{clip_id}"))
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", str(title))

            # 断点续传：跳过已处理的 clip
            if clip_id in done_ids:
                logger.info(f"clip {clip_id} 已有字幕记录，跳过")
                continue

            video_path = self._get_clip_video_path(clip)
            if not video_path:
                logger.warning(f"找不到 clip {clip_id} 的视频文件，跳过")
                error_info = {"clip_id": clip_id, "error": "video not found"}
                results["errors"].append(error_info)
                step7_data["errors"].append(error_info)
                _save_step7_json(step7_json_path, step7_data)
                continue

            try:
                # clip 时间范围（毫秒）
                start_ms = int(_parse_time_value(clip.get("start_time", 0)) * 1000)
                end_ms = int(_parse_time_value(clip.get("end_time", 0)) * 1000)

                # 1. 提取 clip 范围内的 utterances
                clip_utts = _extract_clip_utterances(all_utterances, start_ms, end_ms)
                if not clip_utts:
                    logger.info(f"clip {clip_id} 无 utterances，跳过")
                    continue

                # 2. 语气词过滤
                clip_utts = [u for u in clip_utts if not _is_filler_utterance(u)]
                if not clip_utts:
                    logger.info(f"clip {clip_id} 过滤语气词后为空，跳过")
                    continue

                # 3. 词典替换
                for utt in clip_utts:
                    utt["text"] = _apply_dict_replacements(utt.get("text", ""))
                    for w in utt.get("words", []):
                        w["text"] = _apply_dict_replacements(w.get("text", ""))

                # 4. 按 word 时间戳断句
                clip_entries = _segment_by_words(clip_utts)
                if not clip_entries:
                    logger.info(f"clip {clip_id} 断句后无有效字幕，跳过")
                    continue

                # 5. 去除句中语气词 + 清理标点 + 过滤纯语气词条目
                cleaned = []
                for e in clip_entries:
                    t = _strip_filler_phrases(e['text'])
                    # 去除句中夹杂的语气字符（如"呃移民局" → "移民局"）
                    t = "".join(ch for ch in t if ch not in INLINE_FILLER_CHARS)
                    t = re.sub(r'^[，。、；：！？,.…\s]+', '', t)
                    t = re.sub(r'[，。、；：！？,.…]+$', '', t).strip()
                    if not t:
                        continue
                    cleaned.append({**e, 'text': t})
                clip_entries = cleaned
                if not clip_entries:
                    continue

                # 6. LLM 纠错
                clip_entries = _correct_entries_with_llm(clip_entries)

                # 7. 生成 SRT
                vid_w, vid_h = _get_video_dimensions(video_path)
                style = _subtitle_style(vid_w, vid_h)
                srt_content = _entries_to_srt_wrapped(clip_entries, style["max_chars"])

                # 8. 保存 SRT
                srt_dir = self.metadata_dir / "srt"
                srt_dir.mkdir(parents=True, exist_ok=True)
                srt_file = srt_dir / f"{safe_name}.srt"
                srt_file.write_text(srt_content, encoding="utf-8")
                clip["srt_path"] = str(srt_file)
                results["subtitles"].append(str(srt_file))
                logger.info(f"[Utterances] SRT已保存: {srt_file.name} ({len(clip_entries)} 条字幕)")

                # 9. 写入 step7 JSON（每个 clip 完成后立即写入）
                step7_data["clips"].append({
                    "clip_id": clip_id,
                    "srt_path": str(srt_file),
                    "entries_count": len(clip_entries),
                    "mode": "utterances",
                })
                _save_step7_json(step7_json_path, step7_data)

            except Exception as e:
                logger.error(f"[Utterances] clip {clip_id} 处理失败: {e}")
                error_info = {"clip_id": clip_id, "error": str(e)}
                results["errors"].append(error_info)
                step7_data["errors"].append(error_info)
                _save_step7_json(step7_json_path, step7_data)

        return results

    # ─────────────── SRT 降级模式 ───────────────

    def _process_with_srt(
        self,
        clips: List[Dict],
        step7_data: Dict[str, Any],
        done_ids: set,
        step7_json_path: Path,
    ) -> Dict[str, Any]:
        """降级模式：从全局 SRT 提取 → 简单合并 → 纠错 → SRT"""
        all_srt_entries: List[Dict] = []
        if self.input_srt_path and self.input_srt_path.exists():
            all_srt_entries = _parse_srt(self.input_srt_path)
            logger.info(f"已解析全局SRT，共 {len(all_srt_entries)} 条")
        else:
            logger.warning("无全局SRT文件，跳过字幕处理")
            return {"subtitles": [], "errors": [], "status": "skipped", "reason": "no srt file"}

        results: Dict[str, Any] = {"subtitles": [], "errors": []}

        for clip in clips:
            clip_id = str(clip.get("id", "unknown"))
            title = clip.get("generated_title", clip.get("title", f"clip_{clip_id}"))
            start = _parse_time_value(clip.get("start_time", 0))
            end = _parse_time_value(clip.get("end_time", 0))
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", str(title))

            # 断点续传：跳过已处理的 clip
            if clip_id in done_ids:
                logger.info(f"clip {clip_id} 已有字幕记录，跳过")
                continue

            video_path = self._get_clip_video_path(clip)
            if not video_path:
                logger.warning(f"找不到 clip {clip_id} 的视频文件，跳过")
                error_info = {"clip_id": clip_id, "error": "video not found"}
                results["errors"].append(error_info)
                step7_data["errors"].append(error_info)
                _save_step7_json(step7_json_path, step7_data)
                continue

            clip_entries = _extract_clip_entries(all_srt_entries, start, end)
            if not clip_entries:
                logger.info(f"clip {clip_id} 无字幕条目，跳过")
                continue

            clip_entries = _rule_correct_entries(clip_entries)
            if not clip_entries:
                continue

            clip_entries = _reboundary_simple(clip_entries, gap_threshold=0.6)

            cleaned = []
            for e in clip_entries:
                t = _strip_filler_phrases(e['text'])
                t = "".join(ch for ch in t if ch not in INLINE_FILLER_CHARS)
                t = re.sub(r'^[，。、；：！？,.…\s]+', '', t)
                t = re.sub(r'[，。、；：！？,.…]+$', '', t).strip()
                if not t:
                    continue
                cleaned.append({**e, 'text': t})
            clip_entries = cleaned
            if not clip_entries:
                continue

            clip_entries = _correct_entries_with_llm(clip_entries)

            vid_w, vid_h = _get_video_dimensions(video_path)
            style = _subtitle_style(vid_w, vid_h)
            srt_content = _entries_to_srt_wrapped(clip_entries, style["max_chars"])

            srt_dir = self.metadata_dir / "srt"
            srt_dir.mkdir(parents=True, exist_ok=True)
            srt_file = srt_dir / f"{safe_name}.srt"
            srt_file.write_text(srt_content, encoding="utf-8")
            clip["srt_path"] = str(srt_file)
            results["subtitles"].append(str(srt_file))
            logger.info(f"[SRT] SRT已保存: {srt_file.name}")

            # 写入 step7 JSON
            step7_data["clips"].append({
                "clip_id": clip_id,
                "srt_path": str(srt_file),
                "entries_count": len(clip_entries),
                "mode": "srt_fallback",
            })
            _save_step7_json(step7_json_path, step7_data)

        return results


def run_step7_subtitle_burn(
    clips_metadata_path: Path,
    input_srt_path: Optional[Path],
    output_dir: Path,
    metadata_dir: Path,
    asr_utterances_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Step 7 入口：字幕生成 + 纠错"""
    gen = SubtitleBurnGenerator(
        clips_metadata_path=clips_metadata_path,
        input_srt_path=input_srt_path,
        output_dir=output_dir,
        metadata_dir=metadata_dir,
        asr_utterances_path=asr_utterances_path,
    )
    return gen.process()


def run_step7_srt_only(
    clips_metadata_path: Path,
    input_srt_path: Optional[Path],
    output_dir: Path,
    metadata_dir: Path,
    asr_utterances_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Step 7 入口（兼容旧调用）"""
    gen = SubtitleBurnGenerator(
        clips_metadata_path=clips_metadata_path,
        input_srt_path=input_srt_path,
        output_dir=output_dir,
        metadata_dir=metadata_dir,
        asr_utterances_path=asr_utterances_path,
    )
    return gen.process()
