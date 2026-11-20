"""复习页：卡片式间隔重复，四档反馈，完全本地运行。

两种正面（对应 HISTORY.md M2 / M3.16）：
- 看词回忆（默认）：正面给单词、音标和当初的上下文，先回忆再看答案；
- 拼写回忆：正面只给中文释义和挖空例句，输入英文后回车判分，再按 1~4 评分；
- 空格 / 回车翻面，翻面后用 1~4 或按钮选择「忘记 / 模糊 / 认识 / 太简单」；
- 一轮复习取今天所有到期词（最多 200 个），结束后显示本轮统计；
- 复习只做本地 SQLite 读写，不走 TaskWorker、不依赖网络。
"""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import Word
from ...services.app_context import AppContext
from ...services.config_store import (
    REVIEW_MODE_RECOGNITION,
    REVIEW_MODE_SPELLING,
    ConfigError,
)
from ...services.recall import (
    STATUS_CORRECT,
    STATUS_NEAR,
    STATUS_WRONG,
    RecallResult,
    SpellingPrompt,
    build_spelling_prompt,
    check_spelling,
)
from ...services.srs import (
    RATING_EASY,
    RATING_FORGOT,
    RATING_KNOWN,
    RATING_LABELS,
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

        # 复习模式来自 config.json 的 ui 段；会话中不允许切换。
        self._ui = context.store.load_ui()
        self._review_mode = self._ui.review_mode

        # 会话状态：_session 是本次排队的所有词，_index 指向当前卡片。
        self._session: list[Word] = []
        self._index = 0
        self._current: Word | None = None
        self._revealed = False
        self._reviewed = 0
        self._recalled = 0

        # 拼写模式当前卡片的题面与判分结果；None 表示没走拼写或已跳过。
        self._spelling_active = False
        self._prompt: SpellingPrompt | None = None
        self._recall_result: RecallResult | None = None

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
                "只复习今天到期的词：看词回忆或拼写回忆，再按 1~4 反馈记得牢不牢。全程离线可用。",
            )
        )

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._due_label = QLabel("")
        self._due_label.setObjectName("muted")
        status_row.addWidget(self._due_label)
        status_row.addStretch(1)

        mode_label = QLabel("复习模式")
        mode_label.setObjectName("muted")
        status_row.addWidget(mode_label)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("看词回忆", REVIEW_MODE_RECOGNITION)
        self._mode_combo.addItem("拼写回忆", REVIEW_MODE_SPELLING)
        # 先设好选中项再连信号，避免构造期间误触发一次保存。
        self._mode_combo.setCurrentIndex(max(self._mode_combo.findData(self._review_mode), 0))
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        status_row.addWidget(self._mode_combo)
        layout.addLayout(status_row)

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

        # 拼写模式的正脸：只给释义和挖空例句，原词 / 音标 / 原形都先藏起来。
        self._spelling_box = QWidget()
        spelling = QVBoxLayout(self._spelling_box)
        spelling.setContentsMargins(0, 0, 0, 0)
        spelling.setSpacing(10)

        self._prompt_hint = QLabel("")
        self._prompt_hint.setObjectName("muted")
        spelling.addWidget(self._prompt_hint)

        self._prompt_meaning = QLabel("")
        self._prompt_meaning.setObjectName("reviewPrompt")
        self._prompt_meaning.setWordWrap(True)
        spelling.addWidget(self._prompt_meaning)

        self._prompt_context = QLabel("")
        self._prompt_context.setObjectName("reviewContext")
        self._prompt_context.setWordWrap(True)
        spelling.addWidget(self._prompt_context)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self._spelling_input = QLineEdit()
        self._spelling_input.setPlaceholderText("输入英文单词后按回车")
        self._spelling_input.returnPressed.connect(self._submit_spelling)
        input_row.addWidget(self._spelling_input, 1)
        self._spelling_submit = QPushButton("提交（回车）")
        self._spelling_submit.setObjectName("primaryButton")
        self._spelling_submit.clicked.connect(self._submit_spelling)
        input_row.addWidget(self._spelling_submit)
        spelling.addLayout(input_row)

        self._spelling_result = QLabel("")
        self._spelling_result.setWordWrap(True)
        self._spelling_result.setVisible(False)
        spelling.addWidget(self._spelling_result)

        self._spelling_box.setVisible(False)
        box.addWidget(self._spelling_box)

        self._word_label = QLabel("")
        self._word_label.setObjectName("reviewWord")
        self._word_label.setWordWrap(True)
        box.addWidget(self._word_label)

        self._lemma_label = QLabel("")
        self._lemma_label.setObjectName("wordPhonetic")
        box.addWidget(self._lemma_label)

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

        self._mnemonic_label = QLabel("")
        self._mnemonic_label.setObjectName("wordMnemonic")
        self._mnemonic_label.setWordWrap(True)
        answer.addWidget(self._mnemonic_label)

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

    def _set_shortcuts_enabled(self, enabled: bool) -> None:
        """拼写输入阶段禁用全部快捷键，否则空格/回车/数字会被 QShortcut 抢走。"""
        for shortcut in self._shortcuts:
            shortcut.setEnabled(enabled)

    def _on_mode_changed(self, index: int) -> None:
        """切换复习模式并写入 ui 偏好；会话进行中不允许改。"""
        mode = str(self._mode_combo.itemData(index) or REVIEW_MODE_RECOGNITION)
        if mode == self._review_mode:
            return
        if self._is_session_active():
            self._mode_combo.setCurrentIndex(self._mode_combo.findData(self._review_mode))
            return

        self._review_mode = mode
        self._ui = replace(self._context.store.load_ui(), review_mode=mode)
        try:
            self._context.store.save_ui(self._ui)
        except (ConfigError, OSError) as exc:
            # 保存失败不影响本次切换，只提示一下，下次启动回到旧偏好。
            set_status(self._status, f"复习模式已切换，但偏好保存失败：{exc}", "error")
        else:
            set_status(self._status, "复习模式已保存。", "hint")

        if self._stack.currentWidget() is self._idle_page:
            self._update_idle_hint()

    def _update_idle_hint(self) -> None:
        if self._review_mode == REVIEW_MODE_SPELLING:
            self._idle_hint.setText("卡片正面只给中文释义和挖空例句，先拼出英文，再看答案核对。")
        else:
            self._idle_hint.setText("卡片正面只给单词和当初的上下文；想起来了再翻面。")

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
            self._update_idle_hint()
            self._start_button.setEnabled(True)

        self._current = None
        self._revealed = False
        self._spelling_active = False
        self._prompt = None
        self._recall_result = None
        self._mode_combo.setEnabled(True)
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
        self._mode_combo.setEnabled(False)
        set_status(self._status, "", "hint")
        self._stack.setCurrentWidget(self._card_page)
        self._show_current()
        # 拼写模式把焦点留在输入框，不能再用页面抢焦点。
        if not self._spelling_active:
            self.setFocus()

    def _show_current(self) -> None:
        word = self._session[self._index]
        self._current = word
        self._revealed = False
        self._recall_result = None
        self._prompt = (
            build_spelling_prompt(word) if self._review_mode == REVIEW_MODE_SPELLING else None
        )
        self._spelling_active = self._prompt is not None

        self._progress_label.setText(f"第 {self._index + 1} / {len(self._session)} 个")
        self._word_label.setText(word.display_form or word.lemma)
        if word.display_form and word.display_form.lower() != word.lemma.lower():
            self._lemma_label.setText(f"原形：{word.lemma}")
        else:
            self._lemma_label.setText("")
        self._lemma_label.setVisible(bool(self._lemma_label.text()))

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
        self._mnemonic_label.setText(f"记忆方法：{word.mnemonic}" if word.mnemonic else "")
        self._mnemonic_label.setVisible(bool(word.mnemonic))
        self._example_en_label.setText(word.example_en)
        self._example_en_label.setVisible(bool(word.example_en))
        self._example_cn_label.setText(word.example_cn)
        self._example_cn_label.setVisible(bool(word.example_cn))

        self._answer_box.setVisible(False)
        self._rating_bar.setVisible(False)
        self._rating_hint.setVisible(False)
        self._rating_hint.setText("按 1~4 或点击按钮：忘记 / 模糊 / 认识 / 太简单")

        if self._spelling_active:
            self._show_spelling_front(word)
        else:
            self._show_recognition_front(word)

    def _show_spelling_front(self, word: Word) -> None:
        """拼写模式的正脸：释义 + 挖空例句 + 输入框。"""
        prompt = self._prompt
        if prompt is None:
            return

        hint = "根据释义写出英文单词"
        if prompt.pos:
            hint = f"{hint} · {prompt.pos}"
        self._prompt_hint.setText(hint)
        self._prompt_meaning.setText(prompt.meaning)
        self._prompt_context.setText(prompt.masked_sentence)
        self._prompt_context.setVisible(bool(prompt.masked_sentence))

        self._spelling_result.clear()
        self._spelling_result.setVisible(False)
        self._spelling_input.clear()
        self._spelling_input.setReadOnly(False)
        self._spelling_input.setEnabled(True)
        self._spelling_submit.setVisible(True)
        self._spelling_submit.setEnabled(True)
        self._spelling_box.setVisible(True)

        # 正面不能露题：原词、原形、音标、原文都先藏起来。
        self._word_label.setVisible(False)
        self._lemma_label.setVisible(False)
        self._phonetic_label.setVisible(False)
        self._pos_label.setVisible(False)
        self._context_label.setVisible(False)

        self._reveal_button.setText("想不起来，直接看答案（跳过）")
        self._reveal_button.setVisible(True)
        self._set_shortcuts_enabled(False)
        self._spelling_input.setFocus()

    def _show_recognition_front(self, word: Word) -> None:
        """看词回忆的正脸：原词 + 音标 + 上下文。"""
        self._spelling_box.setVisible(False)
        self._word_label.setVisible(True)
        self._lemma_label.setVisible(bool(self._lemma_label.text()))
        self._phonetic_label.setVisible(bool(word.phonetic))
        self._pos_label.setVisible(bool(word.pos))
        self._context_label.setVisible(bool(word.context_sentence))
        self._reveal_button.setText("显示答案（空格）")
        self._reveal_button.setVisible(True)
        self._set_shortcuts_enabled(True)

    def _reveal(self) -> None:
        """翻面看释义；拼写模式下也用于“跳过输入直接看答案”。"""
        if self._stack.currentWidget() is not self._card_page:
            return
        if self._current is None or self._revealed:
            return
        self._revealed = True
        if self._spelling_active:
            self._reveal_spelling_answer()
        self._answer_box.setVisible(True)
        self._reveal_button.setVisible(False)
        self._rating_bar.setVisible(True)
        self._rating_hint.setVisible(True)
        self._set_shortcuts_enabled(True)

    def _reveal_spelling_answer(self) -> None:
        """拼写模式翻面：停用输入框，露出原词与音标。"""
        word = self._current
        if word is None:
            return
        self._spelling_input.setReadOnly(True)
        self._spelling_submit.setVisible(False)
        self._word_label.setVisible(True)
        self._lemma_label.setVisible(bool(self._lemma_label.text()))
        self._phonetic_label.setVisible(bool(word.phonetic))
        self._pos_label.setVisible(bool(word.pos))
        if self._recall_result is None:
            set_status(self._spelling_result, "已跳过输入，按实际记忆程度评分。", "hint")
            self._spelling_result.setVisible(True)

    def _submit_spelling(self) -> None:
        """判分当前拼写答案；结果只给建议，最终仍由用户按 1~4 确认。"""
        if self._stack.currentWidget() is not self._card_page:
            return
        if self._current is None or self._revealed or not self._spelling_active:
            return
        answer = self._spelling_input.text().strip()
        if not answer:
            set_status(self._status, "先输入一个答案，或点「跳过」直接看答案。", "hint")
            self._spelling_input.setFocus()
            return

        set_status(self._status, "", "hint")
        result = check_spelling(self._current, answer)
        self._recall_result = result
        self._reveal()

        expected = self._answer_display(self._current)
        if result.status == STATUS_CORRECT:
            set_status(self._spelling_result, f"拼写正确：{expected}", "success")
        elif result.status == STATUS_NEAR:
            set_status(
                self._spelling_result,
                f"差一点：你写的是「{result.answer}」，正确拼写是 {expected}",
                "warning",
            )
        elif result.status == STATUS_WRONG:
            set_status(
                self._spelling_result,
                f"不对：你写的是「{result.answer}」，正确拼写是 {expected}",
                "error",
            )
        self._spelling_result.setVisible(True)
        self._rating_hint.setText(
            f"建议评分：{RATING_LABELS[result.suggested_rating]}"
            f"（{result.suggested_rating}），按 1~4 确认"
        )

    @staticmethod
    def _answer_display(word: Word) -> str:
        display = (word.display_form or word.lemma).strip()
        lemma = word.lemma.strip()
        if display and lemma and display.lower() != lemma.lower():
            return f"{display}（原形：{lemma}）"
        return display or lemma

    def _rate(self, rating: int) -> None:
        """记录评分并前进到下一张；只有翻面后才接受评分。

        “忘记 / 模糊”会先弹窗收集原因，再让 AI 给针对性记忆帮助；
        用户取消弹窗则不记录本次评分，留在当前卡片。
        """
        if not self._revealed or self._current is None:
            return

        note = ""
        advice = ""
        if rating in (RATING_FORGOT, RATING_VAGUE):
            help_result = self._collect_review_help(rating)
            if help_result is None:
                return
            note, advice = help_result

        try:
            self._context.review_service().review(
                self._current,
                rating,
                note=note,
                advice=advice,
            )
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

    def _collect_review_help(self, rating: int) -> tuple[str, str] | None:
        """弹出复习帮助框，返回 (原因, AI 建议)；None 表示用户取消本次评分。

        单独抽成方法便于单测替换：测试里直接返回 ("", "") 即可跳过模态框。
        """
        if self._current is None:
            return None

        # 延迟导入：review_help_dialog 依赖 pages.common，而 pages/__init__ 又会
        # 导入本模块，放在模块顶层会形成循环导入。
        from ..review_help_dialog import ReviewHelpDialog

        dialog = ReviewHelpDialog(self._context, self._current, rating, parent=self)
        # 弹窗打开期间禁用 1~4 快捷键，避免输入原因时误触发评分。
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            for shortcut in self._shortcuts:
                shortcut.setEnabled(True)

        if not accepted:
            return None
        return dialog.result_reason, dialog.result_advice

    def _finish_session(self) -> None:
        self._current = None
        self._revealed = False
        self._spelling_active = False
        self._prompt = None
        self._recall_result = None
        self._mode_combo.setEnabled(True)
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
