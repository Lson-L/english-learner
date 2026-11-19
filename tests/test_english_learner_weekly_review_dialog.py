"""每周复盘弹窗测试（offscreen，AI 用桩服务替身）。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.weekly_review_dialog import WeeklyReviewDialog  # noqa: E402
from english_learner.models.entities import Profile, WeeklyReview, now_iso  # noqa: E402
from english_learner.services.ai_client import AIError  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_context(db: Database, tmp_path: Path) -> AppContext:
    return AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(tmp_path / "config.json"),
        db,
    )


def make_review(*, suggested: str = "B2", applied: str = "") -> WeeklyReview:
    return WeeklyReview(
        id=1,
        week_start="2026-11-16",
        summary="本周完成 1 次检测，阅读稳定性提升。",
        suggested_level=suggested,
        focus=("推断题", "B2 高频词"),
        quiz_count=1,
        review_count=3,
        words_added=2,
        confidence=0.7,
        applied_level=applied,
        created_at=now_iso(),
    )


class StubWeeklyService:
    def __init__(self, review: WeeklyReview | None = None, error: Exception | None = None) -> None:
        self.review = review
        self.error = error
        self.calls = 0
        self.apply_calls = 0

    def get_or_generate(self, profile: Profile) -> WeeklyReview:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.review is not None
        return self.review

    def apply_suggestion(self, review: WeeklyReview, profile: Profile) -> Profile:
        self.apply_calls += 1
        self.review = replace(
            review,
            applied_level=review.suggested_level,
            applied_at=now_iso(),
        )
        return replace(profile, cefr_level=review.suggested_level)

    def latest(self) -> WeeklyReview | None:
        return self.review


def test_loads_review_and_enables_apply(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1"))
    service = StubWeeklyService(make_review())

    dialog = WeeklyReviewDialog(make_context(db, tmp_path), service=service)  # type: ignore[arg-type]
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert service.calls == 1
    assert dialog.review is not None
    assert dialog._apply_button.isEnabled() is True
    assert "B2" in dialog._suggestion_label.text()
    dialog.close()
    db.close()


def test_apply_updates_profile_and_result(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1"))
    service = StubWeeklyService(make_review())
    dialog = WeeklyReviewDialog(make_context(db, tmp_path), service=service)  # type: ignore[arg-type]
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    dialog._apply()

    assert service.apply_calls == 1
    assert dialog.applied is True
    assert dialog.result_profile is not None
    assert dialog.result_profile.cefr_level == "B2"
    # 已应用后按钮不可再点
    assert dialog._apply_button.isEnabled() is False
    dialog.close()
    db.close()


def test_keep_current_does_not_change_profile(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1"))
    service = StubWeeklyService(make_review())
    dialog = WeeklyReviewDialog(make_context(db, tmp_path), service=service)  # type: ignore[arg-type]
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    dialog._keep_button.click()

    assert service.apply_calls == 0
    assert dialog.result_profile is None
    profile = db.profiles.get()
    assert profile is not None and profile.cefr_level == "B1"
    db.close()


def test_no_profile_shows_error_without_calling_ai(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    service = StubWeeklyService(make_review())

    dialog = WeeklyReviewDialog(make_context(db, tmp_path), service=service)  # type: ignore[arg-type]

    assert dialog._worker is None
    assert service.calls == 0
    assert dialog._apply_button.isEnabled() is False
    assert dialog._keep_button.isEnabled() is True
    dialog.close()
    db.close()


def test_ai_failure_shows_error_and_allows_close(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B1"))
    service = StubWeeklyService(error=AIError("网关超时", "稍后重试"))

    dialog = WeeklyReviewDialog(make_context(db, tmp_path), service=service)  # type: ignore[arg-type]
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert dialog.review is None
    assert dialog._apply_button.isEnabled() is False
    assert dialog._keep_button.isEnabled() is True
    assert dialog._retry_button.isHidden() is False
    dialog.close()
    db.close()


def test_already_applied_review_disables_apply(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    db.profiles.save(Profile(cefr_level="B2"))
    service = StubWeeklyService(make_review(suggested="B2", applied="B2"))

    dialog = WeeklyReviewDialog(make_context(db, tmp_path), service=service)  # type: ignore[arg-type]
    worker = dialog._worker
    assert worker is not None
    assert worker.wait(3000) is True
    get_app().processEvents()

    assert dialog._apply_button.isEnabled() is False
    dialog.close()
    db.close()
