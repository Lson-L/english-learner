"""启动遮罩：重依赖导入与主窗口构建期间给用户即时反馈。

为什么需要它：
- `openai` SDK 与全部页面模块的导入在冷启动时要 1~2 秒，这段时间没有任何窗口，
  用户容易以为程序没启动；
- 本模块只依赖 PySide6 与 `theme` 的调色板，必须在导入任何业务模块之前可用；
- `app.main()` 先 `show()` 遮罩并 `processEvents()`，再导入重模块、跑备份、
  建库、构建主窗口，中间用 `set_message()` 更新阶段文案与进度。

Java 对照：≈ `java.awt.SplashScreen`——主界面出来前先显示一张启动画面。

注意：这里**不能** import `gui.main_window`（它会把 openai 与所有页面拉进来），
否则遮罩就失去意义了。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from .theme import LIGHT_PALETTE, LOW_PROFILE_PALETTE

# 与 main_window._SIDEBAR_TEXTS 的 subtitle 保持一致；不能直接 import 那份常量，
# 否则又把主窗口和 openai 带回来了。
_SUBTITLES: dict[bool, str] = {
    False: "每天读一点，就会不一样",
    True: "local",
}

_INITIAL_MESSAGES: dict[bool, str] = {
    False: "正在启动…",
    True: "starting…",
}

# 只覆盖颜色与字号，不设 font-family：英文外观仍由 gui/fonts.py 的全局字体链决定。
_SPLASH_QSS = """
QWidget#startupSplash {
    background: __BG__;
    border: 1px solid __BORDER__;
}
QLabel#splashTitle {
    color: __TEXT_STRONG__;
    font-size: 22px;
    font-weight: 600;
}
QLabel#splashSubtitle {
    color: __TEXT_MUTED__;
    font-size: 12px;
}
QLabel#splashStatus {
    color: __TEXT_SECOND__;
    font-size: 12px;
}
QProgressBar#splashProgress {
    background: __TRACK__;
    border: none;
    border-radius: 3px;
}
QProgressBar#splashProgress::chunk {
    background: __ACCENT__;
    border-radius: 3px;
}
"""


class StartupSplash(QWidget):
    """无边框启动遮罩：标题 + 副标题 + 阶段文案 + 进度条。

    进度值由调用方按阶段设置（不是真实百分比），每次 `set_message()` 后
    调用方需要 `QApplication.processEvents()` 才会重绘——重依赖导入会阻塞
    事件循环，遮罩本身无法在导入期间动起来。
    """

    def __init__(self, *, low_profile: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("startupSplash")
        # SplashScreen 类型：不进任务栏、置顶；ShowWithoutActivating 避免抢焦点。
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(420, 208)

        palette = LOW_PROFILE_PALETTE if low_profile else LIGHT_PALETTE
        qss = _SPLASH_QSS
        for name, value in palette.items():
            qss = qss.replace(f"__{name}__", value)
        self.setStyleSheet(qss)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 26)
        layout.setSpacing(6)

        title = QLabel("Workspace" if low_profile else "Daily English")
        title.setObjectName("splashTitle")
        layout.addWidget(title)

        subtitle = QLabel(_SUBTITLES[low_profile])
        subtitle.setObjectName("splashSubtitle")
        layout.addWidget(subtitle)

        layout.addStretch(1)

        self._status = QLabel(_INITIAL_MESSAGES[low_profile])
        self._status.setObjectName("splashStatus")
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setObjectName("splashProgress")
        self._bar.setRange(0, 100)
        self._bar.setValue(8)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        layout.addWidget(self._bar)

        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        rect = self.frameGeometry()
        rect.moveCenter(screen.availableGeometry().center())
        self.move(rect.topLeft())

    def set_message(self, text: str, progress: int | None = None) -> None:
        """更新阶段文案；`progress` 为 0~100 的展示进度，越界自动收敛。"""
        self._status.setText(text)
        if progress is not None:
            self._bar.setValue(max(0, min(100, progress)))

    def finish(self, window: QWidget | None = None) -> None:
        """主窗口已经 `show()` 之后收起遮罩。"""
        if window is not None:
            window.raise_()
        self.close()
        self.deleteLater()
