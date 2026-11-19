"""右侧释义卡：展示音标、词性、双语释义、例句和上下文句子。

交互很克制：只有一个「加入生词本」按钮，是否可点由页面控制。
控件本身不发请求，只负责展示与发信号。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import Word


def _label(object_name: str, *, wrap: bool = True) -> QLabel:
    label = QLabel("")
    label.setObjectName(object_name)
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class WordPanel(QFrame):
    add_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wordPanel")
        self.setFixedWidth(380)
        self.setMinimumHeight(180)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 16)
        root.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addStretch(1)
        self._close_button = QPushButton("×")
        self._close_button.setObjectName("cardCloseButton")
        self._close_button.setFixedSize(24, 24)
        self._close_button.setToolTip("关闭释义卡")
        self._close_button.clicked.connect(self.close_requested.emit)
        top_row.addWidget(self._close_button)
        root.addLayout(top_row)

        self._placeholder = QLabel("双击正文中的英文单词\n查看语境释义")
        self._placeholder.setObjectName("muted")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        root.addWidget(self._placeholder, 1)

        self._detail = QWidget()
        detail = QVBoxLayout(self._detail)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(8)

        self._title = _label("wordTitle", wrap=False)
        detail.addWidget(self._title)

        self._lemma = _label("wordPhonetic", wrap=False)
        detail.addWidget(self._lemma)

        phonetic_row = QHBoxLayout()
        phonetic_row.setSpacing(8)
        self._phonetic = _label("wordPhonetic", wrap=False)
        self._pos = _label("wordPos", wrap=False)
        phonetic_row.addWidget(self._phonetic)
        phonetic_row.addWidget(self._pos)
        phonetic_row.addStretch(1)
        detail.addLayout(phonetic_row)

        self._meaning_cn = _label("wordMeaningCn")
        detail.addWidget(self._meaning_cn)

        self._meaning_en = _label("wordMeaningEn")
        detail.addWidget(self._meaning_en)

        line = QFrame()
        line.setObjectName("divider")
        line.setFrameShape(QFrame.Shape.HLine)
        detail.addWidget(line)

        self._example_en = _label("wordExampleEn")
        detail.addWidget(self._example_en)

        self._example_cn = _label("wordExampleCn")
        detail.addWidget(self._example_cn)

        self._context = _label("wordContext")
        detail.addWidget(self._context)

        self._mnemonic = _label("wordMnemonic")
        detail.addWidget(self._mnemonic)

        detail.addStretch(1)

        # 释义内容可能很长（例句 + 记忆方法），放进滚动区，卡片高度受限时也能看全。
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._detail)
        root.addWidget(self._scroll, 1)

        self._add_button = QPushButton("加入生词本")
        self._add_button.setObjectName("primaryButton")
        self._add_button.clicked.connect(self.add_requested.emit)
        root.addWidget(self._add_button)

        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._current: Word | None = None
        self.show_placeholder()

    # ------------------------------------------------------------------ 状态
    @property
    def current_word(self) -> Word | None:
        return self._current

    def show_placeholder(self) -> None:
        self._current = None
        self._placeholder.setVisible(True)
        self._scroll.setVisible(False)
        self._close_button.setVisible(False)
        self._add_button.setVisible(False)
        self._status.setText("")

    def show_loading(self, word: str) -> None:
        self._current = None
        self._placeholder.setVisible(False)
        self._scroll.setVisible(True)
        self._close_button.setVisible(True)
        self._title.setText(word)
        self._lemma.setText("")
        self._lemma.setVisible(False)
        self._phonetic.setText("")
        self._pos.setText("")
        self._meaning_cn.setText("正在查询…")
        self._meaning_en.setText("")
        self._example_en.setText("")
        self._example_cn.setText("")
        self._context.setText("")
        self._mnemonic.setText("")
        self._mnemonic.setVisible(False)
        self._add_button.setVisible(True)
        self._add_button.setEnabled(False)
        self._add_button.setText("查询中…")
        self._status.setText("")

    def show_definition(self, word: Word, sentence: str = "", *, cached: bool = False) -> None:
        self._current = word
        self._placeholder.setVisible(False)
        self._scroll.setVisible(True)
        self._close_button.setVisible(True)

        self._title.setText(word.display_form or word.lemma)
        if word.display_form and word.display_form.lower() != word.lemma.lower():
            self._lemma.setText(f"原形：{word.lemma}")
        else:
            self._lemma.setText("")
        self._lemma.setVisible(bool(self._lemma.text()))
        self._phonetic.setText(word.phonetic)
        self._phonetic.setVisible(bool(word.phonetic))
        self._pos.setText(word.pos)
        self._pos.setVisible(bool(word.pos))
        self._meaning_cn.setText(word.meaning_cn)
        self._meaning_cn.setVisible(bool(word.meaning_cn))
        self._meaning_en.setText(word.meaning_en)
        self._meaning_en.setVisible(bool(word.meaning_en))
        self._example_en.setText(word.example_en)
        self._example_en.setVisible(bool(word.example_en))
        self._example_cn.setText(word.example_cn)
        self._example_cn.setVisible(bool(word.example_cn))

        context = sentence or word.context_sentence
        self._context.setText(f"原文：{context}" if context else "")
        self._context.setVisible(bool(context))

        self._mnemonic.setText(f"记忆方法：{word.mnemonic}" if word.mnemonic else "")
        self._mnemonic.setVisible(bool(word.mnemonic))

        self._add_button.setVisible(True)
        self._add_button.setEnabled(True)
        if word.in_vocabulary:
            self._add_button.setText("加入本篇高亮")
            self._status.setText("这个词已在生词本中，加入后会在本篇高亮。")
        else:
            self._add_button.setText("加入生词本")
            self._status.setText("已有释义缓存，点「加入生词本」才会收进生词本。" if cached else "")

    def show_error(self, message: str) -> None:
        self._current = None
        self._placeholder.setVisible(False)
        self._scroll.setVisible(True)
        self._close_button.setVisible(True)
        self._title.setText("查询失败")
        self._lemma.setText("")
        self._lemma.setVisible(False)
        self._phonetic.setText("")
        self._pos.setText("")
        self._meaning_cn.setText(message)
        self._meaning_en.setText("")
        self._example_en.setText("")
        self._example_cn.setText("")
        self._context.setText("")
        self._mnemonic.setText("")
        self._mnemonic.setVisible(False)
        self._add_button.setVisible(False)
        self._status.setText("")

    def mark_added(self, inserted: bool = True) -> None:
        self._add_button.setEnabled(False)
        self._add_button.setText("已加入生词本")
        self._status.setText("已加入生词本，正文已高亮。" if inserted else "这个位置已经标注过了。")
