"""M7.1 / M7.2：真实文章供给门槛、间隔、候选打分与完成反馈。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from english_learner.models.entities import Profile, RealText, RealTextHistory
from english_learner.services.config_store import RealTextConfig
from english_learner.services.real_text_feedback import (
    OUTCOME_NEUTRAL,
    OUTCOME_STRUGGLE,
    OUTCOME_SUCCESS,
    outcome_from_completion,
)
from english_learner.services.real_text_supply import (
    OUTCOME_SPACING,
    RealTextSupplyService,
    level_window,
    spacing_from_outcome,
)

TODAY = date(2026, 11, 25)
TODAY_TEXT = TODAY.isoformat()


def active_days(count: int, *, end: date = TODAY - timedelta(days=1)) -> list[str]:
    return [(end - timedelta(days=offset)).isoformat() for offset in range(count)]


class FakeArticles:
    def __init__(self, *, today_article=None, recent=()) -> None:
        self._today_article = today_article
        self._recent = list(recent)

    def get_by_date(self, day: str):
        return self._today_article if self._today_article is not None else None

    def list_recent(self, limit: int = 30):
        return self._recent[:limit]


class FakeStats:
    def __init__(self, days: list[str]) -> None:
        self._rows = [SimpleNamespace(stat_date=day, is_active=True) for day in days]

    def list_all(self):
        return self._rows


class FakeQuizzes:
    def __init__(self, quizzes=()) -> None:
        self._quizzes = list(quizzes)

    def list_recent_formal_completed(self, limit: int = 8):
        return self._quizzes[:limit]


class FakeRealTexts:
    def __init__(self, unread=(), history: RealTextHistory | None = None) -> None:
        self._unread = list(unread)
        self._history = history
        self._by_id = {text.id: text for text in unread}

    def list_unread(self):
        return list(self._unread)

    def last_history(self):
        return self._history

    def get(self, text_id: str):
        return self._by_id.get(text_id)


def make_text(text_id: str, *, level: str = "B1", topics=("science",)) -> RealText:
    return RealText(
        id=text_id,
        title=f"Title {text_id}",
        content=" ".join(["word"] * 160),
        level=level,
        topics=tuple(topics),
        word_count=160,
    )


def make_quiz(
    *,
    score: int = 6,
    total: int = 8,
    previous: str = "B1",
    result: str = "B1",
    completed_at: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        score=score,
        total=total,
        previous_level=previous,
        result_level=result,
        completed_at=completed_at or (TODAY - timedelta(days=1)).isoformat() + "T20:00:00",
    )


def make_service(
    *,
    articles: FakeArticles | None = None,
    stats: FakeStats | None = None,
    quizzes: FakeQuizzes | None = None,
    texts: FakeRealTexts | None = None,
) -> RealTextSupplyService:
    return RealTextSupplyService(
        articles or FakeArticles(),
        stats or FakeStats(active_days(10)),
        quizzes or FakeQuizzes([make_quiz()]),
        texts or FakeRealTexts([make_text("b1-1")]),
        None,
        today=lambda: TODAY_TEXT,
    )


PROFILE = Profile(cefr_level="B1")


# --------------------------------------------------------------- 间隔 / 等级
def test_spacing_table_matches_product_decision() -> None:
    assert spacing_from_outcome("success") == (3, -1, 1)
    assert spacing_from_outcome("neutral") == (7, -1, 1)
    assert spacing_from_outcome("struggle") == (14, -1, 0)
    assert spacing_from_outcome("abandon") == (10, -1, 0)
    # 兼容文档 / 旧导出里的 abandoned 写法。
    assert spacing_from_outcome("abandoned") == (10, -1, 0)
    assert spacing_from_outcome("skip") == (7, -1, 1)
    assert spacing_from_outcome("") == OUTCOME_SPACING[""]


def test_level_window_clamps_to_cefr_range() -> None:
    assert level_window("B1", -1, 1) == ("A2", "B1", "B2")
    assert level_window("B1", -1, 0) == ("A2", "B1")
    assert level_window("A2", -1, 1) == ("A2", "B1")
    assert level_window("C1", -1, 1) == ("B2", "C1")
    assert level_window("?", -1, 1) == ("A2", "B1", "B2", "C1")


def test_outcome_priority_struggle_then_success_then_neutral() -> None:
    assert outcome_from_completion(0.9, "hard") == OUTCOME_STRUGGLE
    assert outcome_from_completion(0.4, "easy") == OUTCOME_STRUGGLE
    assert outcome_from_completion(0.7, "ok") == OUTCOME_SUCCESS
    assert outcome_from_completion(0.7, "easy") == OUTCOME_SUCCESS
    assert outcome_from_completion(0.6, "ok") == OUTCOME_NEUTRAL
    assert outcome_from_completion(0.7, "") == OUTCOME_NEUTRAL
    assert outcome_from_completion(0.49, "ok") == OUTCOME_STRUGGLE


# ------------------------------------------------------------------- 门槛
def test_gate_disabled() -> None:
    decision = make_service().decide(PROFILE, RealTextConfig(auto_enabled=False))
    assert decision.action == "skip"
    assert decision.reason == "disabled"


def test_gate_article_exists_and_already_decided() -> None:
    service = make_service(articles=FakeArticles(today_article=object()))
    assert service.decide(PROFILE, RealTextConfig()).reason == "article_exists"

    service = make_service()
    decision = service.decide(PROFILE, RealTextConfig(last_decision_on=TODAY_TEXT))
    assert decision.reason == "already_decided"


def test_gate_no_profile() -> None:
    assert make_service().decide(None, RealTextConfig()).reason == "no_profile"


def test_gate_new_user() -> None:
    service = make_service(stats=FakeStats(active_days(6)))
    assert service.decide(PROFILE, RealTextConfig()).reason == "new_user"


def test_gate_downgraded_recently() -> None:
    quizzes = FakeQuizzes([make_quiz(previous="B1", result="A2")])
    service = make_service(quizzes=quizzes)
    assert service.decide(PROFILE, RealTextConfig()).reason == "downgraded_recently"


def test_gate_struggling_by_recent_average() -> None:
    quizzes = FakeQuizzes([make_quiz(score=2, total=8) for _ in range(3)])
    service = make_service(quizzes=quizzes)
    assert service.decide(PROFILE, RealTextConfig()).reason == "struggling"


def test_gate_too_hard_by_generated_article_ratio() -> None:
    articles = FakeArticles(
        recent=[
            SimpleNamespace(
                source="generated",
                metrics={
                    "word_total": 200,
                    "unknown_count": 24,
                    "wordlist_size": 2000,
                },
            )
        ]
    )
    service = make_service(articles=articles)
    assert service.decide(PROFILE, RealTextConfig()).reason == "too_hard"


def test_gate_welcome_back_after_long_break() -> None:
    # 近 30 天有 7 个活跃日，但最近一次活动在 10 天前。
    service = make_service(stats=FakeStats(active_days(7, end=TODAY - timedelta(days=10))))
    assert service.decide(PROFILE, RealTextConfig()).reason == "welcome_back"


def test_gate_level_changing_within_guard_window() -> None:
    quizzes = FakeQuizzes([make_quiz(previous="B1", result="B2")])
    service = make_service(quizzes=quizzes)
    assert service.decide(PROFILE, RealTextConfig()).reason == "level_changing"


def test_gate_too_soon_after_success() -> None:
    history = RealTextHistory(
        text_id="b1-1",
        served_on=(TODAY - timedelta(days=2)).isoformat(),
        created_at="2026-11-01T00:00:00",
        updated_at="2026-11-01T00:00:00",
        outcome="success",
    )
    service = make_service(texts=FakeRealTexts([make_text("b1-1")], history))
    assert service.decide(PROFILE, RealTextConfig()).reason == "too_soon"


def test_gate_no_library_when_all_texts_are_read() -> None:
    service = make_service(texts=FakeRealTexts([]))
    assert service.decide(PROFILE, RealTextConfig()).reason == "no_library"


def test_gate_no_candidate_when_ratio_out_of_window(monkeypatch) -> None:
    service = make_service()
    monkeypatch.setattr(service, "estimate_unknown_ratio", lambda text: 0.2)
    assert service.decide(PROFILE, RealTextConfig()).reason == "no_candidate"


# ------------------------------------------------------------------- 供给
def test_serves_best_candidate_deterministically(monkeypatch) -> None:
    texts = FakeRealTexts(
        [
            make_text("b1-1", topics=("science",)),
            make_text("b1-2", topics=("history",)),
            make_text("b2-1", level="B2"),
        ]
    )
    service = make_service(texts=texts)
    ratios = {"b1-1": 0.05, "b1-2": 0.05, "b2-1": 0.05}
    monkeypatch.setattr(service, "estimate_unknown_ratio", lambda text: ratios[text.id])

    first = service.decide(PROFILE, RealTextConfig())
    second = service.decide(PROFILE, RealTextConfig())

    assert first.should_serve
    assert first.text is not None
    assert first.text.level in ("A2", "B1", "B2")
    assert 0 < first.score <= 1
    assert second.text is not None and first.text.id == second.text.id


def test_serves_prefers_closer_ratio(monkeypatch) -> None:
    texts = FakeRealTexts([make_text("b1-1"), make_text("b1-2"), make_text("b1-3")])
    service = make_service(texts=texts)
    ratios = {"b1-1": 0.03, "b1-2": 0.07, "b1-3": 0.05}
    monkeypatch.setattr(service, "estimate_unknown_ratio", lambda text: ratios[text.id])

    decision = service.decide(PROFILE, RealTextConfig())

    assert decision.should_serve
    assert decision.text is not None
    assert decision.text.id == "b1-3"


def test_struggle_window_excludes_higher_levels(monkeypatch) -> None:
    history = RealTextHistory(
        text_id="b2-9",
        served_on=(TODAY - timedelta(days=30)).isoformat(),
        created_at="2026-10-01T00:00:00",
        updated_at="2026-10-01T00:00:00",
        outcome="struggle",
    )
    texts = FakeRealTexts([make_text("b1-1"), make_text("b2-1", level="B2")], history)
    service = make_service(texts=texts)
    monkeypatch.setattr(service, "estimate_unknown_ratio", lambda text: 0.05)

    decision = service.decide(PROFILE, RealTextConfig())

    assert decision.should_serve
    assert decision.text is not None
    assert decision.text.id == "b1-1"


def test_estimate_unknown_ratio_uses_readability(monkeypatch) -> None:
    service = make_service()
    from english_learner.services import real_text_supply

    monkeypatch.setattr(
        real_text_supply,
        "analyze",
        lambda *args, **kwargs: SimpleNamespace(unknown_ratio=0.042),
    )
    assert service.estimate_unknown_ratio(make_text("b1-1")) == pytest.approx(0.042)


def test_abandoned_history_uses_ten_day_spacing_and_lower_window(monkeypatch) -> None:
    """expire_abandoned 写的是 outcome='abandon'，间隔必须是 10 天且不能再升档。"""

    def history(days: int) -> RealTextHistory:
        return RealTextHistory(
            text_id="b2-9",
            served_on=(TODAY - timedelta(days=days)).isoformat(),
            created_at="2026-10-01T00:00:00",
            updated_at="2026-10-01T00:00:00",
            status="abandoned",
            outcome="abandon",
        )

    early = make_service(texts=FakeRealTexts([make_text("b2-1", level="B2")], history(9)))
    assert early.decide(PROFILE, RealTextConfig()).reason == "too_soon"

    on_time = make_service(texts=FakeRealTexts([make_text("b2-1", level="B2")], history(10)))
    monkeypatch.setattr(on_time, "estimate_unknown_ratio", lambda text: 0.05)
    assert on_time.decide(PROFILE, RealTextConfig()).reason == "no_candidate"
