"""设置页：查看当前 AI 配置、测试连接、重新配置。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import today_str
from ...services.ai_client import AIClient, ConnectionResult
from ...services.app_context import AppContext
from ...services.backup_service import BACKUP_DIR_NAME, BACKUP_KEEP, backup_directory
from ...services.config_store import (
    MAX_DAILY_REVIEW_LIMIT,
    MIN_DAILY_REVIEW_LIMIT,
    AIConfig,
    ConfigError,
    ConfigStore,
)
from ...services.database_transfer import (
    DatabaseTransferError,
    discard_pending_import,
    export_database,
    stage_import,
)
from ...services.export_service import ExportResult
from ...services.log_files import log_directory
from ..app_restart import restart_application
from ..fonts import ENGLISH_FONT_CHOICES, get_english_font
from ..log_viewer_dialog import LogViewerDialog
from ..widgets import busy_progress_bar
from ..workers import TaskWorker, retire_worker
from .common import Card, PageHeader, set_status

logger = logging.getLogger(__name__)


class SettingsPage(QWidget):
    """配置展示与连接测试。

    重新配置弹窗由 MainWindow 统一管理，这里只发信号，避免页面之间互相依赖。
    """

    reconfigure_requested = Signal()
    # 英文字体切换：由主窗口负责重建全局 QSS 并同步阅读页。
    english_font_changed = Signal(str)
    # 主题切换（True = 低调深色）：由主窗口重建 QSS 并更新窗口/侧边栏文案。
    theme_changed = Signal(bool)

    def __init__(
        self,
        config: AIConfig,
        store: ConfigStore,
        context: AppContext | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._store = store
        self._context = context
        self._ui = store.load_ui()
        self._review = store.load_review()
        self._real_text = store.load_real_text()
        self._worker: TaskWorker | None = None

        # 卡片较多，小窗口下放进滚动区，避免内容被挤压或截断。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        layout.addWidget(
            PageHeader(
                "设置",
                "AI 服务配置、运行日志、阅读外观、复习节奏、数据导出与备份。"
                "学习效果与调用用量已移到统计页。",
            )
        )
        layout.addWidget(self._build_ai_card())
        layout.addWidget(self._build_log_card())
        layout.addWidget(self._build_appearance_card())
        layout.addWidget(self._build_reading_card())
        layout.addWidget(self._build_review_card())
        layout.addWidget(self._build_data_card())
        layout.addWidget(self._build_export_card())
        layout.addStretch(1)

        scroll.setWidget(container)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------ UI
    def _build_ai_card(self) -> Card:
        card = Card()

        title = QLabel("AI 服务")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)

        self._url_value = self._value_label("")
        self._model_value = self._value_label("")
        self._key_value = self._value_label("")

        form.addRow(self._form_label("服务地址"), self._url_value)
        form.addRow(self._form_label("模型 id"), self._model_value)
        form.addRow(self._form_label("API Key"), self._key_value)
        card.body.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self._test_button = QPushButton("测试连接")
        self._test_button.setObjectName("secondaryButton")
        self._test_button.setToolTip("测试中可再次点击取消")
        self._test_button.clicked.connect(self._on_test_clicked)
        buttons.addWidget(self._test_button)
        self._test_progress = busy_progress_bar(width=96)
        buttons.addWidget(self._test_progress)

        reconfigure_button = QPushButton("重新配置")
        reconfigure_button.setObjectName("primaryButton")
        reconfigure_button.clicked.connect(self.reconfigure_requested.emit)
        buttons.addWidget(reconfigure_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)

        self._status = QLabel("")
        card.body.addWidget(self._status)

        note = QLabel("API Key 仅保存在本机用户目录，不会上传到除你配置的服务商以外的任何地方。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)

        self.apply_config(self._config)
        return card

    def _build_log_card(self) -> Card:
        """运行日志入口；AI 调用用量已移到统计页。"""
        card = Card()
        title = QLabel("运行日志")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._logs_button = QPushButton("查看运行日志")
        self._logs_button.setObjectName("secondaryButton")
        self._logs_button.clicked.connect(self._open_logs)
        row.addWidget(self._logs_button)
        row.addStretch(1)
        card.body.addLayout(row)

        note = QLabel(
            f"日志文件位于 {log_directory()}，按 1 MB 轮转、最多保留 4 个；"
            "AI 调用用量与明细已移到统计页。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _build_appearance_card(self) -> Card:
        card = Card()
        title = QLabel("外观")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)

        self._theme_combo = QComboBox()
        self._theme_combo.addItem("默认（浅色）", False)
        self._theme_combo.addItem("低调（深色等宽）", True)
        # 先设好选中项再连信号，避免构造期间误触发一次保存。
        self._theme_combo.setCurrentIndex(1 if self._ui.low_profile else 0)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow(self._form_label("界面主题"), self._theme_combo)

        self._font_combo = QComboBox()
        for choice in ENGLISH_FONT_CHOICES:
            self._font_combo.addItem(choice.label, choice.key)
        current_index = self._font_combo.findData(get_english_font(self._ui.english_font).key)
        self._font_combo.setCurrentIndex(max(current_index, 0))
        # 低调主题强制等宽，字体选择此时不生效，置灰避免误解。
        self._font_combo.setEnabled(not self._ui.low_profile)
        self._font_combo.currentIndexChanged.connect(self._on_english_font_changed)
        form.addRow(self._form_label("英语字体"), self._font_combo)
        card.body.addLayout(form)

        preview = QLabel(
            "The quick brown fox jumps over the lazy dog. "
            "Small habits, repeated every day, slowly reshape who we become."
        )
        preview.setObjectName("passageText")
        preview.setWordWrap(True)
        card.body.addWidget(preview)

        self._font_status = QLabel("")
        card.body.addWidget(self._font_status)

        note = QLabel(
            "作用于整个应用里所有英文：界面、文章、测评、释义卡；中文统一使用系统雅黑。"
            "低调主题会改用深色等宽字体，并把窗口标题与侧边栏换成中性文案。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _on_theme_changed(self, index: int) -> None:
        low_profile = bool(self._theme_combo.itemData(index))
        self._ui = replace(self._ui, low_profile=low_profile)
        try:
            self._store.save_ui(self._ui)
        except (ConfigError, OSError) as exc:
            set_status(self._font_status, f"主题已切换，但偏好保存失败：{exc}", "error")
        else:
            set_status(self._font_status, "已保存，立即生效。", "hint")
        self._font_combo.setEnabled(not low_profile)
        self.theme_changed.emit(low_profile)

    def _on_english_font_changed(self, index: int) -> None:
        key = str(self._font_combo.itemData(index) or "")
        # 用 replace 只改字体字段，保留 UiConfig 里其它界面偏好。
        self._ui = replace(self._ui, english_font=key)
        try:
            self._store.save_ui(self._ui)
        except (ConfigError, OSError) as exc:
            # 保存失败不影响这次切换，只提示一下，下次启动会回到旧偏好。
            set_status(self._font_status, f"字体已切换，但偏好保存失败：{exc}", "error")
        else:
            set_status(self._font_status, "已保存，立即生效。", "hint")
        self.english_font_changed.emit(key)

    def _build_reading_card(self) -> Card:
        """阅读节奏：打开文章后是否后台预查生词。"""
        card = Card()
        title = QLabel("阅读")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)

        self._prefetch_check = QCheckBox("打开文章后自动预查生词")
        self._prefetch_check.setChecked(self._ui.prefetch_enabled)
        self._prefetch_check.toggled.connect(self._on_prefetch_toggled)
        form.addRow(self._form_label("查词预取"), self._prefetch_check)

        # M7.1：真实文章自动供给的总开关；关闭后阅读页仍可手动换回 AI。
        self._real_text_check = QCheckBox("自动安排真实文章")
        self._real_text_check.setChecked(self._real_text.auto_enabled)
        self._real_text_check.setToolTip("每天根据阅读与检测状态，自动安排一篇分级真实文章")
        self._real_text_check.toggled.connect(self._on_real_text_toggled)
        form.addRow(self._form_label("真实文章"), self._real_text_check)
        card.body.addLayout(form)

        self._prefetch_status = QLabel("")
        card.body.addWidget(self._prefetch_status)

        note = QLabel(
            "预取会在文章加载后，用一次批量请求查 10 个左右对当前等级偏难的词，"
            "写入本地查词缓存；之后双击这些词直接出释义，不再等待。"
            "只补缓存、不加入生词本，会额外消耗少量 token。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _on_real_text_toggled(self, checked: bool) -> None:
        self._real_text = replace(self._real_text, auto_enabled=bool(checked))
        try:
            self._real_text = self._store.save_real_text(self._real_text)
        except (ConfigError, OSError) as exc:
            set_status(self._prefetch_status, f"真实文章偏好保存失败：{exc}", "error")
        else:
            set_status(
                self._prefetch_status,
                (
                    "已保存；下一次启动时生效。"
                    if checked
                    else "已关闭自动安排；阅读页仍可手动换回 AI 文章。"
                ),
                "hint",
            )

    def _on_prefetch_toggled(self, checked: bool) -> None:
        self._ui = replace(self._ui, prefetch_enabled=bool(checked))
        try:
            self._ui = self._store.save_ui(self._ui)
        except (ConfigError, OSError) as exc:
            set_status(self._prefetch_status, f"预取偏好保存失败：{exc}", "error")
        else:
            set_status(self._prefetch_status, "已保存，下一次打开文章时生效。", "hint")

    def _build_review_card(self) -> Card:
        """复习节奏：每日目标与拼写输入开关。"""
        card = Card()
        title = QLabel("复习")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)

        self._daily_limit_spin = QSpinBox()
        self._daily_limit_spin.setRange(MIN_DAILY_REVIEW_LIMIT, MAX_DAILY_REVIEW_LIMIT)
        self._daily_limit_spin.setSuffix(" 个")
        self._daily_limit_spin.setValue(self._review.daily_limit)
        # 先设好值再连信号，避免构造期间误触发保存。
        self._daily_limit_spin.valueChanged.connect(self._on_daily_limit_changed)
        form.addRow(self._form_label("每日复习目标"), self._daily_limit_spin)

        self._spelling_check = QCheckBox("复习时要求默写英文单词")
        self._spelling_check.setChecked(self._review.spelling_enabled)
        self._spelling_check.toggled.connect(self._on_spelling_toggled)
        form.addRow(self._form_label("拼写输入"), self._spelling_check)
        card.body.addLayout(form)

        self._review_status = QLabel("")
        card.body.addWidget(self._review_status)

        note = QLabel(
            "每日目标只决定当天先复习多少个；没做完的到期词会留在积压里，"
            "可以随时加练，不会丢失进度。拼写默认关闭，先保证看到词能想起意思。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _on_daily_limit_changed(self, value: int) -> None:
        self._review = replace(self._review, daily_limit=int(value))
        self._save_review_preference()

    def _on_spelling_toggled(self, checked: bool) -> None:
        self._review = replace(self._review, spelling_enabled=bool(checked))
        self._save_review_preference()

    def _save_review_preference(self) -> None:
        try:
            self._review = self._store.save_review(self._review)
        except (ConfigError, OSError) as exc:
            set_status(self._review_status, f"复习偏好保存失败：{exc}", "error")
        else:
            set_status(self._review_status, "已保存，下一次开始复习时生效。", "hint")

    def _build_data_card(self) -> Card:
        card = Card()
        title = QLabel("数据与安全")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        path_value = self._value_label(str(self._store.path))
        form.addRow(self._form_label("配置文件"), path_value)
        db_value = self._value_label(
            str(self._context.db.path) if self._context is not None else "（未连接）"
        )
        form.addRow(self._form_label("数据库"), db_value)
        card.body.addLayout(form)

        open_button = QPushButton("打开配置目录")
        open_button.setObjectName("secondaryButton")
        open_button.clicked.connect(self._open_config_dir)
        row = QHBoxLayout()
        row.addWidget(open_button)
        row.addStretch(1)
        card.body.addLayout(row)

        note = QLabel(
            "API Key 在 Windows 上使用系统 DPAPI 加密后保存（与本机用户绑定，"
            "复制到其他电脑无法解密）；其他系统回退明文并收紧文件权限。"
            "配置和数据都在用户目录，不在 Git 仓库内。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _build_export_card(self) -> Card:
        card = Card()
        title = QLabel("数据导出与备份")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        desc = QLabel(
            "导出全部学习数据：学习画像、文章、生词、高亮、复习记录、回译练习与打卡统计。"
            "JSON 适合整体备份，CSV 每张表一个文件（Excel 可直接打开）。"
            f"应用每次启动会自动把数据库备份到 {BACKUP_DIR_NAME}/，保留最近 "
            f"{BACKUP_KEEP} 份。"
            "换电脑时用「导出数据库 / 导入数据库」整体搬运（导入覆盖本机数据、"
            "重启后生效）。"
        )
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        card.body.addWidget(desc)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self._export_json_button = QPushButton("导出 JSON")
        self._export_json_button.setObjectName("primaryButton")
        self._export_json_button.clicked.connect(self._export_json)
        buttons.addWidget(self._export_json_button)

        self._export_csv_button = QPushButton("导出 CSV")
        self._export_csv_button.setObjectName("secondaryButton")
        self._export_csv_button.clicked.connect(self._export_csv)
        buttons.addWidget(self._export_csv_button)

        self._backup_button = QPushButton("打开备份目录")
        self._backup_button.setObjectName("secondaryButton")
        self._backup_button.setToolTip(str(self._backup_directory()))
        self._backup_button.clicked.connect(self._open_backup_directory)
        buttons.addWidget(self._backup_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)

        # 整库搬运单独一行：导出是可直接导入的 SQLite 快照，导入在下次启动时覆盖本机。
        db_row = QHBoxLayout()
        db_row.setSpacing(10)
        self._export_db_button = QPushButton("导出数据库")
        self._export_db_button.setObjectName("secondaryButton")
        self._export_db_button.setToolTip("保存一个可直接在另一台电脑导入的 .db 快照")
        self._export_db_button.clicked.connect(self._export_database)
        db_row.addWidget(self._export_db_button)

        self._import_db_button = QPushButton("导入数据库")
        self._import_db_button.setObjectName("secondaryButton")
        self._import_db_button.setToolTip("覆盖本机数据：先校验，重启应用后生效，导入前自动备份")
        self._import_db_button.clicked.connect(self._import_database)
        db_row.addWidget(self._import_db_button)
        db_row.addStretch(1)
        card.body.addLayout(db_row)

        self._export_status = QLabel("")
        self._export_status.setObjectName("muted")
        self._export_status.setWordWrap(True)
        card.body.addWidget(self._export_status)

        if self._context is None:
            self._export_json_button.setEnabled(False)
            self._export_csv_button.setEnabled(False)
            self._export_db_button.setEnabled(False)
            self._import_db_button.setEnabled(False)
            set_status(self._export_status, "当前页面未连接数据库，导出不可用。", "hint")
        return card

    def _export_database(self) -> None:
        if self._context is None:
            return
        default_path = Path.home() / f"daily-english-{today_str()}.db"
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出数据库", str(default_path), "SQLite 数据库 (*.db);;所有文件 (*)"
        )
        if not filename:
            return
        try:
            target = export_database(filename, self._context.db.path)
        except (DatabaseTransferError, OSError) as exc:
            set_status(self._export_status, f"导出数据库失败：{exc}", "error")
            return
        set_status(self._export_status, f"已导出数据库：{target}", "success")

    def _import_database(self) -> None:
        if self._context is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "导入数据库", str(Path.home()), "SQLite 数据库 (*.db);;所有文件 (*)"
        )
        if not filename:
            return
        answer = QMessageBox.question(
            self,
            "导入数据库",
            "导入会覆盖本机全部学习数据，并在重启应用后生效。\n"
            "替换前会自动把当前数据库备份到 backups/。\n\n继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        db_path = self._context.db.path
        try:
            staging = stage_import(filename, db_path)
        except (DatabaseTransferError, OSError) as exc:
            set_status(self._export_status, f"导入数据库失败：{exc}", "error")
            return

        action = self._prompt_import_ready(staging)
        if action == "restart":
            self._restart_after_import()
        elif action == "rollback":
            discard_pending_import(db_path)
            set_status(
                self._export_status,
                "已回退：本次导入取消，本机数据保持原样。",
                "hint",
            )
        else:
            set_status(
                self._export_status,
                "数据库已暂存：下次启动应用时生效（重启前的新数据会被覆盖）。",
                "success",
            )

    def _prompt_import_ready(self, staging: Path) -> str:
        """导入校验通过后的选择：立即重启 / 回退 / 稍后。返回动作标识。"""
        box = QMessageBox(self)
        box.setWindowTitle("导入数据库")
        box.setText("数据库已校验并暂存，重启应用后生效。")
        box.setInformativeText(
            f"暂存位置：{staging}\n"
            "重启时会先把当前数据备份到 backups/，再替换成导入的数据库。\n"
            "选择「回退」将放弃本次导入，本机数据保持不变。"
        )
        restart_button = box.addButton("立即重启", QMessageBox.ButtonRole.AcceptRole)
        rollback_button = box.addButton("回退", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(restart_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is restart_button:
            return "restart"
        if clicked is rollback_button:
            return "rollback"
        return "later"

    def _restart_after_import(self) -> None:
        # 先释放数据库句柄：Windows 上文件被占用时，新进程无法替换 data.db。
        if self._context is not None:
            try:
                self._context.db.close()
            except Exception:  # noqa: BLE001 - 关闭失败也要继续尝试重启
                logger.debug("重启前关闭数据库失败", exc_info=True)
        if restart_application():
            # 新进程会在启动阶段应用暂存的数据库，本进程正常退出。
            QApplication.instance().quit()
        else:
            set_status(
                self._export_status,
                "无法自动重启：请手动关闭并重新打开应用，导入才会生效。",
                "error",
            )

    def _backup_directory(self) -> Path:
        """备份目录与数据库同级；没有上下文时按默认数据目录推算。"""
        db_path = self._context.db.path if self._context is not None else None
        return backup_directory(db_path)

    def _open_backup_directory(self) -> None:
        directory = self._backup_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            set_status(self._export_status, f"打开备份目录失败：{exc}", "error")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    @staticmethod
    def _form_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    @staticmethod
    def _value_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    # -------------------------------------------------------------- actions
    def _open_logs(self) -> None:
        LogViewerDialog(parent=self).exec()

    def apply_config(self, config: AIConfig) -> None:
        """配置变更后刷新展示。"""
        self._config = config.normalized()
        self._url_value.setText(self._config.base_url)
        self._model_value.setText(self._config.model)
        self._key_value.setText(self._config.masked_key())
        set_status(self._status, "修改配置后建议先测试一次连接。", "hint")

    def _on_test_clicked(self) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            if retire_worker(worker):
                self._worker = None
                self._on_test_finished()
                set_status(self._status, "已取消连接测试。", "hint")
            return
        self._test_connection()

    def _test_connection(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        # 保持按钮可点：测试中再点一次用于取消。
        self._test_button.setEnabled(True)
        self._test_button.setText("取消测试")
        self._test_progress.setVisible(True)
        set_status(self._status, "正在连接，请稍候…", "loading")

        config = self._config
        recorder = self._context.ai_call_repo if self._context is not None else None
        worker = TaskWorker(lambda: AIClient(config, recorder=recorder).test_connection())
        worker.succeeded.connect(self._on_test_succeeded)
        worker.failed.connect(self._on_test_failed)
        worker.cancelled.connect(self._on_test_finished)
        worker.finished.connect(self._on_test_finished)
        self._worker = worker
        worker.start()

    def _on_test_succeeded(self, result: object) -> None:
        if isinstance(result, ConnectionResult):
            set_status(
                self._status,
                f"连接成功 · {result.model} 响应正常（{result.latency_ms} ms）",
                "success",
            )
        else:
            set_status(self._status, "连接成功", "success")

    def _on_test_failed(self, message: str) -> None:
        set_status(self._status, message, "error")

    def _on_test_finished(self) -> None:
        self._test_button.setEnabled(True)
        self._test_button.setText("测试连接")
        self._test_progress.setVisible(False)
        self._worker = None

    # -------------------------------------------------------------- 数据导出
    def _export_json(self) -> None:
        if self._context is None:
            return
        default_path = Path.home() / f"daily-english-{today_str()}.json"
        filename, _selected = QFileDialog.getSaveFileName(
            self,
            "导出学习数据（JSON）",
            str(default_path),
            "JSON 文件 (*.json)",
        )
        if not filename:
            return
        service = self._context.export_service()
        self._run_export(lambda: service.export_json(Path(filename)), "正在导出 JSON…")

    def _export_csv(self) -> None:
        if self._context is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择导出目录", str(Path.home()))
        if not directory:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = Path(directory) / f"daily-english-export-{stamp}"
        service = self._context.export_service()
        self._run_export(lambda: service.export_csv(target), "正在导出 CSV…")

    def _run_export(self, task: Callable[[], object], loading_text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            set_status(self._export_status, "已有后台任务在进行，请稍后再试。", "hint")
            return

        self._set_export_busy(True)
        set_status(self._export_status, loading_text, "loading")
        worker = TaskWorker(task)
        worker.succeeded.connect(self._on_export_succeeded)
        worker.failed.connect(self._on_export_failed)
        worker.cancelled.connect(self._on_export_cancelled)
        worker.finished.connect(self._on_export_finished)
        self._worker = worker
        worker.start()

    def _on_export_succeeded(self, result: object) -> None:
        if not isinstance(result, ExportResult) or not result.files:
            set_status(self._export_status, "导出完成，但返回了意外数据。", "error")
            return
        location = result.files[0] if len(result.files) == 1 else result.files[0].parent
        set_status(
            self._export_status,
            f"导出完成：{result.summary()}\n保存位置：{location}",
            "success",
        )

    def _on_export_failed(self, message: str) -> None:
        set_status(self._export_status, message, "error")

    def _on_export_cancelled(self) -> None:
        set_status(self._export_status, "导出已取消。", "hint")

    def _on_export_finished(self) -> None:
        self._set_export_busy(False)
        self._worker = None

    def _set_export_busy(self, busy: bool) -> None:
        if self._context is None:
            return
        self._export_json_button.setEnabled(not busy)
        self._export_csv_button.setEnabled(not busy)

    # ------------------------------------------------------------------ 关闭
    def shutdown(self, timeout_ms: int = 3000) -> TaskWorker | None:
        """窗口关闭时收尾：测试连接 / 导出都共用 `_worker`。"""
        worker = self._worker
        if worker is None or worker.isFinished():
            return None
        if worker.stop(timeout_ms):
            return None
        return worker

    def _open_config_dir(self) -> None:
        directory = self._store.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
