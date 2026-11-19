"""复习页：卡片式间隔重复，四档反馈，完全本地运行。

交互设计（对应 PROGRESS.md M2）：
- 正面只给单词、音标和当初的上下文句子，先回忆再看答案；
- 空格 / 回车翻面，翻面后用 1~4 或按钮选择「忘记 / 模糊 / 认识 / 太简单」；
- 一轮复习取今天所有到期词（最多 200 个），结束后显示本轮统计；
- 复习只做本地 SQLite 读写，不走 TaskWorker、不依赖网络。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import Word
from ...services.app_context import AppContext
from ...services.srs import (
    RATING_EASY,
    RATING_FORGOT,
    RATING_KNOWN,
    RATING_VAGUE,
)
from .common import PageHeader, set_status

logger = logging.getLogger(__name__)

# 一轮最多复习多少个词：防止积压太久时一次性加载过多。剩下的下一轮继续。
SESSION_LIMIT = 200

_RATING_SPECS: tuple[tuple[int, str, str], ...] = (
    (RATING_FORGOT, "忘记 (1)", "ratingForgot"),
    (RATING_VAGUE, "模糊 (2)", "ratingVague"),
    (RATING_KNOWN, "认识 (3)", "ratingKnown"),
    (RATING_EASY, "太简单 (4)", "ratingEasy"),
)


class ReviewPage(QWidget):
    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context

        # 会话状态：_session 是本次排队的所有词，_index 指向当前卡片。
        self._session: list[Word] = []
        self._index = 0
        self._current: Word | None = None
        self._revealed = False
        self._reviewed = 0
        self._recalled = 0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        self._install_shortcuts()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(14)

        layout.addWidget(
            PageHeader(
                "复习",
                "只复习今天到期的词：先回忆，再翻面，按 1~4 反馈记得牢不牢。全程离线可用。",
            )
        )

        self._due_label = QLabel("")
        self._due_label.setObjectName("muted")
        layout.addWidget(self._due_label)

        card = QFrame()
        card.setObjectName("reviewCard")
        card.setMinimumHeight(420)
        card_body = QVBoxLayout(card)
        card_body.setContentsMargins(32, 28, 32, 28)

        self._stack = QStackedWidget()
        self._idle_page = self._build_idle_page()
        self._card_page = self._build_card_page()
        self._summary_page = self._build_summary_page()
        for page in (self._idle_page, self._card_page, self._summary_page):
            self._stack.addWidget(page)
        card_body.addWidget(self._stack)

        layout.addWidget(card, 1)

        self._status = QLabel("")
        layout.addWidget(self._status)

    def _build_idle_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        box.addStretch(1)

        self._idle_title = QLabel("今天没有到期的词")
        self._idle_title.setObjectName("reviewIdleTitle")
        self._idle_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self._idle_title)

        self._idle_hint = QLabel("读完今天的文章、标几个新词，明天它们就会出现在这里。")
        self._idle_hint.setObjectName("muted")
        self._idle_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._idle_hint.setWordWrap(True)
        box.addWidget(self._idle_hint)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._start_button = QPushButton("开始复习")
        self._start_button.setObjectName("primaryButton")
        self._start_button.clicked.connect(self._start_session)
        button_row.addWidget(self._start_button)
        button_row.addStretch(1)
        box.addLayout(button_row)

        box.addStretch(1)
        return page

    def _build_card_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("muted")
        box.addWidget(self._progress_label)

        self._word_label = QLabel("")
        self._word_label.setObjectName("reviewWord")
        self._word_label.setWordWrap(True)
        box.addWidget(self._word_label)

        phonetic_row = QHBoxLayout()
        phonetic_row.setSpacing(10)
        self._phonetic_label = QLabel("")
        self._phonetic_label.setObjectName("wordPhonetic")
        self._pos_label = QLabel("")
        self._pos_label.setObjectName("wordPos")
        phonetic_row.addWidget(self._phonetic_label)
        phonetic_row.addWidget(self._pos_label)
        phonetic_row.addStretch(1)
        box.addLayout(phonetic_row)

        self._context_label = QLabel("")
        self._context_label.setObjectName("reviewContext")
        self._context_label.setWordWrap(True)
        box.addWidget(self._context_label)

        line = QFrame()
        line.setObjectName("divider")
        line.setFrameShape(QFrame.Shape.HLine)
        box.addWidget(line)

        self._answer_box = QWidget()
        answer = QVBoxLayout(self._answer_box)
        answer.setContentsMargins(0, 0, 0, 0)
        answer.setSpacing(8)

        self._meaning_cn_label = QLabel("")
        self._meaning_cn_label.setObjectName("reviewMeaning")
        self._meaning_cn_label.setWordWrap(True)
        answer.addWidget(self._meaning_cn_label)

        self._meaning_en_label = QLabel("")
        self._meaning_en_label.setObjectName("wordMeaningEn")
        self._meaning_en_label.setWordWrap(True)
        answer.addWidget(self._meaning_en_label)

        self._example_en_label = QLabel("")
        self._example_en_label.setObjectName("wordExampleEn")
        self._example_en_label.setWordWrap(True)
        answer.addWidget(self._example_en_label)

        self._example_cn_label = QLabel("")
        self._example_cn_label.setObjectName("wordExampleCn")
        self._example_cn_label.setWordWrap(True)
        answer.addWidget(self._example_cn_label)
        box.addWidget(self._answer_box)

        box.addStretch(1)

        self._reveal_button = QPushButton("显示答案（空格）")
        self._reveal_button.setObjectName("primaryButton")
        self._reveal_button.clicked.connect(self._reveal)
        box.addWidget(self._reveal_button)

        self._rating_bar = QWidget()
        rating_row = QHBoxLayout(self._rating_bar)
        rating_row.setContentsMargins(0, 0, 0, 0)
        rating_row.setSpacing(10)
        self._rating_buttons: dict[int, QPushButton] = {}
        for rating, text, object_name in _RATING_SPECS:
            button = QPushButton(text)
            button.setObjectName(object_name)
            button.clicked.connect(lambda _checked=False, r=rating: self._rate(r))
            self._rating_buttons[rating] = button
            rating_row.addWidget(button)
        box.addWidget(self._rating_bar)

        self._rating_hint = QLabel("按 1~4 或点击按钮：忘记 / 模糊 / 认识 / 太简单")
        self._rating_hint.setObjectName("muted")
        self._rating_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self._rating_hint)

        self._answer_box.setVisible(False)
        self._rating_bar.setVisible(False)
        self._rating_hint.setVisible(False)
        return page

    def _build_summary_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        box.addStretch(1)

        self._summary_title = QLabel("本轮复习完成")
        self._summary_title.setObjectName("reviewIdleTitle")
        self._summary_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self._summary_title)

        self._summary_detail = QLabel("")
        self._summary_detail.setObjectName("muted")
        self._summary_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._summary_detail.setWordWrap(True)
        box.addWidget(self._summary_detail)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._again_button = QPushButton("再检查一次到期词")
        self._again_button.setObjectName("secondaryButton")
        self._again_button.clicked.connect(self._start_session)
        button_row.addWidget(self._again_button)
        button_row.addStretch(1)
        box.addLayout(button_row)

        box.addStretch(1)
        return page

    def _install_shortcuts(self) -> None:
        """空格/回车翻面，1~4 评分；只在复习页及其子控件获得焦点时生效。"""
        self._shortcuts: list[QShortcut] = []
        for key in ("Space", "Return"):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._reveal)
            self._shortcuts.append(shortcut)

        for rating, _text, _object_name in _RATING_SPECS:
            shortcut = QShortcut(QKeySequence(str(rating)), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda r=rating: self._rate(r))
            self._shortcuts.append(shortcut)

    # -------------------------------------------------------------- 生命周期
    def on_activated(self) -> None:
        """主窗口切到本页时刷新；会话进行中切走再回来不打断。"""
        if self._is_session_active():
            return
        self.refresh()

    def refresh(self) -> None:
        """回到概览态：显示到期数量与今日已复习数。"""
        due_count = self._context.review_service().count_due()
        reviews_done = self._context.stats_repo.get().reviews_done
        self._due_label.setText(f"今天到期 {due_count} 词 · 今日已复习 {reviews_done} 词")

        if due_count == 0:
            self._idle_title.setText("今天没有到期的词")
            self._idle_hint.setText("读完今天的文章、标几个新词，明天它们就会出现在这里。")
            self._start_button.setEnabled(False)
        else:
            self._idle_title.setText(f"今天有 {due_count} 个词到期")
            self._idle_hint.setText("卡片正面只给单词和当初的上下文；想起来了再翻面。")
            self._start_button.setEnabled(True)

        self._current = None
        self._revealed = False
        self._stack.setCurrentWidget(self._idle_page)

    # ------------------------------------------------------------------ 会话
    def _is_session_active(self) -> bool:
        return (
            bool(self._session)
            and self._index < len(self._session)
            and self._stack.currentWidget() is self._card_page
        )

    def _start_session(self) -> None:
        words = self._context.review_service().due_words(limit=SESSION_LIMIT)
        if not words:
            self.refresh()
            set_status(self._status, "今天没有到期的词，读一篇文章再回来吧。", "hint")
            return

        self._session = words
        self._index = 0
        self._reviewed = 0
        self._recalled = 0
        self._current = None
        self._revealed = False
        set_status(self._status, "", "hint")
        self._stack.setCurrentWidget(self._card_page)
        self._show_current()
        self.setFocus()

    def _show_current(self) -> None:
        word = self._session[self._index]
        self._current = word
        self._revealed = False

        self._progress_label.setText(f"第 {self._index + 1} / {len(self._session)} 个")
        self._word_label.setText(word.display_form or word.lemma)

        self._phonetic_label.setText(word.phonetic)
        self._phonetic_label.setVisible(bool(word.phonetic))
        self._pos_label.setText(word.pos)
        self._pos_label.setVisible(bool(word.pos))

        self._context_label.setText(
            f"原文：{word.context_sentence}" if word.context_sentence else ""
        )
        self._context_label.setVisible(bool(word.context_sentence))

        self._meaning_cn_label.setText(word.meaning_cn)
        self._meaning_cn_label.setVisible(bool(word.meaning_cn))
        self._meaning_en_label.setText(word.meaning_en)
        self._meaning_en_label.setVisible(bool(word.meaning_en))
        self._example_en_label.setText(word.example_en)
        self._example_en_label.setVisible(bool(word.example_en))
        self._example_cn_label.setText(word.example_cn)
        self._example_cn_label.setVisible(bool(word.example_cn))

        self._answer_box.setVisible(False)
        self._reveal_button.setVisible(True)
        self._rating_bar.setVisible(False)
        self._rating_hint.setVisible(False)

    def _reveal(self) -> None:
        """翻面看释义；重复触发（比如按住空格）直接忽略。"""
        if self._stack.currentWidget() is not self._card_page:
            return
        if self._current is None or self._revealed:
            return
        self._revealed = True
        self._answer_box.setVisible(True)
        self._reveal_button.setVisible(False)
        self._rating_bar.setVisible(True)
        self._rating_hint.setVisible(True)

    def _rate(self, rating: int) -> None:
        """记录评分并前进到下一张；只有翻面后才接受评分。"""
        if not self._revealed or self._current is None:
            return
        try:
            self._context.review_service().review(self._current, rating)
        except Exception as exc:  # noqa: BLE001 - 本地写库失败不能让界面崩掉
            logger.exception("复习评分失败")
            set_status(self._status, f"保存复习进度失败：{exc}", "error")
            return

        self._reviewed += 1
        if rating >= RATING_KNOWN:
            self._recalled += 1

        self._index += 1
        if self._index >= len(self._session):
            self._finish_session()
        else:
            self._show_current()

    def _finish_session(self) -> None:
        self._current = None
        self._revealed = False
        total = self._reviewed
        recalled = self._recalled
        percent = round(recalled * 100 / total) if total else 0

        self._summary_title.setText("本轮复习完成")
        self._summary_detail.setText(
            f"复习了 {total} 个词，其中 {recalled} 个记得（{percent}%）。\n"
            "「忘记」和「模糊」的词会更早再次出现。"
        )
        self._stack.setCurrentWidget(self._summary_page)
        self._refresh_due_label()
        set_status(self._status, "复习进度已保存，今日打卡 +1。", "success")

    def _refresh_due_label(self) -> None:
        due_count = self._context.review_service().count_due()
        reviews_done = self._context.stats_repo.get().reviews_done
        self._due_label.setText(f"今天到期 {due_count} 词 · 今日已复习 {reviews_done} 词")
