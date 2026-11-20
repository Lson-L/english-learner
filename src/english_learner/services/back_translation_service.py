"""回译练习编排：本地抽句 → AI 翻译出题 → 用户写英文 → AI 批量批改。

设计（AI_TASKS.md M5.4）：
- 题目按文章缓存：一篇文章只出一次题（`writing_attempts` 3 行），
  重复打开练习不重复请求 AI；
- 翻译与批改是两次独立调用，分别打 `回译出题` / `回译批改` 标签；
- 批改只给练习反馈，**不**写等级、不写 SRS，失败可以直接跳过；
- 本模块不 import PySide6，调用方必须把 AI 调用放进 TaskWorker。

Java 对照：≈ `BackTranslationService`，依赖一个 `TextCompleter` 与
`WritingAttemptRepo`，业务规则都在这里，界面只管输入输出。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ..models.entities import Article, WritingAttempt, now_iso
from ..storage.repositories import WritingAttemptRepo
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .back_translation import (
    SENTENCES_PER_ROUND,
    InvalidBackTranslationError,
    SentenceGrade,
    parse_grades,
    parse_translations,
    pick_sentences,
)
from .prompts import (
    PROMPT_VERSION,
    build_back_translation_grade_messages,
    build_back_translation_translate_messages,
)

logger = logging.getLogger(__name__)

TASK_TRANSLATE = "回译出题"
TASK_GRADE = "回译批改"

TRANSLATE_MAX_TOKENS = 1200
GRADE_MAX_TOKENS = 2400


class BackTranslationService:
    """一次练习的生命周期：出题（缓存）、批改（回写）。实例可复用。"""

    def __init__(
        self,
        client: TextCompleter,
        writing_repo: WritingAttemptRepo,
        *,
        sentences_per_round: int = SENTENCES_PER_ROUND,
    ) -> None:
        self._client = client
        self._writing = writing_repo
        self._limit = max(1, int(sentences_per_round))

    # ------------------------------------------------------------ 出题
    def get_cached(self, article_id: int) -> tuple[WritingAttempt, ...]:
        """读取某篇文章已生成的练习题（本地 SQL，不会请求 AI）。"""
        return tuple(self._writing.list_for_article(article_id))

    def load_or_create(self, article: Article) -> tuple[WritingAttempt, ...]:
        """有缓存直接返回；没有则本地抽句并翻译出题，落库后返回。"""
        if article.id is None:
            raise ValueError("文章还没有入库，不能生成回译练习。")

        cached = self.get_cached(article.id)
        if cached and all(item.reference_cn and item.sentence_en for item in cached):
            logger.info("命中回译练习缓存：article=%s", article.id)
            return cached

        sentences = pick_sentences(article.content, article.target_words, limit=self._limit)
        if not sentences:
            raise AIError(
                "这篇文章里没有适合做回译的句子",
                "文章太短或句子结构特殊，可以换一篇后再练。",
            )
        translations = self._translate(sentences, article.level)
        created_at = now_iso()
        attempts = [
            WritingAttempt(
                article_id=int(article.id),
                article_date=article.article_date,
                position=position,
                sentence_en=sentence,
                reference_cn=translation,
                created_at=created_at,
            )
            for position, (sentence, translation) in enumerate(
                zip(sentences, translations, strict=True)
            )
        ]
        stored = self._writing.replace_for_article(int(article.id), attempts)
        logger.info(
            "回译练习已生成：article=%s 句数=%s prompt=%s",
            article.id,
            len(stored),
            PROMPT_VERSION,
        )
        return tuple(stored)

    # ------------------------------------------------------------ 批改
    def grade(
        self,
        article: Article,
        answers: Sequence[str],
    ) -> tuple[WritingAttempt, ...]:
        """一次批量批改全部句子，并把答案 / 判定 / 得分回写数据库。"""
        if article.id is None:
            raise ValueError("文章还没有入库，不能提交回译练习。")

        attempts = self.get_cached(article.id)
        if not attempts:
            raise AIError("回译练习还没有出题", "请先重新打开练习并等待题目生成。")
        texts = [str(answer or "").strip() for answer in answers]
        if len(texts) != len(attempts):
            raise ValueError("答案数量与题目数量不一致。")
        if any(not text for text in texts):
            raise ValueError("请先用英文写出所有句子，再提交批改。")

        grades = self._grade(attempts, texts, article.level)
        graded_at = now_iso()
        rows = [
            (attempt.position, text, grade.to_dict(), grade.score, graded_at)
            for attempt, text, grade in zip(attempts, texts, grades, strict=True)
        ]
        stored = self._writing.save_grades(int(article.id), rows)
        logger.info(
            "回译练习已批改：article=%s 得分=%s/%s",
            article.id,
            sum(grade.score for grade in grades),
            len(grades) * 2,
        )
        return tuple(stored)

    # ------------------------------------------------------------ 内部
    def _translate(self, sentences: Sequence[str], level: str) -> tuple[str, ...]:
        last_reason = ""
        for attempt in range(2):
            messages = build_back_translation_translate_messages(
                sentences,
                level,
                repair_note=last_reason if attempt else "",
            )
            text = self._client.complete_json(
                messages,
                temperature=0.3,
                max_tokens=TRANSLATE_MAX_TOKENS,
                task=TASK_TRANSLATE,
            )
            try:
                return parse_translations(text, len(sentences))
            except InvalidBackTranslationError as exc:
                last_reason = str(exc)
                logger.warning("回译译文未通过校验（第 %s 次）：%s", attempt + 1, exc)
        raise AIError(
            "AI 返回的中文译文没有通过校验",
            f"最后一次原因：{last_reason}。可以重试，或稍后再练。",
        )

    def _grade(
        self,
        attempts: Sequence[WritingAttempt],
        answers: Sequence[str],
        level: str,
    ) -> tuple[SentenceGrade, ...]:
        items = [
            (attempt.sentence_en, attempt.reference_cn, answer)
            for attempt, answer in zip(attempts, answers, strict=True)
        ]
        last_reason = ""
        for attempt in range(2):
            messages = build_back_translation_grade_messages(
                items,
                level,
                repair_note=last_reason if attempt else "",
            )
            text = self._client.complete_json(
                messages,
                temperature=0.2,
                max_tokens=GRADE_MAX_TOKENS,
                task=TASK_GRADE,
            )
            try:
                return parse_grades(text, len(items))
            except InvalidBackTranslationError as exc:
                last_reason = str(exc)
                logger.warning("回译批改未通过校验（第 %s 次）：%s", attempt + 1, exc)
        raise AIError(
            "AI 返回的批改结果没有通过校验",
            f"最后一次原因：{last_reason}。答案还保留着，可以直接重试。",
        )


__all__ = [
    "BackTranslationService",
    "TASK_GRADE",
    "TASK_TRANSLATE",
]
