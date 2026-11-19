"""Phase 2 本地后验估计器：纯函数验收测试。"""

from __future__ import annotations

import pytest

from english_learner.models.entities import (
    BandReviewStat,
    EvidenceObservation,
    LevelEvidence,
    QuizQuestion,
    ReadingQuiz,
)
from english_learner.services.level_estimator import (
    CONFIDENCE_THRESHOLD,
    LevelPosterior,
    estimate_level,
    observations_from_blind_level,
    observations_from_quiz,
    observations_from_self_report,
    posterior_to_tuple,
    replay_evidence,
    review_band_observations,
)

LEVELS = ("A2", "B1", "B2", "C1")


def obs(*specs: tuple[str, bool], weight: float = 1.0) -> tuple[EvidenceObservation, ...]:
    return tuple(
        EvidenceObservation(difficulty=level, correct=correct, weight=weight)
        for level, correct in specs
    )


def test_no_observations_keeps_current_level() -> None:
    posterior = estimate_level("B1", ())

    assert isinstance(posterior, LevelPosterior)
    assert posterior.map_level == "B1"
    assert posterior.recommended_level == "B1"
    assert sum(posterior.probabilities) == pytest.approx(1.0)
    assert posterior.observation_count == 0


def test_all_correct_easy_questions_do_not_upgrade() -> None:
    """连续答对低难度题只说明“基础稳”，不能快速升级。"""
    posterior = estimate_level("B1", obs(*(("A2", True),) * 6))

    assert posterior.map_level != "C1"
    assert posterior.recommended_level == "B1"


def test_all_correct_hard_questions_upgrade_one_step() -> None:
    """B1 用户连续答对 B2 题，应建议 B2，但不跨到 C1。"""
    posterior = estimate_level("B1", obs(*(("B2", True),) * 6))

    assert posterior.map_level in {"B2", "C1"}
    assert posterior.recommended_level == "B2"
    assert posterior.confidence >= CONFIDENCE_THRESHOLD


def test_all_wrong_same_level_downgrades() -> None:
    posterior = estimate_level("B1", obs(*(("B1", False),) * 6))

    assert posterior.map_level == "A2"
    assert posterior.recommended_level == "A2"
    assert posterior.confidence >= CONFIDENCE_THRESHOLD


def test_boundary_levels_do_not_overflow() -> None:
    lowest = estimate_level("A2", obs(*(("A2", False),) * 8))
    highest = estimate_level("C1", obs(*(("C1", True),) * 8))

    assert lowest.recommended_level == "A2"
    assert highest.recommended_level == "C1"


def test_mixed_difficulty_stays_current_when_evidence_is_balanced() -> None:
    mixed = obs(
        ("A2", True),
        ("B1", True),
        ("B1", False),
        ("B2", False),
        ("C1", False),
        ("B1", True),
    )
    posterior = estimate_level("B1", mixed)

    assert posterior.recommended_level == "B1"


def test_unknown_difficulty_falls_back_to_current_level() -> None:
    quiz = ReadingQuiz(
        article_id=1,
        article_date="2026-11-19",
        level="B1",
        questions=(
            QuizQuestion(
                prompt="Q1?",
                options=("a", "b"),
                answer_index=1,
                difficulty="",
            ),
        ),
    )
    observations = observations_from_quiz(quiz, (1,))

    assert observations[0].difficulty == "B1"
    assert observations[0].correct is True


def test_quiz_observations_include_production_items() -> None:
    quiz = ReadingQuiz(
        article_id=1,
        article_date="2026-11-19",
        level="B1",
        questions=(
            QuizQuestion(prompt="Q1?", options=("a", "b"), answer_index=0, difficulty="B2"),
            QuizQuestion(
                prompt="Rewrite it.",
                kind="short",
                skill="rewrite",
                difficulty="C1",
            ),
        ),
    )
    observations = observations_from_quiz(quiz, (1,), (1,))

    assert len(observations) == 2
    assert observations[0].correct is False
    assert observations[1].difficulty == "C1"
    assert observations[1].source == "production"


def test_self_report_is_a_weak_observation() -> None:
    easy = observations_from_self_report("easy", "B1")
    hard = observations_from_self_report("hard", "B1")
    ok = observations_from_self_report("ok", "B1")

    assert easy[0].correct is True and easy[0].weight == pytest.approx(0.25)
    assert hard[0].correct is False and hard[0].weight == pytest.approx(0.25)
    assert ok == ()


def test_blind_observation_encodes_direction() -> None:
    higher = observations_from_blind_level("B2", current_level="B1", weight=0.5)
    lower = observations_from_blind_level("A2", current_level="B1", weight=0.5)
    same = observations_from_blind_level("B1", current_level="B1", weight=0.5)
    invalid = observations_from_blind_level("Z9", current_level="B1")

    assert higher[0].difficulty == "B2" and higher[0].correct is True
    assert higher[0].weight == pytest.approx(0.5)
    assert lower[0].difficulty == "A2" and lower[0].correct is False
    assert same == ()
    assert invalid == ()


def test_blind_lower_suggestion_pushes_posterior_down() -> None:
    # B1 用户，盲评 A2：应把后验往 A2 推，而不是往上推。
    only_blind = observations_from_blind_level("A2", current_level="B1", weight=0.8)
    posterior = estimate_level("B1", only_blind)

    assert posterior.probabilities[0] > estimate_level("B1", ()).probabilities[0]


def test_blind_higher_suggestion_pushes_posterior_up() -> None:
    # B1 用户，盲评 B2：应把后验往 B2 推。
    only_blind = observations_from_blind_level("B2", current_level="B1", weight=0.8)
    posterior = estimate_level("B1", only_blind)

    assert posterior.probabilities[2] > estimate_level("B1", ()).probabilities[2]


def test_replay_evidence_decays_older_observations() -> None:
    recent = LevelEvidence(
        source="quiz",
        observed_at="2026-11-19T10:00:00",
        observations=(EvidenceObservation("B2", True, 1.0),),
    )
    older = LevelEvidence(
        source="quiz",
        observed_at="2026-11-18T10:00:00",
        observations=(EvidenceObservation("B2", True, 1.0),),
    )

    replayed = replay_evidence((recent, older))

    assert replayed[0].weight == pytest.approx(1.0)
    assert replayed[1].weight < replayed[0].weight
    assert replayed[0].source.startswith("history:")


def test_review_band_stats_become_weak_observations() -> None:
    stats = (
        BandReviewStat("B2", 20, 16),
        BandReviewStat("B1", 3, 3),  # 样本太少，不计入
    )
    observations = review_band_observations(stats)

    assert len(observations) == 1
    assert observations[0].difficulty == "B2"
    assert observations[0].correct is True
    assert observations[0].weight <= 0.8


def test_posterior_tuple_is_rounded_and_aligned() -> None:
    posterior = estimate_level("B1", obs(("B1", True)))
    values = posterior_to_tuple(posterior)

    assert len(values) == len(LEVELS)
    assert all(0.0 <= value <= 1.0 for value in values)
    assert sum(values) == pytest.approx(1.0, abs=0.001)
