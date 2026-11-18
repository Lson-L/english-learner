"""GUI 冒烟测试：不弹真实窗口（offscreen），只验证控件能创建、状态机正确。

注意：环境变量必须在 import PySide6 之前设置。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.main_window import MainWindow  # noqa: E402
from english_learner.gui.onboarding import OnboardingDialog  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_config() -> AIConfig:
    return AIConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )


def test_onboarding_requires_successful_test(tmp_path: Path) -> None:
    get_app()
    dialog = OnboardingDialog(ConfigStore(tmp_path / "config.json"))
    dialog._show_step(1)

    assert not dialog._primary_button.isEnabled()

    dialog._tested_config = make_config()
    dialog._show_step(1)
    assert dialog._primary_button.isEnabled()

    dialog._invalidate_test()
    assert not dialog._primary_button.isEnabled()


def test_main_window_builds_all_pages(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())

    window = MainWindow(make_config(), store)

    assert window._stack.count() == 5
    assert window._settings_page is not None
    assert window._nav_group.buttons()[0].isChecked()
