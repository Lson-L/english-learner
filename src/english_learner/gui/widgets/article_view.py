"""只读正文视图：双击取词、生词高亮、固定字号与行距。

实现要点：
- 用 QTextEdit（只读）而不是 QLabel，才有光标定位与 ExtraSelection；
- 双击时用 `cursorForPosition` + `WordUnderCursor` 拿到单词的字符区间，
  再把 (单词, 起点, 终点, 所在句子) 通过信号发给页面；
- 高亮按数据库里的字符偏移重绘，重开应用后位置不变；
- 句子提取是纯函数（services.dictionary_service.extract_sentence），可单测。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Signal
from PySide6.QtGui import QColor, QMouseEvent, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QTextEdit, QWidget

from ...models.entities import Article, Highlight
from ...services.dictionary_service import extract_sentence
from ..fonts import font_families

# 生词高亮的（背景, 前景）。浅色主题用浅黄底 + 深色字；暗色主题必须换成
# 暗琥珀底 + 亮色字，否则正文默认的浅灰字落在浅黄底上几乎看不清。
HIGHLIGHT_COLORS: dict[bool, tuple[str, str]] = {
    False: ("#FDE68A", "#111827"),
    True: ("#5C4A22", "#FFE9A8"),
}


class ArticleView(QTextEdit):
    """正文区域。`word_selected` 的参数：单词、起始偏移、结束偏移、所在句子。"""

    word_selected = Signal(str, int, int, str)
    # 点击正文空白 / 其他位置时发出，页面据此收起悬浮释义卡。
    background_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("articleView")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.document().setDocumentMargin(24)

        # 正文用全局英文字体 + 13pt ≈ 17px；走文档默认字体，避免 QSS 的全局 14px
        # 覆盖正文。中文字符由字体链回退到雅黑。具体字体由 set_english_font 决定。
        font = self.document().defaultFont()
        font.setFamilies(font_families())
        font.setPointSizeF(13.0)
        self.document().setDefaultFont(font)

        self._plain_text = ""
        self._highlights: tuple[Highlight, ...] = ()
        self._low_profile = False
        # 最近一次双击取词的位置（视口坐标），用于把悬浮卡放在单词附近。
        self._last_word_rect: QRect | None = None

    # ------------------------------------------------------------------ 内容
    def set_article(self, article: Article | None) -> None:
        self._plain_text = article.content if article is not None else ""
        self.setPlainText(self._plain_text)
        self._apply_line_height()
        self._apply_highlights()
        self.moveCursor(QTextCursor.MoveOperation.Start)

    def set_english_font(self, key: str | None = None, *, mono: bool = False) -> None:
        """切换正文字体（key 见 gui.fonts.ENGLISH_FONT_CHOICES）。

        只换族名，字号与行距保持原样；QSS 管不到 QTextDocument 的默认字体，
        所以设置页切换字体后要显式调这里。`mono=True` 用于低调主题的等宽正文。
        """
        font = self.document().defaultFont()
        font.setFamilies(font_families(key, mono=mono))
        self.document().setDefaultFont(font)

    def set_low_profile(self, low_profile: bool) -> None:
        """切换深色/浅色高亮配色，并重绘已有的生词标注。

        QTextDocument 的 ExtraSelection 不归 QSS 管，主题切换时必须显式重绘，
        否则暗色主题下会残留浅黄底 + 浅色字。
        """
        low_profile = bool(low_profile)
        if low_profile == self._low_profile:
            return
        self._low_profile = low_profile
        self._apply_highlights()

    def set_highlights(self, highlights: list[Highlight] | tuple[Highlight, ...]) -> None:
        self._highlights = tuple(highlights)
        self._apply_highlights()

    def highlight_count(self) -> int:
        """给测试用：当前实际绘制的高亮数量。"""
        return len(self.extraSelections())

    # ------------------------------------------------------------------ 交互
    def word_at_position(self, position: QPoint) -> tuple[str, int, int, str] | None:
        """把屏幕坐标解析成 (单词, 起点, 终点, 句子)；没有单词则返回 None。"""
        cursor = self.cursorForPosition(position)
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        text = cursor.selectedText().strip()
        if not text:
            return None
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        sentence = extract_sentence(self._plain_text, start, end)
        # cursorRect 只给光标位置，拼出单词首尾才能得到覆盖整词的矩形。
        start_cursor = QTextCursor(self.document())
        start_cursor.setPosition(start)
        end_cursor = QTextCursor(self.document())
        end_cursor.setPosition(end)
        self._last_word_rect = self.cursorRect(start_cursor).united(self.cursorRect(end_cursor))
        return text, start, end, sentence

    def last_word_rect(self) -> QRect | None:
        """最近一次取词的矩形（视口坐标）；没有取过词时为 None。"""
        return self._last_word_rect

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 约定
        # 单击正文任意位置先收起旧卡片；双击会紧接着触发新的查询。
        self.background_clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 约定
        found = self.word_at_position(event.position().toPoint())
        if found is not None:
            self.word_selected.emit(*found)
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------ 绘制
    def _apply_line_height(self) -> None:
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setLineHeight(180, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(block_format)

    def _apply_highlights(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        doc_length = len(self._plain_text)
        for highlight in self._highlights:
            start = max(0, min(highlight.start_offset, doc_length))
            end = max(start, min(highlight.end_offset, doc_length))
            if end <= start:
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            background, foreground = HIGHLIGHT_COLORS[self._low_profile]
            char_format = QTextCharFormat()
            char_format.setBackground(QColor(background))
            # 必须连前景色一起设：否则暗色主题下浅灰正文落在浅黄底上失去对比。
            char_format.setForeground(QColor(foreground))
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = char_format
            selections.append(selection)
        self.setExtraSelections(selections)
