"""档内进度与难度提示（M6.4 / M6.5）。

为什么需要这个模块：
- 等级只有四档，用户升级慢时看不到「在进步」的证据；
- 文章生成只拿到 CEFR 标签，低 B1 和高 B1 会拿到同样的难度。

这里把已有数据（检测后验、题目难度、文章可读性、词档复习）折成两份纯本地
结论：`LevelProgress`（给今日页展示）与 `DifficultyHint`（给文章 prompt）。

约定：
- 纯函数：不碰数据库、不碰 PySide6、不发网络请求，方便单测与回放；
- 口径集中在模块常量与本文档（AI_TASKS.md 7.5），调用方不要各写一套；
- 数据缺失时返回空值 / `mid`，绝不猜。

Java 对照：≈ 一个无状态的 `LevelProgressCalculator`，
`LevelProgress` / `DifficultyHint` 是它的不可变返回值（record）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..models.entities import (
    CEFR_LEVELS,
    Article,
    BandReviewStat,
    Profile,
    ReadingQuiz,
    normalize_cefr_level,
)

# 生词比例趋势：与更早 3 天相比，±2 个百分点以内算持平。
TREND_THRESHOLD = 0.02
# 近 / 更早两个窗口的长度（天）。
ARTICLE_RECENT_DAYS = 7
ARTICLE_PREVIOUS_DAYS = 3
# 词档认识率至少复习这么多次才展示，避免 1 次记录就下结论。
BAND_MIN_REVIEWS = 4
# “高两档题”：难度比当前等级高两档及以上。
HIGH_BAND_STEP = 2
# 后验里上一档的把握达到这个值，就认为文章可以在档内偏难。
HIGH_HINT_NEXT_PROBABILITY = 0.45
# 文章指标可信的最小正文词数与词表规模（与 readability 的宽容规则一致）。
MIN_WORD_TOTAL = 40
MIN_WORDLIST_SIZE = 500
# 默认生词比例阈值；调用方通常传 ConfigStore 里的 readability 配置。
DEFAULT_UNKNOWN_RATIO = 0.07


def _level_index(level: str) -> int | None:
    try:
        return CEFR_LEVELS.index(level)
    except ValueError:
        return None


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _completed_key(quiz: ReadingQuiz) -> tuple[str, int]:
    """最近完成的排在前面；同一秒用 id 兜底（与仓库排序口径一致）。"""
    return (quiz.completed_at or "", int(quiz.id or 0))


def _latest_completed(quizzes: list[ReadingQuiz] | tuple[ReadingQuiz, ...]) -> ReadingQuiz | None:
    completed = [quiz for quiz in quizzes if quiz.completed]
    return max(completed, key=_completed_key) if completed else None


def _latest_formal(quizzes: list[ReadingQuiz] | tuple[ReadingQuiz, ...]) -> ReadingQuiz | None:
    formal = [quiz for quiz in quizzes if quiz.completed and not quiz.practice_mode]
    return max(formal, key=_completed_key) if formal else None


def _ratio_from_metrics(metrics: dict[str, object] | None) -> float | None:
    """从 `articles.metrics_json` 取可信的生词比例；不可信时返回 None。"""
    if not isinstance(metrics, dict):
        return None
    try:
        word_total = int(metrics.get("word_total") or 0)
        wordlist_size = int(metrics.get("wordlist_size") or 0)
        ratio = float(metrics.get("unknown_ratio"))
    except (TypeError, ValueError):
        return None
    if word_total < MIN_WORD_TOTAL or wordlist_size < MIN_WORDLIST_SIZE:
        return None
    return min(max(ratio, 0.0), 1.0)


@dataclass(frozen=True)
class QuizProgressSummary:
    """最近一次正式检测的题目级摘要（供结果页与今日页共用）。"""

    answered: int = 0
    correct: int = 0
    high_correct: int = 0
    # 按 CEFR 顺序排列的难度分布，如 (("B1", 3), ("B2", 2), ("C1", 1))。
    difficulty_counts: tuple[tuple[str, int], ...] = ()

    @property
    def has_data(self) -> bool:
        return self.answered > 0


@dataclass(frozen=True)
class ArticleRatioSummary:
    """近 7 天与更早 3 天的生词比例对比。"""

    recent_ratio: float
    previous_ratio: float | None
    recent_count: int
    direction: str  # "up" / "down" / "flat"

    @property
    def delta(self) -> float | None:
        if self.previous_ratio is None:
            return None
        return self.recent_ratio - self.previous_ratio


@dataclass(frozen=True)
class BandProgressSummary:
    """当前词档与下一词档的复习认识率。"""

    band: str
    accuracy: float
    next_band: str = ""
    next_accuracy: float | None = None


@dataclass(frozen=True)
class LevelProgress:
    """今日页的档内进度快照；所有字段都可为空数据下的默认值。"""

    current_level: str
    next_level: str = ""
    progress: float | None = None
    # "posterior"（四档后验）/ "pending"（旧数据连续证据）/ ""（无进度）。
    progress_source: str = ""
    confidence: float = 0.0
    pending_level: str = ""
    pending_count: int = 0
    quiz: QuizProgressSummary = QuizProgressSummary()
    article: ArticleRatioSummary | None = None
    band: BandProgressSummary | None = None
    practice_only: bool = False

    @property
    def has_data(self) -> bool:
        return self.progress is not None or self.quiz.has_data or self.article is not None

    @property
    def progress_percent(self) -> int | None:
        if self.progress is None:
            return None
        return max(0, min(100, round(self.progress * 100)))


@dataclass(frozen=True)
class DifficultyHint:
    """文章档内位置：low / mid / high + 一句中文理由。"""

    within_level: str = ""
    reason: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.within_level


def summarize_quiz(quiz: ReadingQuiz, current_level: str) -> QuizProgressSummary:
    """把一次检测折算成「答对 X/Y」与高难度题表现。

    - 选择题按作答下标本地判分；
    - 产出题按 AI 给出的 0/1 分计入；AI 漏评分的题既不算对，也不进分母；
    - 难度使用题目自带的 `difficulty`，缺失时回退到本卷等级。
    """
    current = normalize_cefr_level(current_level) or normalize_cefr_level(quiz.level, "B1")
    counts: dict[str, int] = {level: 0 for level in CEFR_LEVELS}
    for question in quiz.questions:
        difficulty = normalize_cefr_level(question.difficulty, quiz.level)
        if difficulty in counts:
            counts[difficulty] += 1

    current_index = _level_index(current)
    high_index = current_index + HIGH_BAND_STEP if current_index is not None else None

    answered = 0
    correct = 0
    high_correct = 0

    for position, question in enumerate(quiz.choice_questions):
        chosen = quiz.answers[position] if position < len(quiz.answers) else -1
        answered += 1
        if chosen == question.answer_index:
            correct += 1
            difficulty = normalize_cefr_level(question.difficulty, quiz.level)
            index = _level_index(difficulty)
            if high_index is not None and index is not None and index >= high_index:
                high_correct += 1

    productions = quiz.production_answers
    for position, question in enumerate(quiz.production_questions):
        production = productions[position] if position < len(productions) else None
        # AI 漏评分的产出题既不算答对，也不进“答对 X/Y”的分母，
        # 与 quiz_calibration 的“未评分不参与统计”口径保持一致。
        if production is None or not production.scored:
            continue
        score = production.score
        answered += 1
        if score >= 1:
            correct += 1
            difficulty = normalize_cefr_level(question.difficulty, quiz.level)
            index = _level_index(difficulty)
            if high_index is not None and index is not None and index >= high_index:
                high_correct += 1

    distribution = tuple((level, counts[level]) for level in CEFR_LEVELS if counts[level])
    return QuizProgressSummary(
        answered=answered,
        correct=correct,
        high_correct=high_correct,
        difficulty_counts=distribution,
    )


def _article_ratio_summary(
    articles: list[Article] | tuple[Article, ...],
    today: date,
) -> ArticleRatioSummary | None:
    recent_start = today - timedelta(days=ARTICLE_RECENT_DAYS - 1)
    previous_end = recent_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=ARTICLE_PREVIOUS_DAYS - 1)

    recent: list[float] = []
    previous: list[float] = []
    for article in articles:
        ratio = _ratio_from_metrics(article.metrics)
        if ratio is None:
            continue
        day = _parse_day(article.article_date)
        if day is None:
            continue
        if recent_start <= day <= today:
            recent.append(ratio)
        elif previous_start <= day <= previous_end:
            previous.append(ratio)

    if not recent:
        return None
    recent_ratio = sum(recent) / len(recent)
    previous_ratio = sum(previous) / len(previous) if previous else None
    if previous_ratio is None:
        direction = "flat"
    elif recent_ratio - previous_ratio > TREND_THRESHOLD:
        direction = "up"
    elif previous_ratio - recent_ratio > TREND_THRESHOLD:
        direction = "down"
    else:
        direction = "flat"
    return ArticleRatioSummary(
        recent_ratio=recent_ratio,
        previous_ratio=previous_ratio,
        recent_count=len(recent),
        direction=direction,
    )


def _band_summary(
    band_stats: list[BandReviewStat] | tuple[BandReviewStat, ...],
    current: str,
    next_level: str,
) -> BandProgressSummary | None:
    by_band = {stat.band: stat for stat in band_stats if stat.band}
    current_stat = by_band.get(current)
    if current_stat is None or current_stat.total < BAND_MIN_REVIEWS:
        return None
    next_accuracy = None
    if next_level:
        next_stat = by_band.get(next_level)
        if next_stat is not None and next_stat.total >= BAND_MIN_REVIEWS:
            next_accuracy = next_stat.accuracy
    return BandProgressSummary(
        band=current,
        accuracy=current_stat.accuracy,
        next_band=next_level,
        next_accuracy=next_accuracy,
    )


def _progress_value(
    profile: Profile,
    latest: ReadingQuiz | None,
    current: str,
    next_level: str,
) -> tuple[float | None, str]:
    if not next_level:
        return None, ""
    if latest is not None and len(latest.posterior) == len(CEFR_LEVELS):
        mapping = dict(zip(CEFR_LEVELS, latest.posterior, strict=True))
        current_probability = max(float(mapping.get(current, 0.0)), 0.0)
        next_probability = max(float(mapping.get(next_level, 0.0)), 0.0)
        total = current_probability + next_probability
        if total > 0:
            return max(0.0, min(1.0, next_probability / total)), "posterior"
    # 旧数据没有 posterior：退化成“连续证据进度”（只看指向下一档的待定）。
    if profile.pending_count > 0 and normalize_cefr_level(profile.pending_level) == next_level:
        return min(1.0, profile.pending_count / 2.0), "pending"
    return None, ""


def build_level_progress(
    profile: Profile | None,
    quizzes: list[ReadingQuiz] | tuple[ReadingQuiz, ...] = (),
    band_stats: list[BandReviewStat] | tuple[BandReviewStat, ...] = (),
    articles: list[Article] | tuple[Article, ...] = (),
    *,
    today: date | None = None,
) -> LevelProgress | None:
    """组装今日页的档内进度；没有画像时返回 None（未测评）。"""
    if profile is None:
        return None

    current = normalize_cefr_level(profile.cefr_level, "B1")
    current_index = CEFR_LEVELS.index(current)
    next_level = CEFR_LEVELS[current_index + 1] if current_index + 1 < len(CEFR_LEVELS) else ""

    latest = _latest_formal(quizzes)
    latest_any = _latest_completed(quizzes)
    summary = summarize_quiz(latest, current) if latest is not None else QuizProgressSummary()
    day = today or date.today()
    progress_value, progress_source = _progress_value(profile, latest, current, next_level)

    return LevelProgress(
        current_level=current,
        next_level=next_level,
        progress=progress_value,
        progress_source=progress_source,
        confidence=(latest.level_confidence if latest is not None else 0.0)
        or profile.level_confidence,
        pending_level=profile.pending_level,
        pending_count=profile.pending_count,
        quiz=summary,
        article=_article_ratio_summary(articles, day),
        band=_band_summary(band_stats, current, next_level),
        practice_only=latest_any is not None and latest_any.practice_mode,
    )


def difficulty_hint(
    profile: Profile | None,
    quizzes: list[ReadingQuiz] | tuple[ReadingQuiz, ...] = (),
    articles: list[Article] | tuple[Article, ...] = (),
    *,
    today: date | None = None,
    max_unknown_ratio: float = DEFAULT_UNKNOWN_RATIO,
) -> DifficultyHint:
    """推导文章在档内应该偏易 / 中位 / 偏难。

    优先级：`low` > `high` > `mid`。读不下去时先降难度，避免为了“挑战”
    把用户劝退。数据不足时返回 `mid`，prompt 与旧版完全一致。
    """
    if profile is None:
        return DifficultyHint()
    current = normalize_cefr_level(profile.cefr_level, "B1")
    current_index = CEFR_LEVELS.index(current)
    if current_index + 1 >= len(CEFR_LEVELS):
        # C1 没有上一档：固定中位，不再叠加更高要求。
        return DifficultyHint("mid", "已到最高档")

    next_level = CEFR_LEVELS[current_index + 1]
    day = today or date.today()
    recent_start = day - timedelta(days=ARTICLE_RECENT_DAYS - 1)

    valid_articles = [
        (article_day, ratio)
        for article in articles
        if (article_day := _parse_day(article.article_date)) is not None
        and (ratio := _ratio_from_metrics(article.metrics)) is not None
        and article_day <= day
    ]
    valid_articles.sort(key=lambda item: item[0], reverse=True)
    latest_ratio = valid_articles[0][1] if valid_articles else None
    two_hard = len(valid_articles) >= 2 and all(
        ratio > max_unknown_ratio for _, ratio in valid_articles[:2]
    )

    downgraded = False
    for quiz in quizzes:
        if not quiz.completed or quiz.practice_mode or not quiz.level_changed:
            continue
        quiz_day = _parse_day(quiz.completed_at or "")
        if quiz_day is None or not (recent_start <= quiz_day <= day):
            continue
        previous_index = _level_index(normalize_cefr_level(quiz.previous_level))
        result_index = _level_index(normalize_cefr_level(quiz.result_level))
        if (
            previous_index is not None
            and result_index is not None
            and result_index < previous_index
        ):
            downgraded = True
            break

    if two_hard or downgraded:
        reason = "最近两篇生词比例偏高" if two_hard else "近 7 天刚降过级"
        return DifficultyHint("low", reason)

    latest = _latest_formal(quizzes)
    next_probability = 0.0
    if latest is not None and len(latest.posterior) == len(CEFR_LEVELS):
        mapping = dict(zip(CEFR_LEVELS, latest.posterior, strict=True))
        next_probability = float(mapping.get(next_level, 0.0))
    pending = (
        profile.pending_count > 0 and normalize_cefr_level(profile.pending_level) == next_level
    )
    if (next_probability >= HIGH_HINT_NEXT_PROBABILITY or pending) and (
        latest_ratio is None or latest_ratio <= max_unknown_ratio
    ):
        if pending:
            reason = f"已积累 {profile.pending_count}/2 次升级证据"
        else:
            reason = f"向 {next_level} 的把握约 {next_probability * 100:.0f}%"
        return DifficultyHint("high", reason)

    return DifficultyHint("mid", "暂无足够信号，保持档内中位")


__all__ = [
    "BAND_MIN_REVIEWS",
    "DEFAULT_UNKNOWN_RATIO",
    "DifficultyHint",
    "HIGH_HINT_NEXT_PROBABILITY",
    "LevelProgress",
    "QuizProgressSummary",
    "TREND_THRESHOLD",
    "build_level_progress",
    "difficulty_hint",
    "summarize_quiz",
]
