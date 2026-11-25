"""真实文章自动供给（M7.1 / M7.2）：硬门槛、间隔、候选打分、今日决策。

设计：
- **决策本身 0 次 AI、0 次网络**：只读 SQLite + 本地词表算生词率；
- 门槛 G0~G10 与间隔表照抄 `docs/english_learner/M7_REAL_TEXT.md` 第 5 节，
  阈值集中在本文件顶部，方便测试与以后调整；
- 只负责“今天该不该供给、供给哪篇”，写入由 `ArticleService.serve_real_text`
  完成；这样失败回滚、题目种子、历史记录仍由文章服务统一处理。

Java 对照：≈ 一个无状态的 `SupplyPolicy` + 只读查询的 `DecisionService`；
真实写库交给 ArticleService / Repository。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, timedelta

from ..models.entities import CEFR_LEVELS, Profile, RealText, RealTextHistory, today_str
from ..storage.repositories import ArticleRepo, QuizRepo, RealTextRepo, StatsRepo, WordRepo
from .config_store import RealTextConfig
from .readability import KNOWN_INTERVAL_DAYS, ReadabilityMetrics, analyze, default_wordlist

logger = logging.getLogger(__name__)

# G3：近 30 天至少 7 个活跃日。
ACTIVE_DAYS_WINDOW = 30
ACTIVE_DAYS_REQUIRED = 7

# G5：近 3 次正式检测均分下限。
QUIZ_AVG_MIN = 0.6
QUIZ_AVG_SAMPLE = 3

# G6：生成文章生词率均值上限（只看最近 3 篇有可信指标的文章）。
GENERATED_RATIO_MAX = 0.08
GENERATED_RATIO_SAMPLE = 3

# G7：连续 7 天没活动，回来头 2 天不供给。
WELCOME_BACK_GAP_DAYS = 7
WELCOME_BACK_PROTECT_DAYS = 2

# G8 / G4：等级变动（升或降）后 7 天内不供给。
LEVEL_CHANGE_GUARD_DAYS = 7

# G10：候选预估生词率窗口。
CANDIDATE_RATIO_MIN = 0.03
CANDIDATE_RATIO_MAX = 0.07
# 打分时“最合适”的生词率目标值（5%）。
RATIO_TARGET = 0.05
# 距上次真实文章多少天算“完全新鲜”。
FRESHNESS_DAYS = 14

# 反馈间隔表：outcome → (最小间隔天数, 等级窗口下偏移, 等级窗口上偏移)。
# struggle / abandoned 只允许当前档及下一档，避免一次降两级。
OUTCOME_SPACING: dict[str, tuple[int, int, int]] = {
    "success": (3, -1, 1),
    "neutral": (7, -1, 1),
    "struggle": (14, -1, 0),
    # 注意：DB 里 expire_abandoned 实际写的是 outcome='abandon'（不是 abandoned）。
    "abandon": (10, -1, 0),
    "skip": (7, -1, 1),
    "": (7, -1, 1),
}
DEFAULT_SPACING = OUTCOME_SPACING[""]


@dataclass(frozen=True)
class RealTextDecision:
    """一次今日决策的结果；skip 时 `text` 为 None。"""

    action: str
    reason: str
    text: RealText | None = None
    score: float = 0.0
    unknown_ratio: float = 0.0
    message: str = ""

    @property
    def should_serve(self) -> bool:
        return self.action == "serve" and self.text is not None

    @classmethod
    def skip(cls, reason: str, message: str = "") -> RealTextDecision:
        return cls(action="skip", reason=reason, message=message)

    @classmethod
    def serve(
        cls,
        text: RealText,
        *,
        score: float,
        unknown_ratio: float,
        message: str = "",
    ) -> RealTextDecision:
        return cls(
            action="serve",
            reason="serve",
            text=text,
            score=round(float(score), 4),
            unknown_ratio=round(float(unknown_ratio), 4),
            message=message,
        )


def spacing_from_outcome(outcome: str) -> tuple[int, int, int]:
    """返回 (最小间隔天数, 等级下偏移, 等级上偏移)。

    兼容旧写法 `abandoned`：数据库与 `real_text_feedback` 统一使用 `abandon`，
    但文档表格里写的是 abandoned，读旧导出或手写配置时不能掉回默认间隔。
    """
    key = (outcome or "").strip().lower()
    if key == "abandoned":
        key = "abandon"
    return OUTCOME_SPACING.get(key, DEFAULT_SPACING)


def level_window(current: str, low_offset: int, high_offset: int) -> tuple[str, ...]:
    """按当前档位取允许的候选等级（自动夹在 A2~C1 内）。"""
    level = (current or "").strip().upper()
    if level not in CEFR_LEVELS:
        return CEFR_LEVELS
    index = CEFR_LEVELS.index(level)
    low = max(0, index + int(low_offset))
    high = min(len(CEFR_LEVELS) - 1, index + int(high_offset))
    if high < low:
        high = low
    return CEFR_LEVELS[low : high + 1]


def _parse_day(value: str | None) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _quiz_ratio(quiz) -> float | None:
    total = int(getattr(quiz, "total", 0) or 0)
    if total <= 0:
        return None
    return float(quiz.score) / total


def _metrics_ratio(article) -> float | None:
    """从文章 metrics 取可信生词率；词表缺失或没读过正文时返回 None。"""
    metrics = ReadabilityMetrics.from_dict(getattr(article, "metrics", None))
    if metrics is None or metrics.wordlist_size <= 0 or metrics.word_total <= 0:
        return None
    return metrics.unknown_ratio


class RealTextSupplyService:
    """真实文章供给决策；构造后每次 `decide()` 都是一次只读快照计算。"""

    def __init__(
        self,
        article_repo: ArticleRepo,
        stats_repo: StatsRepo,
        quiz_repo: QuizRepo,
        real_text_repo: RealTextRepo,
        word_repo: WordRepo | None = None,
        *,
        wordlist_provider=default_wordlist,
        today=today_str,
    ) -> None:
        self._articles = article_repo
        self._stats = stats_repo
        self._quizzes = quiz_repo
        self._real_texts = real_text_repo
        self._words = word_repo
        self._wordlist_provider = wordlist_provider
        self._today = today

    # ------------------------------------------------------------------ 入口
    def decide(
        self,
        profile: Profile | None,
        config: RealTextConfig,
        *,
        today: str | None = None,
    ) -> RealTextDecision:
        """按 G0~G10 顺序判断今天是否供给真实文章。"""
        day = today or self._today()
        if not config.auto_enabled:
            return RealTextDecision.skip("disabled")
        if self._articles.get_by_date(day) is not None:
            return RealTextDecision.skip("article_exists")
        if (config.last_decision_on or "").strip() == day:
            return RealTextDecision.skip("already_decided")
        if profile is None:
            return RealTextDecision.skip("no_profile")

        active_days = self._active_days(day, ACTIVE_DAYS_WINDOW)
        if len(active_days) < ACTIVE_DAYS_REQUIRED:
            return RealTextDecision.skip("new_user")

        recent_quizzes = self._quizzes.list_recent_formal_completed(limit=8)
        today_date = _parse_day(day) or date.today()
        if self._has_level_drop(recent_quizzes, today_date):
            return RealTextDecision.skip("downgraded_recently")

        quiz_avg = self._average_quiz_score(recent_quizzes)
        if quiz_avg is not None and quiz_avg < QUIZ_AVG_MIN:
            return RealTextDecision.skip("struggling")

        generated_ratio = self._mean_generated_ratio()
        if generated_ratio is not None and generated_ratio > GENERATED_RATIO_MAX:
            return RealTextDecision.skip("too_hard")

        if self._is_welcome_back(day):
            return RealTextDecision.skip("welcome_back")

        if self._has_level_change(recent_quizzes, today_date):
            return RealTextDecision.skip("level_changing")

        history = self._real_texts.last_history()
        spacing, low_offset, high_offset = spacing_from_outcome(
            history.outcome if history is not None else ""
        )
        days_since_last = self._days_since_history(history, today_date)
        if history is not None and days_since_last < spacing:
            return RealTextDecision.skip("too_soon")

        allowed_levels = level_window(profile.cefr_level, low_offset, high_offset)
        unread = self._real_texts.list_unread()
        if not unread:
            return RealTextDecision.skip("no_library")
        candidates = [text for text in unread if text.level in allowed_levels]
        if not candidates:
            return RealTextDecision.skip("no_candidate")

        scored: list[tuple[RealText, float, float]] = []
        for text in candidates:
            ratio = self.estimate_unknown_ratio(text)
            if not (CANDIDATE_RATIO_MIN <= ratio <= CANDIDATE_RATIO_MAX):
                continue
            score = self._score_candidate(
                text,
                ratio,
                days_since_last=days_since_last,
                quiz_avg=quiz_avg if quiz_avg is not None else 0.7,
                last_topics=self._last_topics(history),
            )
            scored.append((text, ratio, score))
        if not scored:
            return RealTextDecision.skip("no_candidate")

        # 同分用 hash(text_id + today) 确定性打散，避免每天固定选同一篇。
        best = max(
            scored,
            key=lambda item: (round(item[2], 6), _tiebreak(item[0].id, day)),
        )
        text, ratio, score = best
        message = f"为你安排了真实文章《{text.title}》（约 {text.level}，预计生词 {ratio:.0%}）。"
        return RealTextDecision.serve(text, score=score, unknown_ratio=ratio, message=message)

    # -------------------------------------------------------------- 指标计算
    def estimate_unknown_ratio(self, text: RealText) -> float:
        """用现有可读性分析按“用户认识的词”估算生词率；失败按 0 处理。"""
        try:
            known = (
                self._words.list_known_lemmas(KNOWN_INTERVAL_DAYS)
                if self._words is not None
                else ()
            )
            metrics = analyze(
                text.content,
                self._wordlist_provider(),
                known_words=known,
            )
            return float(metrics.unknown_ratio)
        except Exception:  # noqa: BLE001 - 估算失败不能阻断供给决策
            logger.warning("估算真实文章生词率失败：%s", text.id, exc_info=True)
            return 0.0

    def _active_days(self, day: str, window: int) -> set[str]:
        start = (_parse_day(day) or date.today()) - timedelta(days=window - 1)
        return {
            row.stat_date
            for row in self._stats.list_all()
            if row.is_active and (_parse_day(row.stat_date) or start) >= start
        }

    def _average_quiz_score(self, quizzes: list) -> float | None:
        ratios = [
            ratio for quiz in quizzes[:QUIZ_AVG_SAMPLE] if (ratio := _quiz_ratio(quiz)) is not None
        ]
        if not ratios:
            return None
        return sum(ratios) / len(ratios)

    def _mean_generated_ratio(self) -> float | None:
        ratios: list[float] = []
        for article in self._articles.list_recent(limit=30):
            if (article.source or "generated") != "generated":
                continue
            ratio = _metrics_ratio(article)
            if ratio is not None:
                ratios.append(ratio)
            if len(ratios) >= GENERATED_RATIO_SAMPLE:
                break
        if not ratios:
            return None
        return sum(ratios) / len(ratios)

    def _has_level_drop(self, quizzes: list, today: date) -> bool:
        cutoff = today - timedelta(days=LEVEL_CHANGE_GUARD_DAYS)
        for quiz in quizzes:
            completed = _parse_day(quiz.completed_at)
            if completed is None or completed < cutoff:
                continue
            previous = str(quiz.previous_level or "").strip().upper()
            result = str(quiz.result_level or "").strip().upper()
            if previous in CEFR_LEVELS and result in CEFR_LEVELS:
                if CEFR_LEVELS.index(result) < CEFR_LEVELS.index(previous):
                    return True
        return False

    def _has_level_change(self, quizzes: list, today: date) -> bool:
        cutoff = today - timedelta(days=LEVEL_CHANGE_GUARD_DAYS)
        for quiz in quizzes:
            completed = _parse_day(quiz.completed_at)
            if completed is None or completed < cutoff:
                continue
            previous = str(quiz.previous_level or "").strip().upper()
            result = str(quiz.result_level or "").strip().upper()
            if previous and result and previous != result:
                return True
        return False

    def _is_welcome_back(self, day: str) -> bool:
        """连续 7 天没活动后，回来头 2 天不供给。

        - 今天就是“回来第一天”（上次活动 ≥7 天前）→ 不供给；
        - 昨天是“回来第一天”（前天之前 ≥7 天没活动）→ 今天仍算头 2 天，不供给。
        """
        today_date = _parse_day(day) or date.today()
        active = sorted(
            {_parse_day(row.stat_date) for row in self._stats.list_all() if row.is_active} - {None}
        )
        past = [item for item in active if item < today_date]
        if not past:
            return False
        last = past[-1]
        gap = (today_date - last).days
        if gap >= WELCOME_BACK_GAP_DAYS:
            return True
        if gap <= WELCOME_BACK_PROTECT_DAYS - 1 and len(past) >= 2:
            previous_gap = (last - past[-2]).days
            if previous_gap >= WELCOME_BACK_GAP_DAYS:
                return True
        return False

    def _days_since_history(self, history: RealTextHistory | None, today: date) -> int:
        if history is None:
            return FRESHNESS_DAYS
        served = _parse_day(history.served_on)
        if served is None:
            return FRESHNESS_DAYS
        return max(0, (today - served).days)

    def _last_topics(self, history: RealTextHistory | None) -> set[str]:
        if history is None:
            return set()
        text = self._real_texts.get(history.text_id)
        return set(text.topics) if text is not None else set()

    def _score_candidate(
        self,
        text: RealText,
        ratio: float,
        *,
        days_since_last: int,
        quiz_avg: float,
        last_topics: set[str],
    ) -> float:
        """候选打分（M7_REAL_TEXT.md 5.4）。"""
        freshness = min(days_since_last / FRESHNESS_DAYS, 1.0)
        ratio_fit = max(0.0, 1 - abs(ratio - RATIO_TARGET) / RATIO_TARGET)
        topic_bonus = 0.0 if (last_topics and set(text.topics) & last_topics) else 1.0
        return 0.4 * freshness + 0.3 * quiz_avg + 0.2 * ratio_fit + 0.1 * topic_bonus


def _tiebreak(text_id: str, day: str) -> str:
    """确定性打散：同分时按 hash(text_id + today) 排序。"""
    return hashlib.sha1(f"{text_id}:{day}".encode()).hexdigest()


__all__ = [
    "ACTIVE_DAYS_REQUIRED",
    "CANDIDATE_RATIO_MAX",
    "CANDIDATE_RATIO_MIN",
    "DEFAULT_SPACING",
    "GENERATED_RATIO_MAX",
    "LEVEL_CHANGE_GUARD_DAYS",
    "OUTCOME_SPACING",
    "QUIZ_AVG_MIN",
    "RealTextDecision",
    "RealTextSupplyService",
    "level_window",
    "spacing_from_outcome",
]
