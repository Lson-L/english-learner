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
from .i18n import refresh_buttons, set_low_profile
from .onboarding import OnboardingDialog
from .pages import ReaderPage, ReviewPage, SettingsPage, TodayPage, VocabularyPage
from .theme import build_stylesheet
from .weekly_review_dialog import WeeklyReviewDialog
from .window_state import capture_window_state, restore_window_state
from .workers import TaskWorker, wait_for_orphans

logger = logging.getLogger(__name__)

# 页面栈顺序与侧边栏按钮索引硬绑定：改这里必须同步改 _build_sidebar / _go。
PAGE_TODAY = 0
PAGE_READER = 1
PAGE_VOCABULARY = 2
PAGE_REVIEW = 3
PAGE_SETTINGS = 4

# 侧边栏与窗口文案：默认 / 低调主题两套。
# 低调模式刻意避开“英语”“生词本”这类字眼，导航保持英文短词，远看像一个
# 普通的本地工作台；窗口标题用中性的 "Workspace"，不冒充具体软件。
_SIDEBAR_TEXTS: dict[bool, dict[str, object]] = {
    False: {
        "window_title": "Daily English · 每日英语",
        "title": "Daily English",
        "subtitle": "每天读一点，就会不一样",
        "section": "学习",
        "other_section": "其他",
        "nav": ("今日", "阅读", "生词本", "复习"),
        "settings": "设置",
        "footer": None,
        "app_display_name": "Daily English",
    },
    True: {
        "window_title": "Workspace",
        "title": "Workspace",
        "subtitle": "local",
        "section": "WORKSPACE",
        "other_section": "SYSTEM",
        "nav": ("Today", "Docs", "Words", "Review"),
        "settings": "Settings",
        "footer": "local · offline",
        "app_display_name": "Workspace",
    },
}


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
        # 低调主题影响窗口标题、侧边栏文案与按钮语言；QSS 由 app.main() 按同一偏好生成。
        self._low_profile = self._ui.low_profile
        self._texts = _SIDEBAR_TEXTS[self._low_profile]
        # 全局开关必须在子控件创建前同步：之后构造的按钮会直接显示英文。
        set_low_profile(self._low_profile)

        self.setWindowTitle(str(self._texts["window_title"]))
        self.setMinimumSize(1080, 680)
        # 恢复上次关闭时的大小 / 位置 / 最大化状态；首次启动用默认尺寸。
        restore_window_state(self, self._store.load_window_state())

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
        self._settings_page.theme_changed.connect(self._apply_low_profile)
        # 全局 QSS 已在 app.main() 按偏好生成；这里只需同步正文文档字体与高亮配色。
        self._reader_page.apply_english_font(self._ui.english_font, mono=self._low_profile)
        self._reader_page.apply_low_profile(self._low_profile)
        self._vocabulary_page.set_low_profile(self._low_profile)

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
        # 构造参数写进 C++ 的初始文本不经过 setText，这里统一补一次翻译。
        refresh_buttons(self)

    # ------------------------------------------------------------------ UI
    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(212)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 24, 16, 18)
        layout.setSpacing(6)

        self._brand_label = QLabel(str(self._texts["title"]))
        self._brand_label.setObjectName("appTitle")
        layout.addWidget(self._brand_label)

        self._subtitle_label = QLabel(str(self._texts["subtitle"]))
        self._subtitle_label.setObjectName("appSubtitle")
        layout.addWidget(self._subtitle_label)
        layout.addSpacing(22)

        self._section_label = QLabel(str(self._texts["section"]))
        self._section_label.setObjectName("sidebarSection")
        layout.addWidget(self._section_label)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []
        for index, text in enumerate(self._texts["nav"]):
            button = self._nav_button(str(text), index)
            self._nav_buttons.append(button)
            layout.addWidget(button)

        layout.addSpacing(18)
        self._other_section_label = QLabel(str(self._texts["other_section"]))
        self._other_section_label.setObjectName("sidebarSection")
        layout.addWidget(self._other_section_label)
        self._settings_button = self._nav_button(str(self._texts["settings"]), PAGE_SETTINGS)
        layout.addWidget(self._settings_button)

        layout.addStretch(1)

        self._footer_label = QLabel(self._footer_text())
        self._footer_label.setObjectName("sidebarFooter")
        self._footer_label.setWordWrap(True)
        layout.addWidget(self._footer_label)
        return sidebar

    def _footer_text(self) -> str:
        """低调模式不展示版本号与“本地运行”这类学习产品话术。"""
        if self._low_profile:
            return str(self._texts["footer"])
        return f"v{__version__} · 本地运行\n数据仅保存在本机"

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
            app.setStyleSheet(build_stylesheet(key, low_profile=self._low_profile))
        self._reader_page.apply_english_font(key, mono=self._low_profile)

    def _apply_low_profile(self, low_profile: bool) -> None:
        """设置页切换主题：重建 QSS，并同步窗口标题与侧边栏文案。

        低调模式下英文字体固定为等宽，用户原先选的字体保留在配置里，
        关掉低调主题后自动恢复。
        """
        self._low_profile = bool(low_profile)
        self._texts = _SIDEBAR_TEXTS[self._low_profile]
        # 字体变化由 english_font_changed 同步；这里只更新主题字段，
        # 不重新读文件——保存失败时也不会把界面切回旧主题。
        self._ui = replace(self._ui, low_profile=self._low_profile)

        self.setWindowTitle(str(self._texts["window_title"]))
        self._brand_label.setText(str(self._texts["title"]))
        self._subtitle_label.setText(str(self._texts["subtitle"]))
        self._section_label.setText(str(self._texts["section"]))
        self._other_section_label.setText(str(self._texts["other_section"]))
        for button, text in zip(self._nav_buttons, self._texts["nav"], strict=False):
            button.setText(str(text))
        self._settings_button.setText(str(self._texts["settings"]))
        self._footer_label.setText(self._footer_text())

        # 按钮语言跟随主题：set_low_profile 决定后续新按钮，refresh 更新已有按钮。
        set_low_profile(self._low_profile)
        refresh_buttons(self)

        app = QApplication.instance()
        if app is not None:
            app.setApplicationDisplayName(str(self._texts["app_display_name"]))
            app.setStyleSheet(
                build_stylesheet(self._ui.english_font, low_profile=self._low_profile)
            )
        self._reader_page.apply_english_font(self._ui.english_font, mono=self._low_profile)
        self._reader_page.apply_low_profile(self._low_profile)
        self._vocabulary_page.set_low_profile(self._low_profile)

    # -------------------------------------------------------------- 配置变更
    def _open_reconfigure(self) -> None:
        dialog = OnboardingDialog(self._store, initial=self._config, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_config is not None:
            self._config = dialog.saved_config
            self._context.set_config(self._config)
            self._settings_page.apply_config(self._config)

    # ---------------------------------------------------------------- 关闭
    def save_window_state(self) -> None:
        """记住窗口几何。

        写盘失败只记日志：配置文件只读 / 磁盘满时用户仍然应该能关掉窗口。
        `closeEvent` 与 `app.main()`（`QApplication.quit()` 不触发 closeEvent）
        都会调用，重复写同一份数据没有副作用。
        """
        try:
            self._store.save_window_state(capture_window_state(self))
        except OSError as exc:
            logger.warning("保存窗口状态失败：%s", exc)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        current_page = self._pages[self._stack.currentIndex()]
        can_deactivate = getattr(current_page, "can_deactivate", None)
        if callable(can_deactivate) and not can_deactivate():
            event.ignore()
            return

        # 先存几何：后面的线程收尾可能等待网络超时，不应影响这次写入。
        self.save_window_state()
        self._stop_worker_threads()
        # 被“取消”的任务还在后台等 HTTP 返回；它们仍可能写库，先等完再关连接。
        wait_for_orphans()
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
