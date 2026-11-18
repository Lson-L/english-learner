"""设置页：查看当前 AI 配置、测试连接、重新配置。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services.ai_client import AIClient, ConnectionResult
from ...services.config_store import AIConfig, ConfigStore
from ..workers import TaskWorker
from .common import Card, PageHeader, set_status


class SettingsPage(QWidget):
    """配置展示与连接测试。

    重新配置弹窗由 MainWindow 统一管理，这里只发信号，避免页面之间互相依赖。
    """

    reconfigure_requested = Signal()

    def __init__(self, config: AIConfig, store: ConfigStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._store = store
        self._worker: TaskWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        layout.addWidget(PageHeader("设置", "AI 服务配置、数据位置与版本信息。"))
        layout.addWidget(self._build_ai_card())
        layout.addWidget(self._build_data_card())
        layout.addStretch(1)

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
        self._temperature_value = self._value_label("")

        form.addRow(self._form_label("服务地址"), self._url_value)
        form.addRow(self._form_label("模型 id"), self._model_value)
        form.addRow(self._form_label("API Key"), self._key_value)
        form.addRow(self._form_label("温度"), self._temperature_value)
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
        card.body.addLayout(form)

        open_button = QPushButton("打开配置目录")
        open_button.setObjectName("secondaryButton")
        open_button.clicked.connect(self._open_config_dir)
        row = QHBoxLayout()
        row.addWidget(open_button)
        row.addStretch(1)
        card.body.addLayout(row)

        note = QLabel(
            "M0 暂以明文 JSON 保存 API Key（文件在用户目录，不在 Git 仓库内）。"
            "计划在 M1 之后换成 Windows DPAPI 或系统 keyring。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.body.addWidget(note)
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
    def apply_config(self, config: AIConfig) -> None:
        """配置变更后刷新展示。"""
        self._config = config.normalized()
        self._url_value.setText(self._config.base_url)
        self._model_value.setText(self._config.model)
        self._key_value.setText(self._config.masked_key())
        self._temperature_value.setText(f"{self._config.temperature:g}")
        set_status(self._status, "修改配置后建议先测试一次连接。", "hint")

    def _test_connection(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self._test_button.setEnabled(False)
        self._test_button.setText("测试中…")
        set_status(self._status, "正在连接，请稍候…", "loading")

        config = self._config
        worker = TaskWorker(lambda: AIClient(config).test_connection())
        worker.succeeded.connect(self._on_test_succeeded)
        worker.failed.connect(self._on_test_failed)
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

    def _open_config_dir(self) -> None:
        directory = self._store.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
