"""设置页：查看当前 AI 配置、测试连接、重新配置。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import today_str
from ...services.ai_client import AIClient, ConnectionResult
from ...services.app_context import AppContext
from ...services.config_store import AIConfig, ConfigError, ConfigStore, UiConfig
from ...services.export_service import ExportResult
from ...services.log_files import log_directory
from ..ai_usage_dialog import AIUsageDialog
from ..fonts import ENGLISH_FONT_CHOICES, get_english_font
from ..log_viewer_dialog import LogViewerDialog
from ..workers import TaskWorker
from .common import Card, PageHeader, set_status


class SettingsPage(QWidget):
    """配置展示与连接测试。

    重新配置弹窗由 MainWindow 统一管理，这里只发信号，避免页面之间互相依赖。
    """

    reconfigure_requested = Signal()
    # 英文字体切换：由主窗口负责重建全局 QSS 并同步阅读页。
    english_font_changed = Signal(str)

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
            PageHeader("设置", "AI 服务配置、调用记录与运行日志、阅读外观、数据导出与备份。")
        )
        layout.addWidget(self._build_ai_card())
        layout.addWidget(self._build_ai_usage_card())
        layout.addWidget(self._build_appearance_card())
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
        self._test_button.clicked.connect(self._test_connection)
        buttons.addWidget(self._test_button)

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

    def _build_ai_usage_card(self) -> Card:
        card = Card()
        title = QLabel("AI 使用与日志")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        self._usage_summary = QLabel("")
        self._usage_summary.setObjectName("muted")
        self._usage_summary.setWordWrap(True)
        card.body.addWidget(self._usage_summary)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._usage_button = QPushButton("查看调用记录…")
        self._usage_button.setObjectName("secondaryButton")
        self._usage_button.clicked.connect(self._open_usage)
        row.addWidget(self._usage_button)

        self._logs_button = QPushButton("查看运行日志…")
        self._logs_button.setObjectName("secondaryButton")
        self._logs_button.clicked.connect(self._open_logs)
        row.addWidget(self._logs_button)
        row.addStretch(1)
        card.body.addLayout(row)

        note = QLabel(
            "调用记录只保存任务、模型、token 用量、耗时与错误，不保存提示词和模型回复；"
            f"日志文件位于 {log_directory()}，按 1 MB 轮转、最多保留 4 个。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)

        if self._context is None:
            self._usage_button.setEnabled(False)
        self._refresh_usage_summary()
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

        self._font_combo = QComboBox()
        for choice in ENGLISH_FONT_CHOICES:
            self._font_combo.addItem(choice.label, choice.key)
        current_index = self._font_combo.findData(get_english_font(self._ui.english_font).key)
        # 先设好选中项再连信号，避免构造期间误触发一次保存。
        self._font_combo.setCurrentIndex(max(current_index, 0))
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

        note = QLabel("作用于整个应用里所有英文：界面、文章、测评、释义卡；中文统一使用系统雅黑。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
        return card

    def _on_english_font_changed(self, index: int) -> None:
        key = str(self._font_combo.itemData(index) or "")
        self._ui = UiConfig(english_font=key)
        try:
            self._store.save_ui(self._ui)
        except (ConfigError, OSError) as exc:
            # 保存失败不影响这次切换，只提示一下，下次启动会回到旧偏好。
            set_status(self._font_status, f"字体已切换，但偏好保存失败：{exc}", "error")
        else:
            set_status(self._font_status, "已保存，立即生效。", "hint")
        self.english_font_changed.emit(key)

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
        title = QLabel("数据导出")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        desc = QLabel(
            "导出全部学习数据：学习画像、文章、生词、高亮、复习记录与打卡统计。"
            "JSON 适合整体备份，CSV 每张表一个文件（Excel 可直接打开）。"
        )
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        card.body.addWidget(desc)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self._export_json_button = QPushButton("导出 JSON…")
        self._export_json_button.setObjectName("primaryButton")
        self._export_json_button.clicked.connect(self._export_json)
        buttons.addWidget(self._export_json_button)

        self._export_csv_button = QPushButton("导出 CSV…")
        self._export_csv_button.setObjectName("secondaryButton")
        self._export_csv_button.clicked.connect(self._export_csv)
        buttons.addWidget(self._export_csv_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)

        self._export_status = QLabel("")
        self._export_status.setObjectName("muted")
        self._export_status.setWordWrap(True)
        card.body.addWidget(self._export_status)

        if self._context is None:
            self._export_json_button.setEnabled(False)
            self._export_csv_button.setEnabled(False)
            set_status(self._export_status, "当前页面未连接数据库，导出不可用。", "hint")
        return card

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
    def on_activated(self) -> None:
        """切回设置页时刷新用量摘要（连接测试、其他页面调用 AI 后都会变化）。"""
        self._refresh_usage_summary()

    def _refresh_usage_summary(self) -> None:
        if self._context is None:
            self._usage_summary.setText("（未连接数据库，无法查看调用记录）")
            return
        repo = self._context.ai_call_repo
        today = repo.summary(since=today_str())
        total = repo.summary()
        self._usage_summary.setText(
            f"今日 {today.calls:,} 次调用（失败 {today.failed_calls:,}）· "
            f"{today.total_tokens:,} tokens；"
            f"累计 {total.calls:,} 次 · {total.total_tokens:,} tokens。"
        )

    def _open_usage(self) -> None:
        if self._context is None:
            return
        AIUsageDialog(self._context.ai_call_repo, parent=self).exec()
        self._refresh_usage_summary()

    def _open_logs(self) -> None:
        LogViewerDialog(parent=self).exec()

    def apply_config(self, config: AIConfig) -> None:
        """配置变更后刷新展示。"""
        self._config = config.normalized()
        self._url_value.setText(self._config.base_url)
        self._model_value.setText(self._config.model)
        self._key_value.setText(self._config.masked_key())
        set_status(self._status, "修改配置后建议先测试一次连接。", "hint")

    def _test_connection(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self._test_button.setEnabled(False)
        self._test_button.setText("测试中…")
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
        self._worker = None
        # 连接测试本身也会产生一条调用记录，摘要要跟着变。
        self._refresh_usage_summary()

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
