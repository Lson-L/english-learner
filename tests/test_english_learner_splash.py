"""启动遮罩测试：文案 / 主题 / 重依赖延迟导入。

注意：环境变量必须在 import PySide6 之前设置。
"""

from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from english_learner.gui.splash import StartupSplash  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_splash_is_frameless_top_level_and_updates_message() -> None:
    get_app()
    splash = StartupSplash(low_profile=False)
    try:
        flags = splash.windowFlags()
        assert flags & Qt.WindowType.SplashScreen
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint

        assert splash._status.text() == "正在启动…"
        splash.set_message("正在加载界面组件…", 35)
        assert splash._status.text() == "正在加载界面组件…"
        assert splash._bar.value() == 35
        # 越界进度必须收敛，不能把进度条设成超出范围的值。
        splash.set_message("正在准备主界面…", 250)
        assert splash._bar.value() == 100
    finally:
        splash.close()


def test_low_profile_splash_uses_neutral_english_copy() -> None:
    get_app()
    splash = StartupSplash(low_profile=True)
    try:
        texts = [label.text() for label in splash.findChildren(QLabel)]
        assert "Workspace" in texts
        assert "local" in texts
        assert "starting…" in texts
        assert all("每日英语" not in text for text in texts)
    finally:
        splash.close()


def test_app_module_defers_heavy_imports_until_splash() -> None:
    """`english_learner.app` 顶层不能再导入 openai / 主窗口，否则遮罩来不及显示。"""
    code = (
        "import sys\n"
        "import english_learner.app\n"
        "heavy = [m for m in ('openai', 'english_learner.gui.main_window') if m in sys.modules]\n"
        "assert not heavy, f'这些模块应延迟到遮罩显示后再导入: {heavy}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
