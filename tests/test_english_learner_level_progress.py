"""M6.4 / M6.5：档内进度与难度提示的纯函数测试。"""

from __future__ import annotations

from datetime import date, timedelta

from english_learner.models.entities import (
    Article,
    BandReviewStat,
    Profile,
    QuizProduction,
    QuizQuestion,
    ReadingQuiz,
)
from english_learner.services.level_progress import (
    DifficultyHint,
    build_level_progress,
    difficulty_hint,
    summarize_quiz,
)

TODAY = date(2026, 11, 20)


def make_quiz(
    *,
    level: str = "B1",
    posterior: tuple[float, ...] = (0.05, 0.55, 0.40, 0.0),
    practice_mode: bool = False,
    completed_at: str = "2026-11-20T10:00:00",
    previous_level: str = "",
    result_level: str = "",
    confidence: float = 0.7,
    questions: tuple[QuizQuestion, ...] = (),
    answers: tuple[int, ...] = (),
    productions: tuple[QuizProduction, ...] = (),
    quiz_id: int = 1,
) -> ReadingQuiz:
    return ReadingQuiz(
        article_id=1,
        article_date=completed_at[:10],
        level=level,
        questions=questions,
        answers=answers,
        productions=productions,
        posterior=posterior,
        practice_mode=practice_mode,
        completed_at=completed_at,
        previous_level=previous_level,
        result_level=result_level,
        level_confidence=confidence,
        id=quiz_id,
    )


def choice(difficulty: str, answer_index: int = 0) -> QuizQuestion:
    return QuizQuestion(
        prompt="q",
        options=("a", "b", "c", "d"),
        answer_index=answer_index,
        difficulty=difficulty,
    )


def short(difficulty: str) -> QuizQuestion:
    return QuizQuestion(prompt="译成中文", kind="short", skill="translation", difficulty=difficulty)


def make_article(
    day: date, ratio: float, *, word_total: int = 200, wordlist: int = 3000
) -> Article:
    return Article(
        article_date=day.isoformat(),
        title="t",
        content="c",
        level="B1",
        metrics={
            "word_total": word_total,
            "unknown_ratio": ratio,
            "wordlist_size": wordlist,
        },
    )


# ---------------------------------------------------------------- 进度
def test_progress_uses_posterior_of_current_and_next_level() -> None:
    progress = build_level_progress(Profile(cefr_level="B1"), [make_quiz()], today=TODAY)

    assert progress is not None
    assert progress.next_level == "B2"
    assert progress.progress is not None
    assert abs(progress.progress - 0.40 / 0.95) < 1e-6
    assert progress.progress_percent == 42
    assert progress.progress_source == "posterior"
    assert progress.confidence == 0.7


def test_progress_falls_back_to_pending_when_old_data_has_no_posterior() -> None:
    profile = Profile(cefr_level="B1", pending_level="B2", pending_count=1)
    progress = build_level_progress(profile, [make_quiz(posterior=())], today=TODAY)

    assert progress is not None
    assert progress.progress == 0.5
    assert progress.progress_percent == 50
    assert progress.progress_source == "pending"


def test_progress_ignores_practice_quiz_for_ratio() -> None:
    formal = make_quiz(quiz_id=1, completed_at="2026-11-19T10:00:00")
    practice = make_quiz(
        quiz_id=2,
        completed_at="2026-11-20T10:00:00",
        practice_mode=True,
        posterior=(0.0, 0.1, 0.9, 0.0),
    )
    progress = build_level_progress(Profile(cefr_level="B1"), [formal, practice], today=TODAY)

    assert progress is not None
    assert progress.practice_only is True
    assert abs(progress.progress - 0.40 / 0.95) < 1e-6


def test_practice_only_day_still_shows_progress_from_last_formal() -> None:
    formal = make_quiz(quiz_id=1, completed_at="2026-11-18T10:00:00")
    practice = make_quiz(quiz_id=2, completed_at="2026-11-20T10:00:00", practice_mode=True)
    progress = build_level_progress(Profile(cefr_level="B1"), [formal, practice], today=TODAY)

    assert progress is not None
    assert progress.practice_only is True
    assert progress.progress is not None


def test_c1_has_no_next_level_or_progress() -> None:
    progress = build_level_progress(
        Profile(cefr_level="C1", level_confidence=0.9),
        [make_quiz(level="C1", posterior=(0.0, 0.02, 0.18, 0.80))],
        today=TODAY,
    )

    assert progress is not None
    assert progress.next_level == ""
    assert progress.progress is None
    assert progress.progress_percent is None
    assert progress.confidence == 0.7


def test_no_profile_returns_none() -> None:
    assert build_level_progress(None, [make_quiz()], today=TODAY) is None


# ---------------------------------------------------------------- 题目摘要
def test_summarize_quiz_counts_correct_and_high_difficulty() -> None:
    quiz = make_quiz(
        questions=(
            choice("B1", 0),
            choice("B1", 0),
            choice("B2", 0),
            choice("C1", 0),
            short("B2"),
        ),
        answers=(0, 1, 0, 0),
        productions=(QuizProduction(text="译文", score=1),),
    )

    summary = summarize_quiz(quiz, "B1")

    assert summary.answered == 5
    assert summary.correct == 4  # B1 对、B1 错、B2 对、C1 对、产出题 1 分
    assert summary.high_correct == 1  # 只有 C1 比 B1 高两档
    assert summary.difficulty_counts == (("B1", 2), ("B2", 2), ("C1", 1))


def test_summarize_quiz_skips_unscored_production() -> None:
    quiz = make_quiz(
        questions=(short("B2"),),
        productions=(QuizProduction(text="译文", score=0, scored=False),),
    )

    summary = summarize_quiz(quiz, "B1")

    # AI 漏评分的产出题既不算答对，也不进分母，避免“答对 0/1”误导。
    assert summary.answered == 0
    assert summary.correct == 0
    assert summary.high_correct == 0
    assert summary.has_data is False


# ---------------------------------------------------------------- 生词比例
def test_article_ratio_trend_compares_recent_and_previous_windows() -> None:
    articles = [
        make_article(TODAY - timedelta(days=1), 0.10),
        make_article(TODAY - timedelta(days=2), 0.08),
        make_article(TODAY - timedelta(days=8), 0.05),
        make_article(TODAY - timedelta(days=9), 0.05),
    ]
    progress = build_level_progress(Profile(cefr_level="B1"), [], [], articles, today=TODAY)

    assert progress is not None
    assert progress.article is not None
    assert progress.article.recent_count == 2
    assert abs(progress.article.recent_ratio - 0.09) < 1e-6
    assert progress.article.previous_ratio is not None
    assert progress.article.direction == "up"


def test_article_ratio_ignores_unreliable_metrics() -> None:
    articles = [
        make_article(TODAY - timedelta(days=1), 0.30, word_total=10),
        make_article(TODAY - timedelta(days=2), 0.30, wordlist=100),
    ]
    progress = build_level_progress(Profile(cefr_level="B1"), [], [], articles, today=TODAY)

    assert progress is not None
    assert progress.article is None


def test_article_direction_is_flat_without_previous_window() -> None:
    articles = [make_article(TODAY - timedelta(days=1), 0.10)]
    progress = build_level_progress(Profile(cefr_level="B1"), [], [], articles, today=TODAY)

    assert progress is not None
    assert progress.article is not None
    assert progress.article.previous_ratio is None
    assert progress.article.direction == "flat"


# ---------------------------------------------------------------- 词档
def test_band_summary_requires_at_least_four_reviews() -> None:
    stats = [BandReviewStat(band="B1", total=3, known=2)]
    progress = build_level_progress(Profile(cefr_level="B1"), [], stats, [], today=TODAY)
    assert progress is not None
    assert progress.band is None

    stats = [BandReviewStat(band="B1", total=4, known=3)]
    progress = build_level_progress(Profile(cefr_level="B1"), [], stats, [], today=TODAY)
    assert progress is not None
    assert progress.band is not None
    assert progress.band.band == "B1"
    assert progress.band.accuracy == 0.75
    assert progress.band.next_band == "B2"
    assert progress.band.next_accuracy is None


# ---------------------------------------------------------------- 难度提示
def test_hint_is_high_when_next_level_probability_is_strong() -> None:
    quiz = make_quiz(posterior=(0.03, 0.47, 0.50, 0.0))
    hint = difficulty_hint(Profile(cefr_level="B1"), [quiz], [], today=TODAY)
    assert hint == DifficultyHint("high", "向 B2 的把握约 50%")


def test_hint_is_high_when_pending_upgrade() -> None:
    profile = Profile(cefr_level="B1", pending_level="B2", pending_count=1)
    hint = difficulty_hint(profile, [], [], today=TODAY)
    assert hint.within_level == "high"
    assert "1/2" in hint.reason


def test_hint_is_low_when_two_recent_articles_are_hard() -> None:
    articles = [
        make_article(TODAY - timedelta(days=1), 0.20),
        make_article(TODAY - timedelta(days=2), 0.18),
    ]
    hint = difficulty_hint(Profile(cefr_level="B1"), [], articles, today=TODAY)
    assert hint.within_level == "low"
    assert "生词比例" in hint.reason


def test_hint_is_low_after_recent_downgrade() -> None:
    quiz = make_quiz(
        completed_at="2026-11-18T10:00:00",
        previous_level="B2",
        result_level="B1",
    )
    hint = difficulty_hint(Profile(cefr_level="B1"), [quiz], [], today=TODAY)
    assert hint.within_level == "low"
    assert "降过级" in hint.reason


def test_low_has_priority_over_high() -> None:
    articles = [
        make_article(TODAY - timedelta(days=1), 0.20),
        make_article(TODAY - timedelta(days=2), 0.18),
    ]
    profile = Profile(cefr_level="B1", pending_level="B2", pending_count=1)
    hint = difficulty_hint(profile, [make_quiz()], articles, today=TODAY)
    assert hint.within_level == "low"


def test_c1_hint_is_mid_without_next_level() -> None:
    profile = Profile(cefr_level="C1", pending_level="", pending_count=0)
    hint = difficulty_hint(profile, [make_quiz(level="C1")], [], today=TODAY)
    assert hint.within_level == "mid"


def test_hint_without_data_is_mid() -> None:
    hint = difficulty_hint(Profile(cefr_level="B1"), [], [], today=TODAY)
    assert hint == DifficultyHint("mid", "暂无足够信号，保持档内中位")


def test_pending_for_another_level_does_not_fake_progress() -> None:
    profile = Profile(cefr_level="B1", pending_level="C1", pending_count=2)
    progress = build_level_progress(profile, [make_quiz(posterior=())], today=TODAY)

    assert progress is not None
    assert progress.progress is None
    assert progress.progress_source == ""


def test_article_window_boundary_includes_today_minus_six() -> None:
    articles = [
        make_article(TODAY - timedelta(days=6), 0.10),
        make_article(TODAY - timedelta(days=7), 0.05),
    ]
    progress = build_level_progress(Profile(cefr_level="B1"), [], [], articles, today=TODAY)

    assert progress is not None
    assert progress.article is not None
    assert progress.article.recent_count == 1
    assert progress.article.previous_ratio is not None
    assert abs(progress.article.previous_ratio - 0.05) < 1e-6


def test_downgrade_at_window_edge() -> None:
    within = make_quiz(
        completed_at=(TODAY - timedelta(days=6)).isoformat() + "T10:00:00",
        previous_level="B2",
        result_level="B1",
    )
    outside = make_quiz(
        quiz_id=2,
        completed_at=(TODAY - timedelta(days=7)).isoformat() + "T10:00:00",
        previous_level="B2",
        result_level="B1",
    )

    assert (
        difficulty_hint(Profile(cefr_level="B1"), [within], [], today=TODAY).within_level == "low"
    )
    assert (
        difficulty_hint(Profile(cefr_level="B1"), [outside], [], today=TODAY).within_level == "mid"
    )


def test_confidence_falls_back_to_profile_when_quiz_row_has_none() -> None:
    progress = build_level_progress(
        Profile(cefr_level="B1", level_confidence=0.66),
        [make_quiz(confidence=0.0)],
        today=TODAY,
    )

    assert progress is not None
    assert progress.confidence == 0.66
