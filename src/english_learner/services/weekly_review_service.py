"""每周复盘（Phase 3）：汇总一周数据，AI 给建议，用户确认后才改等级。

设计：
- 同一周只生成一次，结果落 `weekly_reviews`，重复打开不消耗 token；
- AI 只给 `suggested_level` 与文字建议，**不直接写 profile**；
- 用户点「应用建议」后，才把等级写回画像，并记录 `applied_level / applied_at`；
- 纯本地读取 + 一次 AI 调用，同步阻塞，必须放在 TaskWorker 里。

Java 对照：≈ 一个 `WeeklyReviewService`，两个用例：
`getOrGenerate`（读缓存 / 调 AI / 落库）与 `applySuggestion`（用户确认后改画像）。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, timedelta

from ..models.entities import (
    CEFR_LEVELS,
    Profile,
    WeeklyReview,
    normalize_cefr_level,
    now_iso,
)
from ..storage.repositories import (
    LevelEvidenceRepo,
    ProfileRepo,
    QuizRepo,
    ReviewRepo,
    StatsRepo,
    WeeklyReviewRepo,
    WordRepo,
)
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .json_utils import JsonParseError, as_float, as_text, parse_json_object
from .prompts import PROMPT_VERSION, build_weekly_review_messages
from .quiz_calibration import difficulty_stats, skill_stats
from .reading_quiz_service import history_entries

logger = logging.getLogger(__name__)

# 复盘建议的置信度最低值：AI 给得太低时只展示文字，不主动建议改级。
MIN_SUGGESTION_CONFIDENCE = 0.6


class WeeklyReviewService:
    """每周复盘：缓存优先，AI 只建议，用户确认才改画像。"""

    def __init__(
        self,
        client: TextCompleter,
        profile_repo: ProfileRepo,
        quiz_repo: QuizRepo,
        review_repo: ReviewRepo,
        stats_repo: StatsRepo,
        weekly_repo: WeeklyReviewRepo,
        word_repo: WordRepo | None = None,
        evidence_repo: LevelEvidenceRepo | None = None,
    ) -> None:
        self._client = client
        self._profiles = profile_repo
        self._quizzes = quiz_repo
        self._reviews = review_repo
        self._stats = stats_repo
        self._weekly = weekly_repo
        self._words = word_repo
        self._evidence = evidence_repo

    # ------------------------------------------------------------------ 周
    @staticmethod
    def week_start(on: date | None = None) -> str:
        """本周一（ISO 日期）。"""
        day = on or date.today()
        return (day - timedelta(days=day.weekday())).isoformat()

    def latest(self) -> WeeklyReview | None:
        reviews = self._weekly.list_recent(limit=1)
        return reviews[0] if reviews else None

    def _previous_focus(self, week_start: str) -> tuple[str, ...]:
        """上一周复盘的“下周重点”，用于让本次复盘交代计划完成度。

        只取严格早于本周的记录：本周记录如果已存在会命中缓存，
        不会走到生成流程；这里再过滤一次，避免“未来周”或重复记录干扰。
        """
        for review in self._weekly.list_recent(limit=4):
            if review.week_start < week_start:
                return tuple(item for item in review.focus if item.strip())
        return ()

    # -------------------------------------------------------------- 生成 / 缓存
    def get_or_generate(
        self,
        profile: Profile,
        *,
        on: date | None = None,
    ) -> WeeklyReview:
        """读取本周缓存；没有就汇总数据并调用 AI 生成一次。"""
        day = on or date.today()
        start = self.week_start(day)
        cached = self._weekly.get_by_week(start)
        if cached is not None:
            logger.info("命中每周复盘缓存：%s", start)
            return cached

        end = min(day, date.fromisoformat(start) + timedelta(days=6)).isoformat()
        recent = self._quizzes.list_recent_completed_with_titles(limit=20)
        weekly_quizzes = [
            (quiz, title)
            for quiz, title in recent
            if start <= quiz.article_date <= end and not quiz.practice_mode
        ]
        quizzes = [quiz for quiz, _ in weekly_quizzes]
        stats = self._stats.summarize_between(start, end)
        band_stats = self._reviews.list_band_stats(start, end)
        review_count = self._reviews.count_between(start, end)

        if (
            not quizzes
            and not review_count
            and not stats.get("words")
            and not stats.get("articles")
        ):
            raise AIError(
                "本周还没有足够的学习数据",
                "先读一篇文章、标几个生词或复习几个词，再来做每周复盘。",
                retryable=False,
                kind="data",
            )

        messages = build_weekly_review_messages(
            profile,
            week_start=start,
            week_end=end,
            quizzes=history_entries(weekly_quizzes),
            difficulty_stats=difficulty_stats(quizzes),
            skill_stats=skill_stats(quizzes),
            band_review_stats=band_stats,
            words_added=stats.get("words", 0),
            reviews_done=review_count,
            articles_read=stats.get("articles", 0),
            previous_focus=self._previous_focus(start),
        )
        logger.info(
            "每周复盘 prompt=%s week=%s quizzes=%s reviews=%s",
            PROMPT_VERSION,
            start,
            len(quizzes),
            review_count,
        )
        text = self._client.complete_json(
            messages, temperature=0.3, max_tokens=1500, task="每周复盘"
        )
        try:
            data = parse_json_object(text)
        except JsonParseError as exc:
            logger.warning("每周复盘 JSON 解析失败：%s", exc)
            raise AIError(
                "AI 复盘结果无法解析",
                "模型没有按 JSON 格式返回，可以重试一次。",
                retryable=True,
                kind="format",
            ) from exc

        summary = as_text(data.get("summary"))
        if not summary:
            raise AIError(
                "AI 没有返回复盘总结",
                "可以重试一次，或换一个更稳定的模型。",
                retryable=True,
                kind="format",
            )

        raw_focus = data.get("focus")
        focus = tuple(
            as_text(item)[:60]
            for item in (raw_focus if isinstance(raw_focus, list) else [])
            if as_text(item)
        )[:4]
        confidence = max(0.0, min(1.0, as_float(data.get("confidence"), 0.0)))
        suggested = self._clamp_suggestion(
            profile.cefr_level, data.get("suggested_level"), confidence
        )

        review = WeeklyReview(
            week_start=start,
            summary=summary[:800],
            suggested_level=suggested,
            focus=focus,
            quiz_count=len(quizzes),
            review_count=review_count,
            words_added=stats.get("words", 0),
            confidence=confidence,
            created_at=now_iso(),
        )
        stored = self._weekly.save(review)
        logger.info(
            "每周复盘已生成：week=%s 建议=%s 置信度=%.2f",
            start,
            suggested or "保持",
            confidence,
        )
        return stored

    # -------------------------------------------------------------- 应用建议
    def apply_suggestion(self, review: WeeklyReview, profile: Profile) -> Profile:
        """用户确认后写回等级；幂等，已应用过直接返回当前画像。"""
        if review.applied:
            return profile
        suggested = normalize_cefr_level(review.suggested_level)
        if not suggested:
            self._weekly.mark_applied(review.week_start, profile.cefr_level)
            return profile
        if suggested == profile.cefr_level:
            self._weekly.mark_applied(review.week_start, suggested)
            return profile

        updated = replace(
            profile,
            cefr_level=suggested,
            pending_level="",
            pending_count=0,
            level_confidence=float(review.confidence),
            level_updated_reason=f"每周复盘确认：{review.week_start} 起调整为 {suggested}",
            updated_at=now_iso(),
        )
        stored = self._profiles.save(updated)
        self._weekly.mark_applied(review.week_start, suggested)
        logger.info("每周复盘建议已应用：%s → %s", profile.cefr_level, suggested)
        return stored

    @staticmethod
    def _clamp_suggestion(
        current: str,
        suggested: object,
        confidence: float,
    ) -> str:
        """非法等级 / 置信度太低 → 保持当前；合法时最多跳一档。"""
        current_level = normalize_cefr_level(current, "B1")
        target = normalize_cefr_level(suggested)
        if not target or confidence < MIN_SUGGESTION_CONFIDENCE:
            return current_level
        current_index = CEFR_LEVELS.index(current_level)
        target_index = CEFR_LEVELS.index(target)
        window = max(
            0,
            min(len(CEFR_LEVELS) - 1, current_index + (1 if target_index > current_index else -1)),
        )
        if abs(target_index - current_index) <= 1:
            return target
        return CEFR_LEVELS[window]


__all__ = ["MIN_SUGGESTION_CONFIDENCE", "WeeklyReviewService"]
