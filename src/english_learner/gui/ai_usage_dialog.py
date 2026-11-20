"""AI 调用记录弹窗：看历史调用、token 用量与失败原因。

数据来自 `ai_calls` 表（每次真实 HTTP 尝试一行）。界面分三块：

1. 时间范围 + 汇总：调用次数、失败数、输入/输出/总 token、平均耗时；
2. 按任务聚合：哪类操作最费 token（查词、每日文章、阅读检测…）；
3. 明细致表：最近 500 条，可按任务筛选、只看失败。

全部是本地 SQLite 查询，不需要后台线程；token 只统计服务端返回了 usage 的调用，
部分兼容网关不返回 usage，此时显示为 “—”、汇总偏小。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models.entities import AICall, AIUsageByTask, AIUsageSummary
from ..storage.repositories import AICallRepo
from .pages.common import Card, divider

# 明细最多展示 500 条：再多的历史用导出 JSON 看。
DETAIL_LIMIT = 500

# (展示名, 天数)；None 表示不限时间（全部）。
RANGE_OPTIONS: tuple[tuple[str, int | None], ...] = (
    ("今天", 1),
    ("近 7 天", 7),
    ("近 30 天", 30),
    ("全部", None),
)

ERROR_KIND_LABELS: dict[str, str] = {
    "auth": "鉴权失败",
    "network": "网络不可达",
    "timeout": "超时",
    "rate_limit": "限流/额度",
    "not_found": "地址或模型不存在",
    "server": "服务端错误",
    "request": "请求参数",
    "config": "配置不完整",
    "format": "返回格式异常",
    "unknown": "未知错误",
}


class _CompactTable(QTableWidget):
    """表格默认 sizeHint 约 192px，会让外层滚动区误以为内容很高。

    这里只收敛 sizeHint（初始高度），不设 maximumHeight：窗口够高时表格
    仍然可以拉伸铺满，窗口较矮时由外层滚动区滚动。
    """

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 约定
        hint = super().sizeHint()
        return QSize(hint.width(), 100)


class AIUsageDialog(QDialog):
    """AI 调用历史与 token 统计。传入的是仓库，便于测试用临时数据库。"""

    def __init__(self, repo: AICallRepo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._metric_labels: dict[str, QLabel] = {}

        self.setWindowTitle("AI 调用记录 · Daily English")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setModal(True)
        self.setMinimumSize(900, 620)
        self.resize(1020, 700)
        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        title = QLabel("AI 调用记录")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "只记录调用元数据（任务、模型、token、耗时、错误），不保存提示词和模型回复。"
            "一次请求被重试或 JSON 模式降级时会产生多条记录，每条代表一次真实请求。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # 屏幕较矮时（高 DPI / 小笔记本）让内容滚动，而不是把汇总区压扁。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # ---------- 筛选栏 ----------
        filter_card = Card()
        filters = QHBoxLayout()
        filters.setSpacing(8)

        filters.addWidget(QLabel("时间范围"))
        self._range_combo = QComboBox()
        for label, _days in RANGE_OPTIONS:
            self._range_combo.addItem(label)
        self._range_combo.currentIndexChanged.connect(self.reload)
        filters.addWidget(self._range_combo)

        filters.addSpacing(10)
        filters.addWidget(QLabel("任务"))
        self._task_combo = QComboBox()
        self._task_combo.addItem("全部任务", "")
        self._task_combo.setMinimumWidth(180)
        self._task_combo.currentIndexChanged.connect(self.reload)
        filters.addWidget(self._task_combo)

        self._failed_check = QCheckBox("只看失败")
        self._failed_check.toggled.connect(self.reload)
        filters.addWidget(self._failed_check)

        filters.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.setObjectName("secondaryButton")
        refresh_button.clicked.connect(self.reload)
        filters.addWidget(refresh_button)
        filter_card.body.addLayout(filters)
        body_layout.addWidget(filter_card)

        # ---------- 汇总 ----------
        # 单行 6 个指标：比 3×2 少一行，窗口较矮时也不会被压扁。
        summary_card = Card()
        summary_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        summary_title = QLabel("汇总")
        summary_title.setObjectName("cardTitle")
        summary_card.body.addWidget(summary_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        metrics = (
            ("calls", "调用次数"),
            ("failed", "失败次数"),
            ("total_tokens", "总 tokens"),
            ("prompt_tokens", "输入 tokens"),
            ("completion_tokens", "输出 tokens"),
            ("avg_latency", "平均耗时 (ms)"),
        )
        for column, (key, caption) in enumerate(metrics):
            grid.addWidget(self._build_metric(key, caption), 0, column)
        # 六列等宽，数字之间的间距才均匀；否则列宽由标题文字撑开，看起来“歪”。
        for column in range(len(metrics)):
            grid.setColumnStretch(column, 1)
        summary_card.body.addLayout(grid)

        self._summary_note = QLabel("")
        self._summary_note.setObjectName("muted")
        self._summary_note.setWordWrap(True)
        summary_card.body.addWidget(self._summary_note)
        body_layout.addWidget(summary_card)

        # ---------- 按任务 ----------
        task_card = Card()
        task_title = QLabel("按任务")
        task_title.setObjectName("cardTitle")
        task_card.body.addWidget(task_title)
        self._task_table = self._make_table(
            ("任务", "调用", "失败", "总 tokens", "平均耗时 (ms)"),
            numeric_columns=(1, 2, 3, 4),
        )
        self._task_table.setMinimumHeight(80)
        self._task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        task_card.body.addWidget(self._task_table)
        body_layout.addWidget(task_card)

        # ---------- 明细 ----------
        detail_card = Card()
        detail_title = QLabel(f"最近 {DETAIL_LIMIT} 条明细")
        detail_title.setObjectName("cardTitle")
        detail_card.body.addWidget(detail_title)
        self._detail_table = self._make_table(
            ("时间", "任务", "模型", "输入", "输出", "总计", "耗时 (ms)", "状态"),
            numeric_columns=(3, 4, 5, 6),
        )
        self._detail_table.setMinimumHeight(120)
        self._detail_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._detail_table.setToolTip("鼠标悬停可查看 base_url、重试次数与错误详情")
        detail_card.body.addWidget(self._detail_table, 1)
        body_layout.addWidget(detail_card, 1)

        root.addWidget(divider())
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setWordWrap(True)
        footer.addWidget(self._status, 1)

        clear_button = QPushButton("清空记录")
        clear_button.setObjectName("secondaryButton")
        clear_button.clicked.connect(self._clear)
        footer.addWidget(clear_button)

        close_button = QPushButton("关闭")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

    def _build_metric(self, key: str, caption: str) -> QWidget:
        box = QWidget()
        box.setMinimumHeight(52)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        caption_label = QLabel(caption)
        caption_label.setObjectName("muted")
        layout.addWidget(caption_label)

        value_label = QLabel("0")
        value_label.setObjectName("usageValue")
        layout.addWidget(value_label)
        self._metric_labels[key] = value_label
        return box

    @staticmethod
    def _make_table(
        headers: tuple[str, ...], *, numeric_columns: tuple[int, ...] = ()
    ) -> _CompactTable:
        table = _CompactTable(0, len(headers))
        table.setObjectName("dataTable")
        table.setHorizontalHeaderLabels(list(headers))
        # 表头与单元格使用相同的对齐方式，数字列才不会有“头左尾右”的错位感。
        for column in numeric_columns:
            header_item = table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    # ---------------------------------------------------------------- 数据
    def _since(self) -> str | None:
        days = RANGE_OPTIONS[self._range_combo.currentIndex()][1]
        if days is None:
            return None
        return (date.today() - timedelta(days=days - 1)).isoformat()

    def _current_task(self) -> str:
        value = self._task_combo.currentData()
        return str(value or "")

    def reload(self) -> None:
        """按当前筛选条件重新查询并刷新整个弹窗。"""
        since = self._since()
        self._sync_task_options()

        summary = self._repo.summary(since=since)
        self._render_summary(summary)
        self._render_task_table(self._repo.by_task(since=since))
        rows = self._repo.list_recent(
            DETAIL_LIMIT,
            task=self._current_task() or None,
            only_failed=self._failed_check.isChecked(),
            since=since,
        )
        self._render_detail(rows, summary)

    def _sync_task_options(self) -> None:
        """任务下拉框跟随数据库里出现过的任务；尽量保留当前选择。"""
        current = self._current_task()
        options = self._repo.task_options()

        self._task_combo.blockSignals(True)
        self._task_combo.clear()
        self._task_combo.addItem("全部任务", "")
        for task in options:
            self._task_combo.addItem(task, task)
        index = self._task_combo.findData(current) if current else 0
        self._task_combo.setCurrentIndex(index if index >= 0 else 0)
        self._task_combo.blockSignals(False)

    def _render_summary(self, summary: AIUsageSummary) -> None:
        self._metric_labels["calls"].setText(f"{summary.calls:,}")
        self._metric_labels["failed"].setText(f"{summary.failed_calls:,}")
        self._metric_labels["total_tokens"].setText(f"{summary.total_tokens:,}")
        self._metric_labels["prompt_tokens"].setText(f"{summary.prompt_tokens:,}")
        self._metric_labels["completion_tokens"].setText(f"{summary.completion_tokens:,}")
        self._metric_labels["avg_latency"].setText(f"{summary.avg_latency_ms:,}")

        range_label = RANGE_OPTIONS[self._range_combo.currentIndex()][0]
        self._summary_note.setText(
            f"{range_label} · 成功 {summary.ok_calls:,} 次 · "
            f"失败 {summary.failed_calls:,} 次；汇总/按任务只受时间范围影响；"
            "token 以服务端 usage 为准，未返回不计入。"
        )

    def _render_task_table(self, rows: list[AIUsageByTask]) -> None:
        table = self._task_table
        table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = (
                item.task or "未标注",
                f"{item.calls:,}",
                f"{item.failed_calls:,}",
                f"{item.total_tokens:,}",
                f"{item.avg_latency_ms:,}",
            )
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if column > 0:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(row, column, cell)

    def _render_detail(self, rows: list[AICall], summary: AIUsageSummary) -> None:
        table = self._detail_table
        table.setRowCount(len(rows))
        for row, call in enumerate(rows):
            if call.ok:
                status = "成功"
            else:
                error_label = ERROR_KIND_LABELS.get(call.error_kind, call.error_kind or "未知")
                status = f"失败 · {error_label}"
            values = (
                _format_time(call.called_at),
                call.task or "未标注",
                call.model,
                _format_tokens(call.prompt_tokens),
                _format_tokens(call.completion_tokens),
                _format_tokens(call.total_tokens),
                f"{call.latency_ms:,}",
                status,
            )
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if column in (3, 4, 5, 6):
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                cell.setToolTip(self._tooltip(call))
                table.setItem(row, column, cell)

        visible = len(rows)
        suffix = f"（最多显示 {DETAIL_LIMIT} 条）" if visible >= DETAIL_LIMIT else ""
        if summary.calls == 0:
            self._status.setText(
                "当前范围内还没有调用记录。发起一次 AI 请求后，这里会出现 token 与耗时。"
            )
        else:
            failed_note = "，只看失败" if self._failed_check.isChecked() else ""
            self._status.setText(f"明细 {visible} 条{suffix}{failed_note}。")

    @staticmethod
    def _tooltip(call: AICall) -> str:
        parts = [
            f"时间：{call.called_at}",
            f"任务：{call.task or '未标注'}",
            f"模型：{call.model}",
            f"地址：{call.base_url}",
            f"第 {call.attempt} 次尝试 · JSON 模式：{'是' if call.json_mode else '否'}",
        ]
        if not call.ok:
            parts.append(
                f"错误：{ERROR_KIND_LABELS.get(call.error_kind, call.error_kind or '未知')}"
            )
            if call.error_message:
                parts.append(call.error_message)
        return "\n".join(parts)

    # ---------------------------------------------------------------- 操作
    def _clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空调用记录",
            "确定清空所有 AI 调用记录吗？\n只删除用量统计，不影响文章、生词、检测结果等学习数据。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self._repo.delete_all()
        self.reload()
        self._status.setText(f"已清空 {removed:,} 条调用记录。")


def _format_tokens(value: int | None) -> str:
    """网关没返回 usage 时显示为 “—”，避免误读成 0 token。"""
    return "—" if value is None else f"{value:,}"


def _format_time(value: str) -> str:
    """2026-11-19T14:32:05 → 11-19 14:32:05；格式不符时原样返回。"""
    text = str(value or "")
    if len(text) >= 19:
        return f"{text[5:10]} {text[11:19]}"
    return text or "—"


__all__ = ["AIUsageDialog"]
