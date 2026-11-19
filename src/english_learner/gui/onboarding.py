"""首次启动向导：配置 AI → 测试连接 → 完成。

产品规则（用户明确要求）：
- 必须真实发一次请求，连接成功后才允许进入下一步；
- 修改任意配置字段都会让"已测试"状态失效，必须重新测试；
- 配置只保存到本机用户目录。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..services.ai_client import AIClient, ConnectionResult
from ..services.config_store import AIConfig, ConfigError, ConfigStore
from ..services.provider_presets import PRESETS
from .pages.common import Card, divider, set_status
from .workers import TaskWorker

STEP_TITLES = (
    ("欢迎使用 Daily English", "三步完成初始化，之后每天打开就能学。"),
    ("连接你的 AI 服务", "支持任何 OpenAI 兼容接口；连接测试通过后才能继续。"),
    ("配置完成", "配置已保存到本机。下一步将进行水平测评（M1）。"),
)


class OnboardingDialog(QDialog):
    """模态向导。成功后把结果放在 self.saved_config。"""

    def __init__(
        self,
        store: ConfigStore,
        initial: AIConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._initial = initial
        self._worker: TaskWorker | None = None
        self._tested_config: AIConfig | None = None
        self._test_result: ConnectionResult | None = None
        self._loading = True
        self.saved_config: AIConfig | None = None

        self.setWindowTitle("配置 AI 服务 · Daily English")
        self.setModal(True)
        self.setMinimumSize(760, 620)
        self.resize(760, 620)

        self._build_ui()
        self._prefill(initial)
        self._loading = False
        self._show_step(0)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(14)

        header_top = QHBoxLayout()
        self._step_badge = QLabel("")
        self._step_badge.setObjectName("stepBadge")
        header_top.addWidget(self._step_badge)
        header_top.addStretch(1)
        root.addLayout(header_top)

        self._step_title = QLabel("")
        self._step_title.setObjectName("pageTitle")
        root.addWidget(self._step_title)

        self._step_subtitle = QLabel("")
        self._step_subtitle.setObjectName("pageSubtitle")
        self._step_subtitle.setWordWrap(True)
        root.addWidget(self._step_subtitle)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome_page())
        self._stack.addWidget(self._build_config_page())
        self._stack.addWidget(self._build_done_page())
        root.addWidget(self._stack, 1)

        root.addWidget(divider())

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._back_button = QPushButton("上一步")
        self._back_button.setObjectName("secondaryButton")
        self._back_button.clicked.connect(self._go_back)
        footer.addWidget(self._back_button)
        footer.addStretch(1)

        self._primary_button = QPushButton("开始配置")
        self._primary_button.setObjectName("primaryButton")
        self._primary_button.clicked.connect(self._primary_clicked)
        footer.addWidget(self._primary_button)
        root.addLayout(footer)

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        card = Card()
        title = QLabel("用 AI 陪你每天读一点英语")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        for text in (
            "第 1 步：填写服务地址、API Key 和模型 id，也可以从常见服务商预设里选。",
            "第 2 步：点「测试连接」，会真实发一次极短的请求——成功后才能继续。",
            "第 3 步：完成配置进入应用。水平测评、每日文章、生词本将在 M1/M2 接入。",
        ):
            card.body.addWidget(self._bullet(text))
        layout.addWidget(card)

        note_card = Card()
        note_title = QLabel("关于隐私与费用")
        note_title.setObjectName("cardTitle")
        note_card.body.addWidget(note_title)
        for text in (
            "API Key 只保存在本机用户目录（Windows 为 %APPDATA%/EnglishLearner），"
            "不会进 Git 仓库。",
            "所有学习数据都留在本机；只有调用 AI 时才会把你的文本发给所配置的服务商。",
            "连接测试只发送一句极短的话，费用可以忽略。",
        ):
            note_card.body.addWidget(self._bullet(text))
        layout.addWidget(note_card)
        layout.addStretch(1)
        return page

    def _build_config_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        card = Card()
        form = QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._preset_combo = QComboBox()
        self._preset_combo.addItem("自定义")
        for preset in PRESETS:
            label = f"{preset.name}  ·  {preset.note}" if preset.note else preset.name
            self._preset_combo.addItem(label)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow(self._field_label("服务商预设"), self._preset_combo)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        form.addRow(self._field_label("服务地址"), self._url_edit)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self._key_edit = QLineEdit()
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText("sk-...")
        key_row.addWidget(self._key_edit, 1)
        self._show_key_check = QCheckBox("显示")
        self._show_key_check.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self._show_key_check)
        form.addRow(self._field_label("API Key"), key_row)

        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("deepseek-chat")
        form.addRow(self._field_label("模型 id"), self._model_edit)

        # 温度是内部按任务调的（出题 0.6、讲评 0.2…），不再暴露给用户，
        # 避免设置了一个实际上不生效的数值。
        self._temperature = 0.7

        card.body.addLayout(form)

        hint = QLabel(
            "提示：base_url 一般以 /v1 结尾（本地 Ollama 为 http://localhost:11434/v1）；"
            "模型 id 以服务商控制台为准。本项目只支持 OpenAI 兼容接口，"
            "若地址路径含 /anthropic 请换成该服务的 OpenAI 兼容地址"
            "（如 https://opencode.ai/inference/go/openai/v1）。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        card.body.addWidget(hint)
        layout.addWidget(card)

        test_card = Card()
        test_row = QHBoxLayout()
        test_row.setSpacing(12)
        self._test_button = QPushButton("测试连接")
        self._test_button.setObjectName("secondaryButton")
        self._test_button.clicked.connect(self._test_connection)
        test_row.addWidget(self._test_button)

        self._test_status = QLabel("")
        self._test_status.setWordWrap(True)
        test_row.addWidget(self._test_status, 1)
        test_card.body.addLayout(test_row)

        test_hint = QLabel("测试会真实调用一次模型；成功后才可点击下一步。")
        test_hint.setObjectName("muted")
        test_card.body.addWidget(test_hint)
        layout.addWidget(test_card)
        layout.addStretch(1)

        for edit in (self._url_edit, self._key_edit, self._model_edit):
            edit.textChanged.connect(self._invalidate_test)
        return page

    def _build_done_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        card = Card()
        title = QLabel("连接成功，配置已就绪")
        title.setObjectName("cardTitle")
        card.body.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._done_url = self._value_label("")
        self._done_model = self._value_label("")
        self._done_latency = self._value_label("")
        form.addRow(self._field_label("服务地址"), self._done_url)
        form.addRow(self._field_label("模型 id"), self._done_model)
        form.addRow(self._field_label("响应延迟"), self._done_latency)
        card.body.addLayout(form)
        layout.addWidget(card)

        next_card = Card()
        next_title = QLabel("接下来")
        next_title.setObjectName("cardTitle")
        next_card.body.addWidget(next_title)
        next_desc = QLabel(
            "M1 将在此配置基础上加入：固定题库的摸底测评（CEFR 定级）、"
            "每日文章生成与阅读器划词释义。主窗口已经搭好，可以先四处看看。"
        )
        next_desc.setObjectName("muted")
        next_desc.setWordWrap(True)
        next_card.body.addWidget(next_desc)
        layout.addWidget(next_card)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _field_label(text: str) -> QLabel:
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

    @staticmethod
    def _bullet(text: str) -> QLabel:
        label = QLabel(f"•  {text}")
        label.setObjectName("muted")
        label.setWordWrap(True)
        return label

    def _prefill(self, config: AIConfig | None) -> None:
        if config is None:
            # 默认选中并填好 DeepSeek，用户只需要粘一个 Key。
            self._preset_combo.setCurrentIndex(1)
            self._url_edit.setText(PRESETS[0].base_url)
            self._model_edit.setText(PRESETS[0].model)
            return
        self._url_edit.setText(config.base_url)
        self._key_edit.setText(config.api_key)
        self._model_edit.setText(config.model)
        self._temperature = config.temperature
        for index, preset in enumerate(PRESETS, start=1):
            if preset.base_url == config.base_url:
                self._preset_combo.setCurrentIndex(index)
                break

    def _on_preset_changed(self, index: int) -> None:
        if self._loading or index <= 0:
            return
        preset = PRESETS[index - 1]
        self._url_edit.setText(preset.base_url)
        self._model_edit.setText(preset.model)
        if preset.key == "ollama" and not self._key_edit.text().strip():
            self._key_edit.setText("ollama")
        self._invalidate_test()

    def _toggle_key_visibility(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self._key_edit.setEchoMode(mode)

    def _collect_config(self) -> AIConfig:
        return AIConfig(
            base_url=self._url_edit.text(),
            api_key=self._key_edit.text(),
            model=self._model_edit.text(),
            temperature=self._temperature,
        ).normalized()

    # --------------------------------------------------------------- 流程
    def _show_step(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._step_badge.setText(f"第 {index + 1} / 3 步")
        title, subtitle = STEP_TITLES[index]
        self._step_title.setText(title)
        self._step_subtitle.setText(subtitle)
        self._back_button.setVisible(index > 0)

        if index == 0:
            self._primary_button.setText("开始配置")
            self._primary_button.setEnabled(True)
        elif index == 1:
            self._primary_button.setText("下一步")
            self._primary_button.setEnabled(self._tested_config is not None)
        else:
            self._primary_button.setText("开始使用")
            self._primary_button.setEnabled(True)
            self._refresh_done_summary()

    def _go_back(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if self._stack.currentIndex() > 0:
            self._show_step(self._stack.currentIndex() - 1)

    def _primary_clicked(self) -> None:
        index = self._stack.currentIndex()
        if index == 0:
            self._show_step(1)
            self._url_edit.setFocus()
        elif index == 1:
            if self._tested_config is not None:
                self._show_step(2)
        else:
            self._save_and_accept()

    def _refresh_done_summary(self) -> None:
        config = self._tested_config
        if config is None:
            return
        self._done_url.setText(config.base_url)
        self._done_model.setText(config.model)
        if self._test_result is not None:
            self._done_latency.setText(f"{self._test_result.latency_ms} ms")
        else:
            self._done_latency.setText("—")

    # ----------------------------------------------------------- 连接测试
    def _test_connection(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        config = self._collect_config()
        errors = config.validate()
        if errors:
            set_status(self._test_status, "；".join(errors), "error")
            self._invalidate_test()
            return

        self._set_busy(True)
        set_status(self._test_status, f"正在连接 {config.base_url} …", "loading")

        worker = TaskWorker(lambda: AIClient(config).test_connection())
        worker.succeeded.connect(self._on_test_succeeded)
        worker.failed.connect(self._on_test_failed)
        worker.finished.connect(self._on_test_finished)
        self._worker = worker
        worker.start()

    def _on_test_succeeded(self, result: object) -> None:
        if not isinstance(result, ConnectionResult):
            set_status(self._test_status, "连接成功，但返回了意外数据。", "error")
            return
        self._tested_config = self._collect_config()
        self._test_result = result
        self._primary_button.setEnabled(True)
        self._refresh_done_summary()
        set_status(
            self._test_status,
            f"连接成功 · {result.model} 响应正常（{result.latency_ms} ms）",
            "success",
        )

    def _on_test_failed(self, message: str) -> None:
        self._tested_config = None
        self._test_result = None
        self._primary_button.setEnabled(False)
        set_status(self._test_status, message, "error")

    def _on_test_finished(self) -> None:
        self._set_busy(False)
        self._worker = None

    def _invalidate_test(self) -> None:
        """任何字段改动都会让之前的测试结果失效。"""
        if self._loading:
            return
        self._tested_config = None
        self._test_result = None
        if self._stack.currentIndex() == 1:
            self._primary_button.setEnabled(False)
        if hasattr(self, "_test_status"):
            set_status(self._test_status, "配置已修改，请重新测试连接。", "hint")

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self._preset_combo,
            self._url_edit,
            self._key_edit,
            self._model_edit,
            self._show_key_check,
            self._test_button,
            self._back_button,
        ):
            widget.setEnabled(not busy)
        self._primary_button.setEnabled(
            (not busy) and (self._tested_config is not None)
            if self._stack.currentIndex() == 1
            else not busy
        )

    # --------------------------------------------------------------- 保存
    def _save_and_accept(self) -> None:
        if self._tested_config is None:
            return
        try:
            self.saved_config = self._store.save(self._tested_config)
        except ConfigError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()

    def reject(self) -> None:  # noqa: D401 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "连接测试正在进行，请等待结果后再关闭。")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "连接测试正在进行，请等待结果后再关闭。")
            event.ignore()
            return
        super().closeEvent(event)
