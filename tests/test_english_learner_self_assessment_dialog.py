"""阅读自评弹窗测试：未选择不能开始，选择后返回 easy / ok / hard。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from english_learner.gui.self_assessment_dialog import (  # noqa: E402
    SelfAssessmentDialog,
)

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_requires_selection_before_start() -> None:
    get_app()
    dialog = SelfAssessmentDialog()

    assert dialog.self_report() == ""
    assert dialog._start_button.isEnabled() is False

    dialog._buttons[2].setChecked(True)
    assert dialog._start_button.isEnabled() is True
    assert dialog.self_report() == "hard"

    dialog._start_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_switching_choice_updates_value() -> None:
    get_app()
    dialog = SelfAssessmentDialog()

    dialog._buttons[0].setChecked(True)
    assert dialog.self_report() == "easy"
    dialog._buttons[1].setChecked(True)
    assert dialog.self_report() == "ok"
    dialog.close()


def test_cancel_returns_without_choice() -> None:
    get_app()
    dialog = SelfAssessmentDialog()

    dialog._cancel_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.self_report() == ""
    dialog.close()
