"""生词本页：列表 + 筛选 + 搜索 + 排序 + 编辑释义 + 批量删除。

数据口径：
- 列表来自 `words` 表（查词即入库，所以可能包含查过但没点「加入」的词，
  这是 M1 已确认的行为，M2 不改数据结构）；
- 筛选按 SM-2 状态：未复习（new）/ 学习中（learning）/ 已掌握（mastered）；
- 编辑只改释义（中英文），不碰调度字段；删除会级联清理高亮与复习历史；
- M3 增加：排序、批量删除、未保存释义的脏检查（切页/筛选/排序前提示）。

全部是本地 SQLite 操作，毫秒级，直接在 UI 线程执行。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import ReviewLog, Word
from ...services.app_context import AppContext
from ...services.srs import RATING_LABELS
from ..meaning_text import display_meaning_text, flatten_meaning_text
from ..widgets import busy_progress_bar
from ..workers import TaskWorker, retire_worker
from .common import PageHeader, set_status

STATUS_LABELS: dict[str, str] = {
    "new": "未复习",
    "learning": "学习中",
    "mastered": "已掌握",
}

# 筛选下拉：显示文字 → words.status（None 表示全部）。
_STATUS_OPTIONS: tuple[tuple[str, str | None], ...] = (
    ("全部", None),
    ("未复习", "new"),
    ("学习中", "learning"),
    ("已掌握", "mastered"),
)

# 排序下拉：显示文字 → 排序 key（在内存里排，词库规模几百到几千足够快）。
_SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("最新加入", "created_desc"),
    ("最早加入", "created_asc"),
    ("字母顺序", "lemma"),
    ("到期日", "due"),
)

_COLUMNS = ("单词", "释义", "状态", "下次复习")

# 暂时隐藏英文释义编辑：数据仍保留，并继续参与 AI prompt / 复习；
# 需要恢复时把这里改成 True 即可。
SHOW_ENGLISH_MEANING = False

# 复习求助记录：详情面板只展示最近 5 条。
_HELP_LOG_LIMIT = 5
_HELP_LOG_EMPTY_HINT = (
    "还没有求助记录。复习时点「忘记 / 模糊」并请 AI 帮记后，你写的原因和 AI 的建议会显示在这里。"
)


class _MeaningEdit(QPlainTextEdit):
    """释义编辑框：高度随内容伸缩。

    默认保持 64px；义项变多时向上长，但最高 192px（约 3 倍），
    再长就在框内滚动，避免把右侧详情面板撑得过高。
    """

    MIN_HEIGHT = 64
    MAX_HEIGHT = 192

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(self.MIN_HEIGHT)
        self.textChanged.connect(self._fit_height)

    def _fit_height(self) -> None:
        # 用“不含滚动条”的完整内容宽度计算：如果按当前 viewport 宽度算，
        # 滚动条出现→宽度变窄→折行更多→高度再变大，会和 resizeEvent 来回抖动。
        frame = self.frameWidth()
        width = max(self.width() - 2 * frame, 1)
        doc = self.document()
        doc.setTextWidth(width)
        # QPlainTextEdit 的 documentSize().height() 不是像素高度（实测 2 行只返回 2.0），
        # 必须按文本块的 blockBoundingRect 逐块累加。
        layout = doc.documentLayout()
        content = 0.0
        block = doc.firstBlock()
        while block.isValid():
            content += layout.blockBoundingRect(block).height()
            block = block.next()
        # 文档边距 + 边框 + 2px 余量：避免 64 / 65 这种临界值反复出现滚动条。
        chrome = 2 * doc.documentMargin() + 2 * frame + 2
        target = int(content + chrome + 0.999)
        target = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, target))
        if target != self.height():
            self.setFixedHeight(target)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        super().resizeEvent(event)
        self._fit_height()


class VocabularyPage(QWidget):
    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._rows: list[Word] = []
        self._selected: Word | None = None
        # 详情面板的“原始释义”，用于判断是否有未保存的修改。
        self._baseline: tuple[str, str] | None = None
        self._last_filter_index = 0
        self._last_search_text = ""
        self._last_sort_index = 0
        self._worker: TaskWorker | None = None
        # 切换选中行时也可能有未保存释义：置位期间忽略 itemSelectionChanged，
        # 避免恢复选中行或保存后的 refresh() 再次触发询问。
        self._suppress_selection_check = False

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(14)

        self._header = PageHeader(
            "生词本",
            "阅读时标注过的词都在这里；可以筛选、搜索、修改释义或删除。",
        )
        layout.addWidget(self._header)
        layout.addLayout(self._build_toolbar())

        self._empty_label = QLabel("还没有生词。去「阅读」页双击一个单词，加入生词本吧。")
        self._empty_label.setObjectName("muted")
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_table())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)
        layout.addWidget(splitter, 1)

        self._status = QLabel("")
        layout.addWidget(self._status)

    # ------------------------------------------------------------------ 外观
    def set_low_profile(self, low_profile: bool) -> None:
        """低调模式下去掉页面顶部的「生词本」大字，说明文字保留。"""
        self._header.set_title_visible(not low_profile)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索单词或中文释义…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(240)
        self._search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_edit)

        self._filter_combo = QComboBox()
        for text, status in _STATUS_OPTIONS:
            self._filter_combo.addItem(text, status)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_combo)

        self._sort_combo = QComboBox()
        for text, key in _SORT_OPTIONS:
            self._sort_combo.addItem(text, key)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        toolbar.addWidget(self._sort_combo)

        self._batch_delete_button = QPushButton("删除选中")
        self._batch_delete_button.setObjectName("secondaryButton")
        self._batch_delete_button.setEnabled(False)
        self._batch_delete_button.clicked.connect(self._on_batch_delete_clicked)
        toolbar.addWidget(self._batch_delete_button)

        toolbar.addStretch(1)

        self._count_label = QLabel("")
        self._count_label.setObjectName("muted")
        toolbar.addWidget(self._count_label)
        return toolbar

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("wordTable")
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(False)
        self._table.setWordWrap(False)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        return self._table

    def _build_detail_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("wordPanel")
        panel.setFixedWidth(360)

        root = QVBoxLayout(panel)
        root.setContentsMargins(20, 20, 20, 18)
        root.setSpacing(10)

        self._detail_placeholder = QLabel("从左侧选择一个词\n查看并编辑释义")
        self._detail_placeholder.setObjectName("muted")
        self._detail_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_placeholder.setWordWrap(True)
        root.addWidget(self._detail_placeholder, 1)

        self._detail_body = QWidget()
        body = QVBoxLayout(self._detail_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        self._title_label = QLabel("")
        self._title_label.setObjectName("wordTitle")
        self._title_label.setWordWrap(True)
        body.addWidget(self._title_label)

        self._lemma_label = QLabel("")
        self._lemma_label.setObjectName("wordPhonetic")
        body.addWidget(self._lemma_label)

        phonetic_row = QHBoxLayout()
        phonetic_row.setSpacing(8)
        self._phonetic_label = QLabel("")
        self._phonetic_label.setObjectName("wordPhonetic")
        self._pos_label = QLabel("")
        self._pos_label.setObjectName("wordPos")
        phonetic_row.addWidget(self._phonetic_label)
        phonetic_row.addWidget(self._pos_label)
        phonetic_row.addStretch(1)
        body.addLayout(phonetic_row)

        body.addWidget(self._field_label("中文释义"))
        self._meaning_cn_edit = _MeaningEdit()
        self._meaning_cn_edit.setObjectName("meaningEdit")
        body.addWidget(self._meaning_cn_edit)

        self._meaning_en_label = self._field_label("英文释义")
        body.addWidget(self._meaning_en_label)
        self._meaning_en_edit = _MeaningEdit()
        self._meaning_en_edit.setObjectName("meaningEdit")
        body.addWidget(self._meaning_en_edit)
        # 英文释义暂时不展示；控件保留，避免隐藏后丢掉已有数据与脏检查逻辑。
        self._meaning_en_label.setVisible(SHOW_ENGLISH_MEANING)
        self._meaning_en_edit.setVisible(SHOW_ENGLISH_MEANING)

        self._srs_label = QLabel("")
        self._srs_label.setObjectName("muted")
        self._srs_label.setWordWrap(True)
        body.addWidget(self._srs_label)

        self._context_label = QLabel("")
        self._context_label.setObjectName("wordContext")
        self._context_label.setWordWrap(True)
        body.addWidget(self._context_label)

        self._source_label = QLabel("")
        self._source_label.setObjectName("muted")
        self._source_label.setWordWrap(True)
        body.addWidget(self._source_label)

        body.addWidget(self._field_label("记忆方法"))
        self._mnemonic_label = QLabel("")
        self._mnemonic_label.setObjectName("wordMnemonic")
        self._mnemonic_label.setWordWrap(True)
        body.addWidget(self._mnemonic_label)

        mnemonic_row = QHBoxLayout()
        mnemonic_row.setSpacing(8)
        self._mnemonic_button = QPushButton("AI 生成记忆方法")
        self._mnemonic_button.setObjectName("secondaryButton")
        self._mnemonic_button.setToolTip("生成中可以再次点击取消")
        self._mnemonic_button.clicked.connect(self._on_mnemonic_clicked)
        mnemonic_row.addWidget(self._mnemonic_button)
        self._mnemonic_progress = busy_progress_bar(width=90)
        mnemonic_row.addWidget(self._mnemonic_progress)
        mnemonic_row.addStretch(1)
        body.addLayout(mnemonic_row)

        # 复习时点“忘记 / 模糊”写下的原因和 AI 帮记内容，复习记录落在
        # review_logs.note / advice，这里回显最近几条，方便事后对照。
        body.addWidget(self._field_label("复习求助记录"))
        self._help_log_label = QLabel("")
        self._help_log_label.setObjectName("reviewHelpLog")
        self._help_log_label.setWordWrap(True)
        self._help_log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self._help_log_label)

        body.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._save_button = QPushButton("保存释义")
        self._save_button.setObjectName("primaryButton")
        self._save_button.clicked.connect(self._save_meaning)
        buttons.addWidget(self._save_button)

        self._delete_button = QPushButton("删除")
        self._delete_button.setObjectName("secondaryButton")
        self._delete_button.clicked.connect(self._on_delete_clicked)
        buttons.addWidget(self._delete_button)
        buttons.addStretch(1)
        body.addLayout(buttons)

        # 字段多（释义 / SRS / 原文 / 记忆方法 / 按钮），放进滚动区避免小窗口重叠。
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail_scroll.setWidget(self._detail_body)
        self._detail_scroll.setVisible(False)
        root.addWidget(self._detail_scroll, 1)
        return panel

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    # -------------------------------------------------------------- 生命周期
    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        selected_id = self._selected.id if self._selected is not None else None

        status = self._filter_combo.currentData()
        words = self._context.word_repo.list_all(status=status)
        keyword = self._search_edit.text().strip().lower()
        if keyword:
            words = [word for word in words if self._matches(word, keyword)]

        words = self._sort_words(words, str(self._sort_combo.currentData() or ""))
        self._rows = words
        self._count_label.setText(f"共 {len(words)} 个词")
        self._empty_label.setVisible(not words)
        self._rebuild_table(selected_id)

    @staticmethod
    def _matches(word: Word, keyword: str) -> bool:
        """只匹配单词原形与中文释义。

        英文释义不参与搜索：里面常有 something / sometimes 这类词，
        搜 "me" 会意外命中 detect，用户很难理解结果从哪来。
        """
        haystack = f"{word.lemma}\n{word.meaning_cn}".lower()
        return keyword in haystack

    @staticmethod
    def _sort_words(words: list[Word], key: str) -> list[Word]:
        if key == "created_asc":
            return sorted(words, key=lambda word: (word.created_at, word.id or 0))
        if key == "lemma":
            return sorted(words, key=lambda word: (word.lemma.lower(), word.id or 0))
        if key == "due":
            return sorted(
                words,
                key=lambda word: (word.due_date or "9999-12-31", word.lemma.lower()),
            )
        # 默认：最新加入（列表仓库已按此排序，这里再排一次保证切换排序后一致）
        return sorted(words, key=lambda word: (word.created_at, word.id or 0), reverse=True)

    # -------------------------------------------------------------- 筛选与排序
    def _on_filter_changed(self, index: int) -> None:
        if index == self._last_filter_index:
            return
        if not self.can_deactivate():
            self._restore_combo(self._filter_combo, self._last_filter_index)
            return
        self._last_filter_index = index
        self.refresh()

    def _on_sort_changed(self, index: int) -> None:
        if index == self._last_sort_index:
            return
        if not self.can_deactivate():
            self._restore_combo(self._sort_combo, self._last_sort_index)
            return
        self._last_sort_index = index
        self.refresh()

    def _on_search_changed(self, text: str) -> None:
        if text == self._last_search_text:
            return
        if not self.can_deactivate():
            self._search_edit.blockSignals(True)
            self._search_edit.setText(self._last_search_text)
            self._search_edit.blockSignals(False)
            return
        self._last_search_text = text
        self.refresh()

    @staticmethod
    def _restore_combo(combo: QComboBox, index: int) -> None:
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    # ------------------------------------------------------------------ 表格
    def _rebuild_table(self, selected_id: int | None) -> None:
        # 上千条词时逐行插入会触发大量重绘，先关掉更新再统一打开。
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(0)
            for row, word in enumerate(self._rows):
                self._table.insertRow(row)
                self._set_row(row, word)
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)

        target_row = self._row_of(selected_id)
        if target_row is not None:
            self._table.selectRow(target_row)
        elif self._rows:
            self._table.selectRow(0)
        else:
            self._table.clearSelection()
            self._clear_detail()
        self._update_batch_button()

    def _set_row(self, row: int, word: Word) -> None:
        form = word.display_form or word.lemma
        if form.lower() != word.lemma.lower():
            form = f"{form} → {word.lemma}"
        cells = (
            form,
            word.meaning_cn or word.meaning_en or "—",
            STATUS_LABELS.get(word.status, word.status),
            word.due_date or "—",
        )
        for column, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, word.id)
            self._table.setItem(row, column, item)

    def _row_of(self, word_id: int | None) -> int | None:
        if word_id is None:
            return None
        for row, word in enumerate(self._rows):
            if word.id == word_id:
                return row
        return None

    def _on_selection_changed(self) -> None:
        """选中的词条变化时刷新详情；有未保存释义先询问，避免静默丢失。"""
        self._update_batch_button()
        if self._suppress_selection_check:
            return
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rows):
            self._clear_detail()
            return

        target = self._rows[row]
        if (
            self._selected is not None
            and target.id != self._selected.id
            and self._has_unsaved_changes()
        ):
            action = self._ask_unsaved("当前词条的释义还没有保存。\n切换词条前要保存吗？")
            if action == "cancel":
                self._select_row_silently(self._selected.id)
                return
            if action == "save" and not self._save_meaning():
                self._select_row_silently(self._selected.id)
                return
            if action == "discard":
                self._reset_edits()
            # 保存会触发 refresh() 并把选中行带回旧词，这里重新定位到用户点的词。
            if self._row_of(target.id) is None:
                return
            self._select_row_silently(target.id)

        self._show_detail(target)

    def _select_row_silently(self, word_id: int | None) -> None:
        """按词条 id 选中行，但不触发详情切换（调用方随后自行决定展示哪条）。"""
        row = self._row_of(word_id)
        if row is None:
            return
        self._suppress_selection_check = True
        try:
            self._table.selectRow(row)
        finally:
            self._suppress_selection_check = False
        self._update_batch_button()

    def _selected_word_ids(self) -> list[int]:
        model = self._table.selectionModel()
        if model is None:
            return []
        rows = sorted({index.row() for index in model.selectedRows()})
        if not rows:
            row = self._table.currentRow()
            rows = [row] if row >= 0 else []

        ids: list[int] = []
        for row in rows:
            if 0 <= row < len(self._rows):
                word_id = self._rows[row].id
                if word_id is not None:
                    ids.append(word_id)
        return ids

    def _update_batch_button(self) -> None:
        count = len(self._selected_word_ids())
        self._batch_delete_button.setEnabled(count > 0)
        self._batch_delete_button.setText(f"删除选中 ({count})" if count else "删除选中")

    # ------------------------------------------------------------------ 详情
    def _show_detail(self, word: Word) -> None:
        self._selected = word
        self._detail_placeholder.setVisible(False)
        self._detail_scroll.setVisible(True)

        self._title_label.setText(word.display_form or word.lemma)
        if word.display_form and word.display_form.lower() != word.lemma.lower():
            self._lemma_label.setText(f"原形：{word.lemma}")
        else:
            self._lemma_label.setText("")
        self._lemma_label.setVisible(bool(self._lemma_label.text()))
        self._phonetic_label.setText(word.phonetic)
        self._phonetic_label.setVisible(bool(word.phonetic))
        self._pos_label.setText(word.pos)
        self._pos_label.setVisible(bool(word.pos))

        # 存储是单行（`1. ... 2. ...`），输入框里按义项换行展示；保存时再压回单行。
        meaning_cn_display = display_meaning_text(word.meaning_cn)
        self._meaning_cn_edit.setPlainText(meaning_cn_display)
        self._meaning_en_edit.setPlainText(word.meaning_en)
        self._baseline = (meaning_cn_display.strip(), word.meaning_en.strip())

        status_text = STATUS_LABELS.get(word.status, word.status)
        parts = [f"状态：{status_text}", f"易度 {word.ease:.2f}", f"间隔 {word.interval_days} 天"]
        if word.due_date:
            parts.append(f"下次复习 {word.due_date}")
        self._srs_label.setText(" · ".join(parts))

        self._context_label.setText(
            f"原文：{word.context_sentence}" if word.context_sentence else ""
        )
        self._context_label.setVisible(bool(word.context_sentence))

        self._source_label.setText(self._source_text(word))
        self._source_label.setVisible(bool(self._source_label.text()))

        self._mnemonic_label.setText(
            word.mnemonic or "还没有记忆方法，可以让 AI 根据释义和原文生成一个。"
        )
        self._mnemonic_button.setText("重新生成记忆方法" if word.mnemonic else "AI 生成记忆方法")
        self._render_help_logs(word)

    def _render_help_logs(self, word: Word) -> None:
        """展示复习求助记录：用户写的原因 + AI 帮记内容（最新在前）。"""
        if word.id is None:
            self._help_log_label.setText(_HELP_LOG_EMPTY_HINT)
            return

        records = self._context.review_repo.list_help_for_word(int(word.id), limit=_HELP_LOG_LIMIT)
        if not records:
            self._help_log_label.setText(_HELP_LOG_EMPTY_HINT)
            return

        text = "\n\n".join(self._format_help_log(log) for log in records)
        total = self._context.review_repo.count_help_for_word(int(word.id))
        if total > _HELP_LOG_LIMIT:
            text += f"\n\n更早的 {total - _HELP_LOG_LIMIT} 次求助已省略。"
        self._help_log_label.setText(text)

    @staticmethod
    def _format_help_log(log: ReviewLog) -> str:
        when = log.reviewed_at[:16].replace("T", " ") if log.reviewed_at else ""
        header = " · ".join(part for part in (when, RATING_LABELS.get(log.rating, "")) if part)
        lines = [header] if header else []
        if log.note:
            lines.append(f"我的输入：{log.note}")
        if log.advice:
            lines.append(f"AI 帮记：{log.advice}")
        return "\n".join(lines)

    def _clear_detail(self) -> None:
        self._selected = None
        self._baseline = None
        self._help_log_label.setText("")
        self._detail_scroll.setVisible(False)
        self._detail_placeholder.setVisible(True)
        self._update_batch_button()

    # -------------------------------------------------------------- 脏检查
    def _current_edits(self) -> tuple[str, str]:
        return (
            self._meaning_cn_edit.toPlainText().strip(),
            self._meaning_en_edit.toPlainText().strip(),
        )

    def _has_unsaved_changes(self) -> bool:
        if self._selected is None or self._baseline is None:
            return False
        return self._current_edits() != self._baseline

    def _reset_edits(self) -> None:
        if self._baseline is None:
            return
        self._meaning_cn_edit.setPlainText(self._baseline[0])
        self._meaning_en_edit.setPlainText(self._baseline[1])

    def can_deactivate(self) -> bool:
        """主窗口切页/关闭窗口前调用：有未保存释义时询问，返回是否允许离开。"""
        if not self._has_unsaved_changes():
            return True

        action = self._ask_unsaved("当前词条的释义还没有保存。\n离开前要保存吗？")
        if action == "save":
            return self._save_meaning()
        if action == "discard":
            self._reset_edits()
            return True
        return False

    def _ask_unsaved(self, message: str) -> str:
        """统一的三选一：返回 save / discard / cancel。

        切页、筛选、排序、切换词条都走这里，保证任何离开当前编辑的动作
        都不会静默丢掉用户输入。
        """
        answer = QMessageBox.question(
            self,
            "有未保存的释义",
            message,
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return "save"
        if answer == QMessageBox.StandardButton.Discard:
            return "discard"
        return "cancel"

    def _source_text(self, word: Word) -> str:
        if word.article_id is None:
            return ""
        article = self._context.article_repo.get_by_id(word.article_id)
        if article is None:
            return ""
        return f"出处：{article.title}（{article.article_date}）"

    # ------------------------------------------------------------------ 操作
    def _save_meaning(self) -> bool:
        """保存当前词条的释义；返回是否保存成功（can_deactivate 会用到）。"""
        word = self._selected
        if word is None or word.id is None:
            return False
        meaning_cn, meaning_en = self._current_edits()
        if not meaning_cn:
            set_status(self._status, "中文释义不能为空。", "error")
            return False

        updated = self._context.word_repo.update_meaning(
            word.id,
            # 输入框里为了可读性按义项换行，写库仍保持单行。
            meaning_cn=flatten_meaning_text(meaning_cn),
            meaning_en=meaning_en,
        )
        if updated is None:
            set_status(self._status, "词条已不存在，列表已刷新。", "error")
            self.refresh()
            return False

        self.refresh()
        name = updated.display_form or updated.lemma
        set_status(self._status, f"「{name}」的释义已保存。", "success")
        return True

    def _on_delete_clicked(self) -> None:
        word = self._selected
        if word is None or word.id is None:
            return
        name = word.display_form or word.lemma
        answer = QMessageBox.question(
            self,
            "删除生词",
            f"确定从生词本删除「{name}」吗？\n相关正文高亮和复习记录也会一起删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._delete_selected()

    def _delete_selected(self) -> None:
        """真正的删除动作（弹窗确认后的那一步，单测直接调用它）。"""
        word = self._selected
        if word is None or word.id is None:
            return
        name = word.display_form or word.lemma
        if not self._context.word_repo.delete(word.id):
            set_status(self._status, "删除失败：词条已不存在。", "error")
            self.refresh()
            return

        self._selected = None
        self.refresh()
        set_status(self._status, f"「{name}」已从生词本删除。", "success")

    # -------------------------------------------------------------- 记忆方法
    def _on_mnemonic_clicked(self) -> None:
        """生成中再点一次 = 取消；取消后旧请求转入后台，界面立即恢复。"""
        worker = self._worker
        if worker is not None and worker.isRunning():
            if retire_worker(worker):
                self._worker = None
                set_status(self._status, "已取消生成记忆方法。", "hint")
                self._on_mnemonic_finished()
            return
        self._generate_mnemonic()

    def _generate_mnemonic(self) -> None:
        word = self._selected
        if word is None or word.id is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if self._has_unsaved_changes():
            set_status(
                self._status,
                "请先保存或放弃当前释义修改，再生成记忆方法。",
                "hint",
            )
            return

        service = self._context.dictionary_service()
        level = self._context.level()
        # 保持按钮可点：生成中再点一次用于取消。
        self._mnemonic_button.setEnabled(True)
        self._mnemonic_button.setText("取消生成")
        self._mnemonic_progress.setVisible(True)
        set_status(self._status, "AI 正在生成记忆方法…", "loading")

        worker = TaskWorker(lambda: service.generate_mnemonic(word, level))
        worker.succeeded.connect(self._on_mnemonic_ready)
        worker.failed.connect(self._on_mnemonic_failed)
        worker.cancelled.connect(self._on_mnemonic_finished)
        worker.finished.connect(self._on_mnemonic_finished)
        self._worker = worker
        worker.start()

    def _on_mnemonic_ready(self, result: object) -> None:
        if not isinstance(result, Word):
            set_status(self._status, "记忆方法生成完成，但返回了意外数据。", "error")
            return
        self.refresh()
        set_status(self._status, "记忆方法已生成。", "success")

    def _on_mnemonic_failed(self, message: str) -> None:
        set_status(self._status, message, "error")

    def _on_mnemonic_finished(self) -> None:
        self._worker = None
        self._mnemonic_progress.setVisible(False)
        self._mnemonic_button.setEnabled(True)
        if self._selected is not None:
            self._mnemonic_button.setText(
                "重新生成记忆方法" if self._selected.mnemonic else "AI 生成记忆方法"
            )
        else:
            self._mnemonic_button.setText("AI 生成记忆方法")

    def _on_batch_delete_clicked(self) -> None:
        ids = self._selected_word_ids()
        if not ids:
            return
        answer = QMessageBox.question(
            self,
            "批量删除",
            f"确定删除选中的 {len(ids)} 个词吗？\n相关正文高亮和复习记录也会一起删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._delete_selected_words()

    def _delete_selected_words(self) -> int:
        """删除表格中选中的所有词（确认弹窗之后的那一步，单测直接调用）。"""
        ids = self._selected_word_ids()
        if not ids:
            return 0

        deleted = sum(1 for word_id in ids if self._context.word_repo.delete(word_id))
        self._selected = None
        self.refresh()
        if deleted:
            set_status(self._status, f"已删除 {deleted} 个词。", "success")
        else:
            set_status(self._status, "删除失败：选中的词已不存在。", "error")
        return deleted

    # ------------------------------------------------------------------ 关闭
    def shutdown(self, timeout_ms: int = 3000) -> TaskWorker | None:
        worker = self._worker
        if worker is None or worker.isFinished():
            return None
        if worker.stop(timeout_ms):
            return None
        return worker
