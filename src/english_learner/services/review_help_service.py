"""复习求助：用户点“忘记 / 模糊”时，AI 结合原因给出针对性记忆帮助。

与复习调度分离：
- `ReviewService` 只负责 SM-2 与落库；
- 本服务只负责“查原因 → 请 AI 给建议”，失败也不影响复习流程
  （用户可以跳过 AI，评分照常记录）。

Java 对照：≈ 一个 `VocabularyCoachService`，调用 LLM 并把新的记忆方法
回写到 `words.mnemonic`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..models.entities import Word
from ..storage.repositories import WordRepo
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .json_utils import JsonParseError, as_text, parse_json_object
from .prompts import PROMPT_VERSION, build_review_help_messages

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewHelpResult:
    """一次复习求助的结果。"""

    advice: str
    mnemonic: str = ""
    reason: str = ""


class ReviewHelpService:
    def __init__(self, client: TextCompleter, word_repo: WordRepo) -> None:
        self._client = client
        self._words = word_repo

    def help(
        self,
        word: Word,
        rating: int,
        reason: str = "",
        level: str = "B1",
    ) -> ReviewHelpResult:
        """根据“为什么没记住”生成具体建议；有新的记忆方法时顺手落库。"""
        if rating not in (1, 2):
            raise ValueError("只有“忘记 / 模糊”才需要复习帮助")
        if word.id is None:
            raise ValueError("help() 需要已落库的词条")

        messages = build_review_help_messages(word, rating, reason, level)
        logger.info("复习求助 prompt=%s word=%s rating=%s", PROMPT_VERSION, word.lemma, rating)
        text = self._client.complete_json(
            messages, temperature=0.5, max_tokens=1200, task="复习求助"
        )
        try:
            data = parse_json_object(text)
        except JsonParseError as exc:
            raise AIError(
                "记忆建议无法解析",
                "模型可能没有按 JSON 格式返回，可以重试或直接跳过。",
            ) from exc

        advice = as_text(data.get("advice"))
        if not advice:
            raise AIError("AI 没有给出记忆建议", "可以重试，或直接跳过继续复习。")

        mnemonic = as_text(data.get("mnemonic"))
        if mnemonic:
            self._words.update_mnemonic(word.id, mnemonic[:500])
        else:
            mnemonic = word.mnemonic

        return ReviewHelpResult(
            advice=advice[:800],
            mnemonic=mnemonic,
            reason=reason.strip()[:300],
        )
