"""回译练习弹窗测试（offscreen，AI 用桩服务替身）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.back_translation_dialog import (  # noqa: E402
    BackTranslationDialog,
)
from english_learner.models.entities import (  # noqa: E402
    Article,
    WritingAttempt,
    now_iso,
    today_str,
)
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


def make_article(db: Database) -> Article:
    article = db.articles.insert(
        Article(
            article_date=today_str(),
            title="Small Habits",
            content=(
                "Resilient people recover from setbacks and keep moving toward their goals. "
                "The team worked late into the night to finish the project before the deadline. "
                "Small habits add up over time and quietly change our lives."
            ),
            level="B1",
            word_count=30,
            created_at=now_iso(),
        )
    )
    assert article.id is not None
    return article


def make_attempts(article: Article, *, graded: bool = False) -> tuple[WritingAttempt, ...]:
    attempts: list[WritingAttempt] = []
    for index in range(3):
        score = 2 if (graded and index < 2) else 0
        attempts.append(
            WritingAttempt(
                id=index + 1,
                article_id=article.id or 1,
                article_date=article.article_date,
                position=index,
                sentence_en=f"Sentence number {index + 1}.",
                reference_cn=f"第 {index + 1} 句中文。",
                answer_en=f"My answer {index + 1}." if graded else "",
                verdict=(
                    {
                        "score": score,
                        "edited": f"Edited {index + 1}.",
                        "reason": f"原因 {index + 1}",
                        "error_types": [] if score == 2 else ["tense"],
                    }
                    if graded
                    else None
                ),
                score=score,
                created_at=now_iso(),
                graded_at=now_iso() if graded else None,
            )
        )
    return tuple(attempts)


class StubService:
    """不访问网络的替身：返回预置题目 / 批改结果。"""

    def __init__(
        self,
        cached: tuple[WritingAttempt, ...] = (),
        *,
        generated: tuple[WritingAttempt, ...] | None = None,
        graded: tuple[WritingAttempt, ...] | None = None,
        prepare_error: Exception | None = None,
        grade_error: Exception | None = None,
    ) -> None:
        self.cached = cached
        self.generated = generated if generated is not None else cached
        self.graded = graded or ()
        self.prepare_error = prepare_error
        self.grade_error = grade_error
        self.get_cached_calls = 0
        self.load_calls = 0
        self.grade_calls = 0
        self.last_answers: tuple[str, ...] = ()

    def get_cached(self, article_id: int) -> tuple[WritingAttempt, ...]:
        self.get_cached_calls += 1
        return self.cached

    def load_or_create(self, article: Article) -> tuple[WritingAttempt, ...]:
        self.load_calls += 1
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.generated

    def grade(self, article: Article, answers) -> tuple[WritingAttempt, ...]:
        self.grade_calls += 1
        self.last_answers = tuple(answers)
        if self.grade_error is not None:
            raise self.grade_error
        return self.graded


def drain(dialog: BackTranslationDialog) -> None:
    worker = dialog._worker
    if worker is not None:
        assert worker.wait(3000) is True
    for _ in range(5):
        get_app().processEvents()


def test_cached_ungraded_shows_three_cards(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubService(make_attempts(article))

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )

    assert len(dialog._cards) == 3
    assert dialog._submit_button.text() == "提交批改"
    assert dialog._submit_button.isEnabled() is True
    assert dialog._cards[0]._cn_label.text() == "第 1 句中文。"
    assert dialog._cards[0]._edit.isReadOnly() is False
    assert service.load_calls == 0
    dialog.close()
    db.close()


def test_cached_graded_opens_results_without_new_ai(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubService(make_attempts(article, graded=True))

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )

    assert dialog._submit_button.text() == "已完成"
    assert dialog._submit_button.isEnabled() is False
    assert len(dialog.result_attempts) == 3
    assert dialog._cards[0]._edit.isReadOnly() is True
    assert dialog._cards[0]._edit.toPlainText() == "My answer 1."
    assert "4/6 分" in dialog._status.text()
    assert service.grade_calls == 0
    dialog.close()
    db.close()


def test_submit_requires_all_answers(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubService(make_attempts(article))

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )
    dialog._submit_button.click()

    assert service.grade_calls == 0
    assert "所有句子" in dialog._status.text()
    dialog.close()
    db.close()


def test_submit_grades_and_renders_results(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    ungraded = make_attempts(article)
    graded = make_attempts(article, graded=True)
    service = StubService(ungraded, graded=graded)

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )
    for index, card in enumerate(dialog._cards):
        card._edit.setPlainText(f"Answer {index + 1}.")
    dialog._submit_button.click()
    drain(dialog)

    assert service.grade_calls == 1
    assert service.last_answers == ("Answer 1.", "Answer 2.", "Answer 3.")
    assert dialog.result_attempts == graded
    assert "4/6 分" in dialog._status.text()
    assert dialog._submit_button.text() == "已完成"
    assert dialog._cards[0]._edit.isReadOnly() is True
    assert "最小修改：Edited 1." in dialog._cards[0]._edited_label.text()
    assert "问题类型：时态" in dialog._cards[2]._tags_label.text()
    dialog.close()
    db.close()


def test_missing_cache_prepares_in_background(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    generated = make_attempts(article)
    service = StubService((), generated=generated)

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )
    assert dialog._submit_button.isEnabled() is False
    drain(dialog)

    assert service.load_calls == 1
    assert len(dialog._cards) == 3
    assert dialog._submit_button.text() == "提交批改"
    dialog.close()
    db.close()


def test_incomplete_cache_prepares_again(tmp_path: Path) -> None:
    """残缺缓存（缺中文译文）与 service 的判定一致：重新出题而不是直接展示。"""
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    broken = tuple(
        WritingAttempt(
            id=item.id,
            article_id=item.article_id,
            article_date=item.article_date,
            position=item.position,
            sentence_en=item.sentence_en,
            reference_cn="",
            created_at=item.created_at,
        )
        for item in make_attempts(article)
    )
    service = StubService(broken, generated=make_attempts(article))

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )
    drain(dialog)

    assert service.load_calls == 1
    assert len(dialog._cards) == 3
    assert dialog._cards[0]._cn_label.text() == "第 1 句中文。"
    dialog.close()
    db.close()


def test_grade_failure_keeps_answers_and_retry_label(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubService(
        make_attempts(article),
        grade_error=AIError("批改失败", "可以直接重试。"),
    )

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )
    for index, card in enumerate(dialog._cards):
        card._edit.setPlainText(f"Answer {index + 1}.")
    dialog._submit_button.click()
    drain(dialog)

    assert dialog._submit_button.text() == "重新提交"
    assert dialog._submit_button.isEnabled() is True
    assert "批改失败" in dialog._status.text()
    assert dialog._cards[0]._edit.toPlainText() == "Answer 1."
    assert dialog._cards[0]._edit.isReadOnly() is False
    dialog.close()
    db.close()


def test_prepare_failure_offers_retry(tmp_path: Path) -> None:
    get_app()
    db = Database(tmp_path / "data.db")
    article = make_article(db)
    service = StubService(
        (),
        prepare_error=AIError("出题失败", "请检查网络或稍后重试。"),
    )

    dialog = BackTranslationDialog(
        make_context(db, tmp_path),
        article,
        service=service,  # type: ignore[arg-type]
    )
    drain(dialog)

    assert dialog._submit_button.text() == "重试"
    assert dialog._submit_button.isEnabled() is True
    assert "出题失败" in dialog._status.text()
    dialog.close()
    db.close()
