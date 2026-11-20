"""学习者记忆：把已有学习数据压成一份本地派生摘要，注入 AI prompt。

设计（AI_TASKS.md M5.2）：
- **纯本地派生**：不建表、不新增 AI 调用、不做“AI 压缩档案”，
  每次用到时从 SQLite 现算，数据改了摘要立刻跟着变；
- **只放统计结论**：等级、错因、反复忘记 / 拼错的词、回译写作错误、
  到期积压、上周计划，不放文章原文，也不回灌历史对话
  （与“不保存 prompt / 回复正文”的隐私约定一致）；
- **空数据返回空记忆**：调用方据此保持 prompt 与未注入时逐字节一致；
- 输出经 `prompts.build_learner_memory_block()` 按任务过滤并截断到约 200 token。

Java 对照：≈ 一个 `LearnerMemoryAssembler`，读多个 Mapper 后拼成一个不可变 DTO；
它自己不调用任何外部服务，可以放心在后台线程里执行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from ..models.entities import today_str
from ..storage.repositories import (
    LevelEvidenceRepo,
    ProfileRepo,
    ReviewRepo,
    WeeklyReviewRepo,
    WordRepo,
    WritingAttemptRepo,
)
from .srs import RATING_FORGOT, VERDICT_NEAR, VERDICT_WRONG

logger = logging.getLogger(__name__)

# 每个词表最多带几个词：多了会挤占 prompt，也超出 200 token 预算。
MEMORY_WORD_LIMIT = 5
# 错因只带 Top N。
MEMORY_ERROR_LIMIT = 2
# 最近多少天内的等级证据参与“近期错因”统计。
ERROR_LOOKBACK_DAYS = 7
# 至少被评“忘记”几次才算“反复忘记”，避免一次失误被当成长期弱点。
FORGOTTEN_MIN_COUNT = 2
# 这些值不是真正的错因，统计时丢掉（与 reading_quiz_service 的口径一致）。
_IGNORED_ERROR_TYPES = {"", "none", "unknown", "short_answer", "rewrite"}


@dataclass(frozen=True)
class LearnerMemory:
    """一份学习记忆快照；所有字段都是统计结论，不含原文。"""

    level: str = ""
    confidence: float = 0.0
    level_reason: str = ""
    # 测评画像里的薄弱点：刚测评完、还没有检测证据时，文章生成靠它保持个性化。
    weaknesses: tuple[str, ...] = ()
    error_types: tuple[str, ...] = ()
    forgotten_words: tuple[str, ...] = ()
    misspelled_words: tuple[str, ...] = ()
    # M5.4 回译批改里出现最多的写作错误类型（时态 / 搭配 / 冠词…）。
    writing_error_types: tuple[str, ...] = ()
    due_backlog: int = 0
    focus: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """没有任何可用信息：调用方应保持 prompt 与注入前完全一致。"""
        return not (
            self.level
            or self.weaknesses
            or self.error_types
            or self.forgotten_words
            or self.misspelled_words
            or self.writing_error_types
            or self.due_backlog
            or self.focus
        )


def load_memory(provider: Callable[[], LearnerMemory] | None) -> LearnerMemory:
    """安全地构建记忆：任何异常都退化为空记忆，不能影响主流程。"""
    if provider is None:
        return LearnerMemory()
    try:
        return provider()
    except Exception:  # noqa: BLE001 - 记忆只是增强信息，失败不应阻断 AI 调用
        logger.warning("构建学习者记忆失败，按空记忆处理", exc_info=True)
        return LearnerMemory()


class LearnerMemoryService:
    """从本地仓库汇总学习者记忆。所有方法只是只读 SQL，可离线运行。"""

    def __init__(
        self,
        profile_repo: ProfileRepo,
        review_repo: ReviewRepo,
        word_repo: WordRepo,
        weekly_repo: WeeklyReviewRepo,
        evidence_repo: LevelEvidenceRepo,
        writing_repo: WritingAttemptRepo | None = None,
        today: Callable[[], str] = today_str,
    ) -> None:
        self._profiles = profile_repo
        self._reviews = review_repo
        self._words = word_repo
        self._weekly = weekly_repo
        self._evidence = evidence_repo
        self._writing = writing_repo
        self._today = today

    def build(self, *, on: date | None = None) -> LearnerMemory:
        """汇总当前可用的学习记忆；没有任何数据时返回空记忆。"""
        day = on or self._parse_today()
        day_str = day.isoformat()
        week_start = (day - timedelta(days=day.weekday())).isoformat()

        profile = self._profiles.get()
        level = profile.cefr_level if profile is not None else ""
        confidence = profile.level_confidence if profile is not None else 0.0
        level_reason = profile.level_updated_reason.strip() if profile is not None else ""
        weaknesses = (
            tuple(item.strip() for item in profile.weaknesses if item and item.strip())
            if profile is not None
            else ()
        )

        return LearnerMemory(
            level=level,
            confidence=confidence,
            level_reason=level_reason,
            weaknesses=weaknesses,
            error_types=self._recent_error_types(day),
            forgotten_words=self._forgotten_words(),
            misspelled_words=self._misspelled_words(),
            writing_error_types=self._writing_error_types(day),
            due_backlog=self._words.count_due(day_str),
            focus=self._previous_focus(week_start),
        )

    # ------------------------------------------------------------------ 内部
    def _parse_today(self) -> date:
        try:
            return date.fromisoformat(self._today())
        except ValueError:
            return date.today()

    def _recent_error_types(self, day: date) -> tuple[str, ...]:
        """最近 7 天（含今天，即今天和之前 6 天）等级证据里的错因频次 Top2。

        同时排除未来日期：正常写入不会产生未来时间，但系统时钟回拨等
        极端情况下不应让“未来证据”长期占据近期错因。
        """
        start = (day - timedelta(days=ERROR_LOOKBACK_DAYS - 1)).isoformat()
        day_str = day.isoformat()
        counts: dict[str, int] = {}
        for evidence in self._evidence.list_recent(limit=12):
            observed = evidence.observed_at[:10]
            if not (start <= observed <= day_str):
                continue
            for item in evidence.error_types:
                key = item.strip()
                if key.lower() in _IGNORED_ERROR_TYPES:
                    continue
                counts[key] = counts.get(key, 0) + 1
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return tuple(key for key, _ in ranked[:MEMORY_ERROR_LIMIT])

    def _forgotten_words(self) -> tuple[str, ...]:
        rows = self._reviews.list_forgotten_words(
            limit=MEMORY_WORD_LIMIT,
            min_count=FORGOTTEN_MIN_COUNT,
            rating=RATING_FORGOT,
        )
        return tuple(lemma for lemma, _ in rows)

    def _misspelled_words(self) -> tuple[str, ...]:
        rows = self._reviews.list_misspelled_words(
            limit=MEMORY_WORD_LIMIT,
            verdicts=(VERDICT_WRONG, VERDICT_NEAR),
        )
        return tuple(lemma for lemma, _ in rows)

    def _writing_error_types(self, day: date) -> tuple[str, ...]:
        """最近 7 天回译批改里的错误类型频次 Top2。

        只统计已批改的记录；`verdict_json` 由 `SentenceGrade` 写入，
        这里对结构做防御性读取（旧数据 / 手工改库都不应让记忆构建失败）。
        """
        if self._writing is None:
            return ()
        start = (day - timedelta(days=ERROR_LOOKBACK_DAYS - 1)).isoformat()
        day_str = day.isoformat()
        counts: dict[str, int] = {}
        for attempt in self._writing.list_recent(limit=30):
            graded_day = (attempt.graded_at or "")[:10]
            if not (start <= graded_day <= day_str):
                continue
            verdict = attempt.verdict or {}
            items = verdict.get("error_types")
            if not isinstance(items, (list, tuple)):
                continue
            for item in items:
                key = str(item or "").strip()
                if not key or key.lower() in _IGNORED_ERROR_TYPES:
                    continue
                counts[key] = counts.get(key, 0) + 1
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return tuple(key for key, _ in ranked[:MEMORY_ERROR_LIMIT])

    def _previous_focus(self, week_start: str) -> tuple[str, ...]:
        """上一周复盘的“下周重点”；本周及更晚的记录不算。"""
        for review in self._weekly.list_recent(limit=4):
            if review.week_start < week_start:
                return tuple(item for item in review.focus if item.strip())
        return ()


__all__ = [
    "ERROR_LOOKBACK_DAYS",
    "FORGOTTEN_MIN_COUNT",
    "LearnerMemory",
    "LearnerMemoryService",
    "MEMORY_ERROR_LIMIT",
    "MEMORY_WORD_LIMIT",
    "load_memory",
]
