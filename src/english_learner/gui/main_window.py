"""主窗口：左侧导航 + 右侧页面栈。

M0 的职责只有两个：
1. 把信息架构立起来（今日 / 阅读 / 生词本 / 复习 / 设置）；
2. 提供修改 AI 配置的入口，并承接首次配置的结果。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..services.config_store import AIConfig, ConfigStore
from .onboarding import OnboardingDialog
from .pages import ReaderPage, ReviewPage, SettingsPage, TodayPage, VocabularyPage


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: AIConfig,
        store: ConfigStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._store = store

        self.setWindowTitle("Daily English · 每日英语")
        self.setMinimumSize(980, 640)
        self.resize(1160, 760)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        self._today_page = TodayPage()
        self._reader_page = ReaderPage()
        self._vocabulary_page = VocabularyPage()
        self._review_page = ReviewPage()
        self._settings_page = SettingsPage(config, store)
        self._settings_page.reconfigure_requested.connect(self._open_reconfigure)

        for page in (
            self._today_page,
            self._reader_page,
            self._vocabulary_page,
            self._review_page,
            self._settings_page,
        ):
            self._stack.addWidget(page)

        root.addWidget(self._stack, 1)
        self.setCentralWidget(central)
        self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------ UI
    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(212)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 24, 16, 18)
        layout.setSpacing(6)

        title = QLabel("Daily English")
        title.setObjectName("appTitle")
        layout.addWidget(title)

        subtitle = QLabel("每天读一点，就会不一样")
        subtitle.setObjectName("appSubtitle")
        layout.addWidget(subtitle)
        layout.addSpacing(22)

        section = QLabel("学习")
        section.setObjectName("sidebarSection")
        layout.addWidget(section)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for index, text in enumerate(("今日", "阅读", "生词本", "复习")):
            layout.addWidget(self._nav_button(text, index))

        layout.addSpacing(18)
        settings_section = QLabel("其他")
        settings_section.setObjectName("sidebarSection")
        layout.addWidget(settings_section)
        layout.addWidget(self._nav_button("设置", 4))

        layout.addStretch(1)

        footer = QLabel(f"M0 · v{__version__}\n数据仅保存在本机")
        footer.setObjectName("sidebarFooter")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        return sidebar

    def _nav_button(self, text: str, index: int) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, i=index: self._stack.setCurrentIndex(i))
        self._nav_group.addButton(button, index)
        if index == 0:
            button.setChecked(True)
        return button

    # -------------------------------------------------------------- 配置变更
    def _open_reconfigure(self) -> None:
        dialog = OnboardingDialog(self._store, initial=self._config, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_config is not None:
            self._config = dialog.saved_config
            self._settings_page.apply_config(self._config)
