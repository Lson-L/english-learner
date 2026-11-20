"""主窗口：左侧导航 + 右侧页面栈。

M1 的职责：
1. 组装 AppContext（配置 + 数据库 + AI 服务）；
2. 导航切换时通知页面刷新（on_activated）；
3. 首次没有 profile 时弹出摸底测评；
4. 承接“重新配置 AI”的结果，并同步给所有页面。
"""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
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
from ..services.app_context import AppContext
from ..services.config_store import AIConfig, ConfigStore
from ..storage.db import Database
from .assessment_dialog import AssessmentDialog
from .onboarding import OnboardingDialog
from .pages import ReaderPage, ReviewPage, SettingsPage, TodayPage, VocabularyPage
from .theme import build_stylesheet
from .weekly_review_dialog import WeeklyReviewDialog
from .workers import TaskWorker

logger = logging.getLogger(__name__)

# 页面栈顺序与侧边栏按钮索引硬绑定：改这里必须同步改 _build_sidebar / _go。
PAGE_TODAY = 0
PAGE_READER = 1
PAGE_VOCABULARY = 2
PAGE_REVIEW = 3
PAGE_SETTINGS = 4


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: AIConfig,
        store: ConfigStore,
        db: Database | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._store = store
        self._ui = store.load_ui()
        self._db = db if db is not None else Database()
        self._context = AppContext(config, store, self._db)
        self._assessment_dialog: AssessmentDialog | None = None

        self.setWindowTitle("Daily English · 每日英语")
        self.setMinimumSize(1080, 680)
        self.resize(1240, 800)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        self._today_page = TodayPage(self._context)
        self._reader_page = ReaderPage(self._context)
        self._vocabulary_page = VocabularyPage(self._context)
        self._review_page = ReviewPage(self._context)
        self._settings_page = SettingsPage(config, store, self._context)
        self._settings_page.reconfigure_requested.connect(self._open_reconfigure)
        self._settings_page.english_font_changed.connect(self._apply_english_font)
        # 全局 QSS 已在 app.main() 按偏好生成；这里只需同步正文文档字体。
        self._reader_page.apply_english_font(self._ui.english_font)

        self._today_page.read_requested.connect(lambda: self._go(PAGE_READER))
        self._today_page.assessment_requested.connect(self.run_assessment)
        self._today_page.review_requested.connect(lambda: self._go(PAGE_REVIEW))
        self._today_page.weekly_review_requested.connect(self.open_weekly_review)

        self._pages: tuple[QWidget, ...] = (
            self._today_page,
            self._reader_page,
            self._vocabulary_page,
            self._review_page,
            self._settings_page,
        )
        for page in self._pages:
            self._stack.addWidget(page)

        root.addWidget(self._stack, 1)
        self.setCentralWidget(central)
        self._go(PAGE_TODAY)

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
        layout.addWidget(self._nav_button("设置", PAGE_SETTINGS))

        layout.addStretch(1)

        footer = QLabel(f"M3.11 · v{__version__}\n数据仅保存在本机")
        footer.setObjectName("sidebarFooter")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        return sidebar

    def _nav_button(self, text: str, index: int) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, i=index: self._go(i))
        self._nav_group.addButton(button, index)
        return button

    def _go(self, index: int) -> None:
        """切换页面，并通知目标页面刷新数据。

        离开当前页前先问它 `can_deactivate()`（生词本用它拦截未保存的释义）；
        被拒绝时把导航按钮的选中状态恢复到当前页。
        """
        current_index = self._stack.currentIndex()
        if index != current_index:
            current_page = self._pages[current_index]
            can_deactivate = getattr(current_page, "can_deactivate", None)
            if callable(can_deactivate) and not can_deactivate():
                current_button = self._nav_group.button(current_index)
                if current_button is not None:
                    current_button.setChecked(True)
                return

        self._stack.setCurrentIndex(index)
        button = self._nav_group.button(index)
        if button is not None:
            button.setChecked(True)
        page = self._pages[index]
        on_activated = getattr(page, "on_activated", None)
        if callable(on_activated):
            on_activated()

    # ---------------------------------------------------------------- 测评
    def maybe_show_assessment(self) -> None:
        """首次没有 profile 时自动弹出测评；已有画像则忽略。"""
        if self._context.profile_repo.get() is None:
            self.run_assessment()

    def run_assessment(self) -> None:
        if self._assessment_dialog is not None:
            return
        dialog = AssessmentDialog(self._context, parent=self)
        self._assessment_dialog = dialog
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._assessment_dialog = None
        if accepted and dialog.result_profile is not None:
            self._today_page.refresh()

    # ---------------------------------------------------------------- 复盘
    def open_weekly_review(self) -> None:
        """打开每周复盘；用户应用建议后首页立即刷新等级。"""
        dialog = WeeklyReviewDialog(self._context, parent=self)
        dialog.exec()
        self._today_page.refresh()

    # -------------------------------------------------------------- 外观变更
    def _apply_english_font(self, key: str) -> None:
        """设置页切换英文字体：重建全局 QSS，并同步正文文档字体。

        QSS 换掉后 Qt 会自动重刷所有控件；但文章正文是 QTextDocument 的默认字体，
        不受 QSS 管，必须单独调 ArticleView.set_english_font。
        """
        self._ui = replace(self._ui, english_font=key)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(key))
        self._reader_page.apply_english_font(key)

    # -------------------------------------------------------------- 配置变更
    def _open_reconfigure(self) -> None:
        dialog = OnboardingDialog(self._store, initial=self._config, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_config is not None:
            self._config = dialog.saved_config
            self._context.set_config(self._config)
            self._settings_page.apply_config(self._config)

    # ---------------------------------------------------------------- 关闭
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        current_page = self._pages[self._stack.currentIndex()]
        can_deactivate = getattr(current_page, "can_deactivate", None)
        if callable(can_deactivate) and not can_deactivate():
            event.ignore()
            return

        self._stop_worker_threads()
        self._context.close()
        self._db.close()
        super().closeEvent(event)

    def _stop_worker_threads(self) -> None:
        """先请求取消并给几秒宽限；仍不结束就阻塞等待，避免 QThread 销毁警告。"""
        stuck: list[TaskWorker] = []
        for page in self._pages:
            shutdown = getattr(page, "shutdown", None)
            if not callable(shutdown):
                continue
            worker = shutdown()
            if worker is not None:
                stuck.append(worker)

        for worker in stuck:
            logger.warning("后台线程未在宽限期内结束，等待其网络超时退出…")
            worker.wait()
