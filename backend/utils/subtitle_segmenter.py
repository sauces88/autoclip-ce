"""
字幕智能断句服务

基于 video-ai-editor 项目迁移，使用 LLM 对转录文本进行语义断句。

核心要求：
1. 每条字幕 8-15 个字（最多 18 个）
2. 不需要标点符号
3. 保持词语完整（不切断词语）
4. 随说话节奏自然断开

AI 调用适配 autoclip 的 llm_manager.call_with_retry。
"""
import json
import logging
import re
from typing import List, Dict

logger = logging.getLogger(__name__)


class SubtitleSegmenter:
    """
    字幕智能断句器

    使用 LLM 进行语义分析，将长文本断成短句字幕。
    AI 失败时自动降级为规则断句。
    """

    def __init__(
        self,
        min_chars: int = 8,
        max_chars: int = 15,
        hard_max_chars: int = 18,
    ):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.hard_max_chars = hard_max_chars

    def segment(
        self,
        characters: List[Dict],
        batch_size: int = 500,
        use_ai: bool = True,
    ) -> List[Dict]:
        """
        对字符序列进行断句（同步版）。

        Args:
            characters: FunASR 字符列表 [{"char": "对", "start": 1.0, "end": 1.1}, ...]
            batch_size: 每批处理的字符数
            use_ai: 是否尝试 AI 断句（失败自动降级）

        Returns:
            字幕列表 [{"text": "对而且为什么", "start": 1.0, "end": 2.5}, ...]
        """
        if not characters:
            return []

        all_subtitles: List[Dict] = []
        ai_failed_count = 0
        total_batches = (len(characters) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            batch = characters[start : start + batch_size]
            batch_text = "".join(c["char"] for c in batch)

            # 连续 2 次 AI 失败就直接用规则
            if not use_ai or ai_failed_count >= 2:
                subtitles = self._fallback_segment(batch)
                all_subtitles.extend(subtitles)
                continue

            # 尝试 AI 断句
            break_indices = self._get_break_points_sync(batch_text)

            if break_indices:
                subtitles = self._create_subtitles_from_breaks(batch, break_indices)
                all_subtitles.extend(subtitles)
            else:
                subtitles = self._fallback_segment(batch)
                all_subtitles.extend(subtitles)
                ai_failed_count += 1

        logger.info(
            f"断句完成: {len(characters)} chars → {len(all_subtitles)} subtitles "
            f"(AI failed {ai_failed_count}/{total_batches} batches)"
        )
        return all_subtitles

    # ─────────────── AI 断句 ───────────────

    def _get_break_points_sync(self, text: str) -> List[int]:
        """
        用 LLM 获取断点位置（同步调用 autoclip 的 llm_manager）。

        Returns:
            断点索引列表 [15, 28, 43, ...]，空列表表示失败
        """
        if len(text) <= self.max_chars:
            return [len(text) - 1]

        try:
            from backend.core.llm_manager import get_llm_manager
            manager = get_llm_manager()
        except Exception as e:
            logger.warning(f"无法获取 LLM 管理器: {e}")
            return []

        prompt = f"""你是字幕断句专家。将以下连续中文文本断成短句字幕。

文本：
{text}

规则：
1. 每条字幕 {self.min_chars}-{self.max_chars} 个字（绝对不超过 {self.hard_max_chars} 个）
2. 在语义自然的位置断开（不要切断词语）
3. 不需要添加标点符号

输出格式：JSON 数组，每个元素是断点在原文中的字符索引（从 0 开始，表示该位置是一条字幕的最后一个字符）。
例如文本有 50 个字，断成 4 条：[12, 25, 38, 49]

只输出 JSON 数组，不要其他文字。"""

        try:
            response = manager.call_with_retry(prompt, max_retries=2)
            # 提取 JSON 数组
            match = re.search(r"\[[\d\s,]+\]", response)
            if not match:
                logger.warning(f"LLM 未返回有效 JSON: {response[:200]}")
                return []

            break_points = json.loads(match.group())
            if isinstance(break_points, list) and all(isinstance(x, int) for x in break_points):
                valid = [bp for bp in break_points if 0 < bp < len(text)]
                return sorted(valid)

            logger.warning(f"Invalid break points: {break_points}")
            return []

        except Exception as e:
            logger.warning(f"AI 断句失败: {e}")
            return []

    # ─────────────── 根据断点创建字幕 ───────────────

    def _create_subtitles_from_breaks(
        self, characters: List[Dict], break_indices: List[int]
    ) -> List[Dict]:
        """根据断点索引和字符时间戳创建字幕条目"""
        subtitles: List[Dict] = []
        start_idx = 0

        # 确保末尾有断点
        if not break_indices or break_indices[-1] < len(characters) - 1:
            break_indices = list(break_indices) + [len(characters) - 1]

        for end_idx in break_indices:
            if end_idx < start_idx:
                continue

            seg = characters[start_idx : end_idx + 1]
            if seg:
                subtitles.append({
                    "text": "".join(c["char"] for c in seg),
                    "start": seg[0]["start"],
                    "end": seg[-1]["end"],
                })
            start_idx = end_idx + 1

        return subtitles

    # ─────────────── 规则降级断句 ───────────────

    def _fallback_segment(self, characters: List[Dict]) -> List[Dict]:
        """
        规则断句降级方案（jieba 分词感知）。
        利用 jieba 分词保证词语完整性，避免切断 "CEO"、"因为"、"银行账户" 等词语。
        """
        if not characters:
            return []

        full_text = "".join(c["char"] for c in characters)

        try:
            import jieba
            jieba.setLogLevel(60)
            words = list(jieba.cut(full_text, cut_all=False))
        except ImportError:
            logger.warning("jieba 未安装，使用字符级断句")
            return self._fallback_segment_charwise(characters)

        subtitles: List[Dict] = []
        current_chars: List[Dict] = []
        cur_len = 0
        char_idx = 0

        for word in words:
            word_len = len(word)
            if char_idx + word_len > len(characters):
                break

            word_chars = characters[char_idx : char_idx + word_len]

            # 判断是否应在此词之前断开
            should_break = False
            if current_chars:
                if cur_len + word_len > self.hard_max_chars:
                    should_break = True
                elif cur_len >= self.min_chars and cur_len + word_len > self.max_chars:
                    should_break = True
                elif cur_len >= self.max_chars:
                    should_break = True

            if should_break and current_chars:
                subtitles.append({
                    "text": "".join(c["char"] for c in current_chars),
                    "start": current_chars[0]["start"],
                    "end": current_chars[-1]["end"],
                })
                current_chars = []
                cur_len = 0

            current_chars.extend(word_chars)
            cur_len += word_len
            char_idx += word_len

        if current_chars:
            subtitles.append({
                "text": "".join(c["char"] for c in current_chars),
                "start": current_chars[0]["start"],
                "end": current_chars[-1]["end"],
            })

        return subtitles

    def _fallback_segment_charwise(self, characters: List[Dict]) -> List[Dict]:
        """字符级断句（jieba 不可用时的最终降级）"""
        if not characters:
            return []

        subtitles: List[Dict] = []
        current_chars: List[Dict] = []

        break_before = {"但", "而", "所", "因", "如", "虽", "不", "然", "那", "这", "就", "也", "还", "又"}
        break_after = {"的", "了", "着", "过", "吗", "呢", "吧", "啊", "呀", "么", "嘛"}

        for i, char_data in enumerate(characters):
            char = char_data["char"]
            current_chars.append(char_data)
            cur_len = len(current_chars)
            should_break = False

            if cur_len >= self.hard_max_chars:
                should_break = True
            elif cur_len >= self.min_chars:
                if i + 1 < len(characters) and characters[i + 1]["char"] in break_before:
                    should_break = True
                if char in break_after:
                    should_break = True
                if cur_len >= self.max_chars:
                    should_break = True

            if should_break and current_chars:
                subtitles.append({
                    "text": "".join(c["char"] for c in current_chars),
                    "start": current_chars[0]["start"],
                    "end": current_chars[-1]["end"],
                })
                current_chars = []

        if current_chars:
            subtitles.append({
                "text": "".join(c["char"] for c in current_chars),
                "start": current_chars[0]["start"],
                "end": current_chars[-1]["end"],
            })

        return subtitles
