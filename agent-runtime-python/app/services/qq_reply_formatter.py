from __future__ import annotations

import hashlib
import re


class QqReplyFormatter:
    """把模型或审查 Agent 的最终文本统一整理为适合 QQ 私聊的短气泡。"""

    _STAGE_DIRECTION_CUES = (
        "挠头",
        "歪头",
        "摇头",
        "点头",
        "抬头",
        "低头",
        "叹气",
        "苦笑",
        "轻笑",
        "偷笑",
        "坏笑",
        "笑",
        "眨眼",
        "挥手",
        "摊手",
        "耸肩",
        "扶额",
        "捂脸",
        "摸头",
        "挑眉",
        "皱眉",
        "脸红",
        "害羞",
        "沉思",
        "思考",
        "沉默",
        "停顿",
        "顿了顿",
        "小声",
        "轻声",
        "压低声音",
        "语气",
        "看着",
        "拍桌",
        "撇嘴",
        "抿嘴",
        "撒娇",
        "卖萌",
        "正襟危坐",
        "内心",
    )

    _REACTION_QUESTION_CUES = (
        "哪个",
        "哪部",
        "哪里",
        "哪来的",
        "谁做",
        "什么",
        "怎么",
        "为啥",
        "为什么",
        "出处",
        "作者",
        "模组",
        "二创",
    )

    _REACTION_ASSISTANT_CUES = (
        "收到",
        "知道了",
        "我记下了",
        "我先确认",
    )

    def format(
        self,
        reply_text: str,
        event_id: str,
        max_reply_chars: int = 16,
        split_long_reply: bool = True,
        split_reply_chance_percent: int = 33,
        max_bubbles: int = 2,
        main_console_mode: bool = False,
    ) -> list[str]:
        """按调用链路清理 QQ 回复；主控台与设定集使用彼此独立的分段策略。"""
        terminal_punctuation = self._resolve_rare_terminal_punctuation(
            reply_text,
            event_id,
            48 if main_console_mode else max_reply_chars,
        )
        cleaned_text = self._clean_chat_text(reply_text)
        if not cleaned_text:
            return ["嗯"]

        if not main_console_mode:
            # 设定集继续尊重用户保存的长度、拆分概率和单条发送选项，避免主控台改造改变旧会话行为。
            return self._format_profile_reply(
                cleaned_text,
                event_id,
                max_reply_chars=max_reply_chars,
                split_long_reply=split_long_reply,
                split_reply_chance_percent=split_reply_chance_percent,
                max_bubbles=max_bubbles,
                terminal_punctuation=terminal_punctuation,
            )

        # 主控台委托任务不把发送消息建模为气泡工具，也不使用固定字符数切割文本。
        # 模型明确输出换行时才连续发送多条，否则始终保留为一条完整 QQ 消息。
        explicit_parts = [part.strip() for part in cleaned_text.split("\n") if part.strip()]
        # 主控台由模型显式换行表达“连续发几条”，不受设定集的拆分开关影响。
        # 该开关只保留给旧的设定集链路，避免主控台把多条自然回复压回一条。
        if len(explicit_parts) > 1:
            parts = [self._trim_chat_bubble(part, 0) for part in explicit_parts]
            return [part for part in parts if part] or ["嗯"]

        single_part = "，".join(explicit_parts) if explicit_parts else cleaned_text
        return self._apply_terminal_punctuation(
            [self._trim_chat_bubble(single_part, 0)],
            terminal_punctuation,
        )

    def _format_profile_reply(
        self,
        cleaned_text: str,
        event_id: str,
        *,
        max_reply_chars: int,
        split_long_reply: bool,
        split_reply_chance_percent: int,
        max_bubbles: int,
        terminal_punctuation: str,
    ) -> list[str]:
        """恢复设定集原有回复形状，使旧会话配置仍能约束长度和分段节奏。"""
        limit = min(max(int(max_reply_chars or 16), 4), 120)
        explicit_parts = [part.strip() for part in cleaned_text.split("\n") if part.strip()]
        normalized = "，".join(explicit_parts) if explicit_parts else cleaned_text

        if not split_long_reply:
            return self._apply_terminal_punctuation(
                [self._legacy_trim_chat_bubble(normalized, limit)],
                terminal_punctuation,
            )

        should_split = (
            len(explicit_parts) > 1
            or len(normalized) > limit
            or self.should_split_reply(event_id, split_reply_chance_percent)
        )
        if should_split:
            parts = self._split_to_chat_bubbles(normalized, limit, max(1, int(max_bubbles or 2)))
            return self._apply_terminal_punctuation(parts, terminal_punctuation)

        # 旧设定在不拆分时会收紧口语停顿，保持为一个简短气泡。
        single_part = normalized.replace("，", "")
        return self._apply_terminal_punctuation(
            [self._legacy_trim_chat_bubble(single_part, limit)],
            terminal_punctuation,
        )

    def format_reaction(self, reply_text: str, event_id: str, media_evidence: str = "") -> list[str]:
        """把纯表情包回复收敛为一个轻量气泡，禁止借图片主动追问或开启新话题。"""
        cleaned_text = self._clean_chat_text(reply_text)
        candidates = [
            part.strip()
            for part in re.split(r"[\n，。；;！？?!]+", cleaned_text)
            if part.strip()
        ]
        for candidate in candidates:
            if self._is_safe_reaction(candidate):
                return [self._trim_chat_bubble(candidate, 10)]
        return [self._fallback_reaction(media_evidence)]

    @staticmethod
    def should_keep_terminal_punctuation(event_id: str, chance_percent: int = 2) -> bool:
        """按事件 ID 稳定抽样句末标点，使重试同一事件时保持相同的聊天样式。"""
        normalized_chance = min(max(int(chance_percent), 0), 100)
        if normalized_chance == 0:
            return False
        digest = hashlib.sha256(f"terminal:{event_id}".encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % 100 < normalized_chance

    @staticmethod
    def should_split_reply(event_id: str, chance_percent: int = 33) -> bool:
        """按事件 ID 稳定决定是否把带停顿的短回复拆成多条，避免真随机导致重试行为不一致。"""
        normalized_chance = min(max(int(chance_percent), 0), 100)
        if normalized_chance == 0:
            return False
        if normalized_chance == 100:
            return True
        digest = hashlib.sha256(f"split:{event_id}".encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % 100 < normalized_chance

    def _resolve_rare_terminal_punctuation(self, reply_text: str, event_id: str, limit: int) -> str:
        """仅极低概率保留短回复原有的句末标点，长回复和分段回复始终使用自然气泡样式。"""
        normalized = " ".join(str(reply_text or "").split()).strip()
        if len(normalized) > limit or not normalized:
            return ""
        punctuation = normalized[-1]
        if punctuation not in "。！？!?":
            return ""
        return punctuation if self.should_keep_terminal_punctuation(event_id) else ""

    @staticmethod
    def _apply_terminal_punctuation(parts: list[str], punctuation: str) -> list[str]:
        """只给单条短消息恢复抽样命中的原始句末标点，不向分段文本额外添加符号。"""
        if punctuation and len(parts) == 1 and parts[0]:
            parts[-1] = f"{parts[-1]}{punctuation}"
        return parts

    @classmethod
    def _clean_chat_text(cls, reply_text: str) -> str:
        """移除 Markdown 和书面化标点，并把语义停顿保留为后续分段候选。"""
        text = str(reply_text or "").strip()
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"^[\s>*#\-•\d.]+", "", text, flags=re.MULTILINE)
        text = text.replace("**", "").replace("__", "").replace("`", "")
        text = cls._remove_stage_directions(text)
        # 非动作括号中的事实内容继续保留，但所有圆括号本身都不能进入 QQ 回复。
        text = re.sub(r"[（）()]", "", text)
        text = re.sub(r"[【】\[\]{}]", "", text)
        text = re.sub(r"[~～]+", "", text)
        # 连续句点和省略号都表示一次自然停顿，不能让字符上限把后面的完整短语从中间切开。
        text = re.sub(r"(?:\.{2,}|…{1,})", "，", text)
        text = re.sub(r"[。！？!?；;]+", "，", text)
        text = re.sub(r"[，、]+", "，", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip(" \n\"'“”。，、；;！？?!~～")

    @classmethod
    def _remove_stage_directions(cls, text: str) -> str:
        """删除圆括号中的动作、神态和语气说明；普通事实括号只去括号并保留内容。"""
        pattern = re.compile(r"[（(]([^（）()\n]{1,40})[）)]")

        def replace_parenthesized(match: re.Match[str]) -> str:
            # 这个内部函数的作用是区分舞台动作和普通补充事实，避免粗暴删除价格、日期等有效内容。
            content = " ".join(match.group(1).split()).strip()
            normalized = content.lower()
            if any(cue in normalized for cue in cls._STAGE_DIRECTION_CUES):
                return ""
            return content

        previous = None
        while previous != text:
            previous = text
            text = pattern.sub(replace_parenthesized, text)
        return text

    @staticmethod
    def _trim_chat_bubble(text: str, limit: int) -> str:
        """去除气泡边缘的书面标点；保留完整语义，不再按照字符数截断。"""
        _ = limit
        return " ".join(text.split()).strip().strip("。，、；;！？?!")

    @staticmethod
    def _legacy_trim_chat_bubble(text: str, limit: int) -> str:
        """仅供设定集兼容模式使用，在用户配置的字符上限处收紧单条回复。"""
        normalized = " ".join(text.split()).strip().strip("。，、；;！？?!")
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip(" ，、；;。！？?!")

    def _split_to_chat_bubbles(self, text: str, limit: int, max_bubbles: int = 2) -> list[str]:
        """只在自然语义边界拆分，并为单轮自动回复设置气泡数量硬上限。"""
        remaining = " ".join(text.split()).strip()
        bubbles: list[str] = []
        while remaining and len(bubbles) < max_bubbles:
            if len(bubbles) == max_bubbles - 1:
                # 最后一条保留全部剩余语义，禁止为了气泡上限静默丢失回复后半段。
                bubbles.append(self._normalize_chat_bubble(remaining))
                break
            if len(remaining) <= limit and not re.search(r"[，。；;？?]", remaining):
                bubbles.append(self._normalize_chat_bubble(remaining))
                break

            split_at = self._find_natural_split_index(remaining, limit)
            if split_at is None:
                # 中文没有空格，按固定字符数硬切很容易得到“你加载 / 完了吗”。
                # 此时长度配置只作为目标值，完整表达优先于气泡长度。
                bubbles.append(self._normalize_chat_bubble(remaining))
                break

            bubble = self._normalize_chat_bubble(remaining[:split_at])
            if not bubble:
                break
            bubbles.append(bubble)
            remaining = remaining[split_at:].lstrip(" ，、；;。！？?!")
        return bubbles or ["嗯"]

    @staticmethod
    def _find_natural_split_index(text: str, limit: int) -> int | None:
        """在目标长度附近寻找标点或分句词；找不到时返回空值，禁止固定字符硬切。"""
        normalized = " ".join(text.split()).strip()
        # 允许向后多看少量字符，避免自然停顿刚好落在目标长度之后时被提前切断。
        search_end = min(len(normalized) - 1, limit + 8)
        if search_end <= 0:
            return None
        candidates = [match.start() + 1 for match in re.finditer(r"[，。；;？?]", normalized[:search_end + 1])]
        for phrase in ("但是", "然后", "所以", "要不", "那就", "你先", "我先", "还是", "如果"):
            start = normalized.find(phrase, 4, search_end + 1)
            if start > 0:
                candidates.append(start)

        suitable = [position for position in candidates if 2 <= position <= search_end]
        before_limit = [position for position in suitable if position <= limit]
        if before_limit:
            return max(before_limit)
        after_limit = [position for position in suitable if position > limit]
        return min(after_limit) if after_limit else None

    @staticmethod
    def _normalize_chat_bubble(text: str) -> str:
        """只清理单个气泡边缘，不再按字符数截掉已经选定的完整语义片段。"""
        return " ".join(text.split()).strip().strip(" 。，、；;！？?!")

    @classmethod
    def _is_safe_reaction(cls, candidate: str) -> bool:
        """判断候选是否只是轻回应，而不是客服确认、图片解说或主动开启的新问题。"""
        normalized = " ".join(str(candidate or "").split()).strip()
        if not normalized or len(normalized) > 10:
            return False
        if any(cue in normalized for cue in cls._REACTION_QUESTION_CUES):
            return False
        if any(cue in normalized for cue in cls._REACTION_ASSISTANT_CUES):
            return False
        return not normalized.endswith(("吗", "么", "呢", "呀"))

    @staticmethod
    def _fallback_reaction(media_evidence: str) -> str:
        """模型没有给出合格轻回应时，依据有限的视觉标签选择不扩展话题的保守回复。"""
        normalized = " ".join(str(media_evidence or "").split())
        if any(cue in normalized for cue in ("可爱", "萌", "猫", "狗", "搞笑", "好笑", "滑稽", "梗")):
            return "哈哈哈"
        if any(cue in normalized for cue in ("哭", "难过", "委屈", "悲伤")):
            return "抱抱"
        return "哈哈"
