"""运行日志查看器：在应用内看 `logs/app.log`，不用去翻用户目录。

只读展示，不修改日志文件；读取逻辑在 `services.log_files`（纯 Python，可单测）。
日志按 1 MB 轮转、最多 4 个文件，这里提供：

- 文件下拉框（app.log 与轮转备份，最新的在前）；
- 关键字按行筛选（例如 `AI 调用完成`、`error`、`查词`）；
- 手动刷新与 3 秒自动刷新（只在文件大小变化时重绘，避免闪烁）。

Java 对照：≈ 一个 `JDialog` 里放 `JTextArea` + `JComboBox`，数据来自
`LogFileReader`，不涉及任何网络或数据库。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services.log_files import (
    MAX_TAIL_BYTES,
    LogFileInfo,
    filter_log_text,
    list_log_files,
    log_directory,
    read_log_tail,
)
from .pages.common import Card, divider

# 自动刷新间隔：日志是给排查问题用的，3 秒足够，也不会频繁读盘。
AUTO_REFRESH_MS = 3000


class LogViewerDialog(QDialog):
    """只读日志查看器。`directory` 仅测试时传入，正常使用当前数据目录。"""

    def __init__(
        self,
        directory: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = Path(directory) if directory is not None else log_directory()
        self._files: tuple[LogFileInfo, ...] = ()
        self._raw_text = ""
        self._last_size: int | None = None

        self.setWindowTitle("运行日志 · Daily English")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setModal(True)
        self.setMinimumSize(860, 560)
        self.resize(960, 640)
        self._build_ui()
        self._refresh_files()

        self._timer = QTimer(self)
        self._timer.setInterval(AUTO_REFRESH_MS)
        self._timer.timeout.connect(self._auto_refresh)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(12)

        title = QLabel("运行日志")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel(
            "日志记录每次 AI 调用的 token 用量、耗时与错误，以及文章 / 查词 / "
            "检测等关键操作。按 1 MB 轮转，最多保留 4 个文件；这里只显示末尾 "
            f"{MAX_TAIL_BYTES // 1024} KB。"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        toolbar_card = Card()
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("文件"))
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(240)
        self._file_combo.currentIndexChanged.connect(self._on_file_changed)
        toolbar.addWidget(self._file_combo)

        toolbar.addSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("按关键字筛选行，例如：AI 调用完成、error、查词")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._search_edit, 1)

        self._refresh_button = QPushButton("刷新")
        self._refresh_button.setObjectName("secondaryButton")
        self._refresh_button.clicked.connect(lambda: self._refresh_files(force=True))
        toolbar.addWidget(self._refresh_button)

        self._auto_check = QCheckBox("自动刷新")
        self._auto_check.toggled.connect(self._on_auto_toggled)
        toolbar.addWidget(self._auto_check)
        toolbar_card.body.addLayout(toolbar)

        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setWordWrap(True)
        toolbar_card.body.addWidget(self._status)
        root.addWidget(toolbar_card)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setPlaceholderText("没有可显示的日志内容。")
        # 字体沿用全局字体链（gui/fonts.py + 全局 QSS），这里不单独设 font-family。
        root.addWidget(self._view, 1)

        root.addWidget(divider())
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._dir_label = QLabel(f"日志目录：{self._directory}")
        self._dir_label.setObjectName("muted")
        self._dir_label.setWordWrap(True)
        footer.addWidget(self._dir_label, 1)

        open_button = QPushButton("打开日志目录")
        open_button.setObjectName("secondaryButton")
        open_button.clicked.connect(self._open_directory)
        footer.addWidget(open_button)

        close_button = QPushButton("关闭")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

    # -------------------------------------------------------------- 文件操作
    def _refresh_files(self, *, force: bool = False) -> None:
        """重新扫描日志目录；保留当前选中的文件。"""
        current = self._current_path()
        self._files = list_log_files(self._directory)

        self._file_combo.blockSignals(True)
        self._file_combo.clear()
        for info in self._files:
            self._file_combo.addItem(f"{info.name} · {info.size_text} · {info.modified_at}")
            self._file_combo.setItemData(self._file_combo.count() - 1, str(info.path))
        self._file_combo.blockSignals(False)

        if not self._files:
            self._raw_text = ""
            self._last_size = None
            self._view.setPlainText("")
            self._view.setEnabled(False)
            self._status.setText(
                "日志目录里还没有日志文件。启动应用或发起一次 AI 调用后会自动生成。"
            )
            return

        self._view.setEnabled(True)
        index = self._index_of(current)
        target = self._files[index].path
        if target != current:
            # 选中的文件被删除/轮转后换成了另一个文件，大小比较没有意义。
            self._last_size = None
        self._file_combo.blockSignals(True)
        self._file_combo.setCurrentIndex(index)
        self._file_combo.blockSignals(False)
        self._load_file(force=force)

    def _current_path(self) -> Path | None:
        index = self._file_combo.currentIndex()
        if 0 <= index < self._file_combo.count():
            value = self._file_combo.itemData(index)
            if value:
                return Path(str(value))
        return None

    def _index_of(self, path: Path | None) -> int:
        if path is not None:
            for index, info in enumerate(self._files):
                if info.path == path:
                    return index
        return 0

    def _load_file(self, *, force: bool = False) -> None:
        path = self._current_path()
        if path is None:
            return
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        if not force and size is not None and size == self._last_size:
            return
        self._last_size = size
        self._raw_text = read_log_tail(path)
        self._render()

    def _render(self) -> None:
        keyword = self._search_edit.text() if hasattr(self, "_search_edit") else ""
        text = filter_log_text(self._raw_text, keyword)

        bar = self._view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        self._view.setPlainText(text)
        if at_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

        info = self._files[self._file_combo.currentIndex()] if self._files else None
        lines = len(text.splitlines())
        parts = []
        if info is not None:
            parts.append(f"{info.name} · {info.size_text}")
            if info.size_bytes > MAX_TAIL_BYTES:
                parts.append(f"仅显示末尾 {MAX_TAIL_BYTES // 1024} KB")
        if keyword.strip():
            parts.append(f"筛选“{keyword.strip()}”后 {lines} 行")
        else:
            parts.append(f"共 {lines} 行")
        parts.append("按行筛选，多行堆栈可能只保留匹配行")
        self._status.setText(" · ".join(parts))

    def _scroll_to_bottom(self) -> None:
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------ 事件
    def _on_file_changed(self, _index: int) -> None:
        self._last_size = None
        self._load_file(force=True)

    def _on_filter_changed(self, _text: str) -> None:
        self._render()

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            self._timer.start()
            self._auto_refresh()
        else:
            self._timer.stop()

    def _auto_refresh(self) -> None:
        self._refresh_files(force=False)

    def _open_directory(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._directory)))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        self._timer.stop()
        super().closeEvent(event)


__all__ = ["LogViewerDialog"]
