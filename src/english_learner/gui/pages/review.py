"""复习页：卡片式间隔重复，四档反馈，完全本地运行。

卡片形态由 `services.review_plan` 自动编排，用户不再选择模式：
- 看词回忆：正面给单词、音标和当初的上下文，先回忆再看答案；
- 拼写回忆：正面只给中文释义和挖空例句，输入英文后回车判分；
- 看词 + 拼写（双阶段）：先看词回忆，翻面后遮住词形再默写一次，
  同一张卡只评分一次、只调度一次；
- 拼写输入是设置里的总开关，默认关闭，关闭时全部降级为看词回忆；
- 每天只取「目标 - 今日已完成」个词，默认目标 20；剩下的留在积压里，可加练；
- 每 10 张卡一个检查点，也可以随时「结束本轮」，已评分的卡片都已落库；
- 复习只做本地 SQLite 读写，不走 TaskWorker、不依赖网络。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import Word
from ...services.app_context import AppContext
from ...services.config_store import ReviewConfig
from ...services.recall import (
    STATUS_CORRECT,
    STATUS_NEAR,
    STATUS_WRONG,
    RecallResult,
    SpellingPrompt,
    build_spelling_prompt,
    check_spelling,
)
from ...services.review_plan import (
    CARD_DUAL,
    CARD_RECOGNITION,
    CARD_SPELLING,
    ReviewCard,
    build_session_plan,
)
from ...services.srs import (
    RATING_EASY,
    RATING_FORGOT,
    RATING_KNOWN,
    RATING_LABELS,
    RATING_VAGUE,
    VERDICT_SKIPPED,
)
from .common import PageHeader, set_status

logger = logging.getLogger(__name__)

# 一轮最多复习多少个词：防止积压太久时一次性加载过多。剩下的下一轮继续。
SESSION_LIMIT = 200

# 每复习这么多张卡停一次，给用户一个「继续 / 到此为止」的节点。
ROUND_SIZE = 10

_RATING_SPECS: tuple[tuple[int, str, str], ...] = (
    (RATING_FORGOT, "忘记 (1)", "ratingForgot"),
    (RATING_VAGUE, "模糊 (2)", "ratingVague"),
    (RATING_KNOWN, "认识 (3)", "ratingKnown"),
    (RATING_EASY, "太简单 (4)", "ratingEasy"),
)

_MODE_TITLES = {
    CARD_RECOGNITION: "看词回忆",
    CARD_SPELLING: "拼写回忆",
    CARD_DUAL: "看词 + 拼写",
}

_IDLE_HINT = (
    "系统会按每个词的学习阶段自动安排：新词先看词回忆，熟悉后加入拼写；"
    "忘记或写错的词会先认一遍再默写。"
)


class ReviewPage(QWidget):
    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context

        # 会话状态：_session 是本次排好的卡片，_index 指向当前卡片。
        self._session: list[ReviewCard] = []
        self._index = 0
        self._current: Word | None = None
        self._current_mode = CARD_RECOGNITION
        self._revealed = False
        self._reviewed = 0
        self._recalled = 0
        self._mode_counts = {CARD_RECOGNITION: 0, CARD_SPELLING: 0, CARD_DUAL: 0}

        # 分批关卡：_round_start 是本轮第一张卡的下标，_round_* 只统计本轮。
        self._round_start = 0
        self._round_no = 1
        self._round_reviewed = 0
        self._round_recalled = 0
        self._at_checkpoint = False

        # 拼写输入状态：题面、判分结果、输入框是否可编辑。
        self._prompt: SpellingPrompt | None = None
        self._recall_result: RecallResult | None = None
        self._spell_input_active = False

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build_ui()
        self._install_shortcuts()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        # 页头压缩：复习页的主体是中间卡片，标题区只留必要高度。
        layout.setContentsMargins(32, 18, 32, 18)
        layout.setSpacing(10)

        layout.addWidget(
            PageHeader(
                "",
                "只复习今天到期的词；系统自动在看词回忆与拼写回忆之间穿插，"
                "翻面后按 1~4 反馈记得牢不牢。全程离线可用。",
                compact=True,
            )
        )

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._due_label = QLabel("")
        self._due_label.setObjectName("mutedCompact")
        status_row.addWidget(self._due_label)
        status_row.addStretch(1)
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
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # 正文放进滚动区：窗口偏矮（高 DPI / 小屏 / 选 Literata 这类行高更大的字体）时，
        # 让内容滚动而不是被 QVBoxLayout 压扁。QLabel 开了 wordWrap 后 minimumSizeHint
        # 只有一行高，压扁时文字会被裁掉，所以这里不能靠标签自适应，必须给它足够的布局高度。
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        self._progress_label = QLabel("")
        self._progress_label.setObjectName("muted")
        progress_row.addWidget(self._progress_label)
        progress_row.addStretch(1)
        # 随时收工：已评分的卡片都已落库，不需要硬撑到整轮结束。
        self._end_button = QPushButton("结束本轮")
        self._end_button.setObjectName("linkButton")
        self._end_button.setToolTip("已复习的卡片都已保存，随时可以回来继续")
        self._end_button.clicked.connect(self._end_session_early)
        progress_row.addWidget(self._end_button)
        self._mode_label = QLabel("")
        self._mode_label.setObjectName("muted")
        progress_row.addWidget(self._mode_label)
        box.addLayout(progress_row)

        # 拼写正面 / 双阶段默写：只给释义和挖空例句，原词 / 音标 / 原形都藏起来。
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

        scroll = QScrollArea()
        scroll.setObjectName("reviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        # 卡片本身是白底，滚动区与内容容器都别再画自己的底色。
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        self._scroll = scroll
        outer.addWidget(scroll, 1)

        # 底部操作区固定在卡片底边，不随正文滚动，窗口再矮也能点。
        self._reveal_button = QPushButton("显示答案（空格）")
        self._reveal_button.setObjectName("primaryButton")
        self._reveal_button.clicked.connect(self._reveal)
        outer.addWidget(self._reveal_button)

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
        outer.addWidget(self._rating_bar)

        self._rating_hint = QLabel("按 1~4 或点击按钮：忘记 / 模糊 / 认识 / 太简单")
        self._rating_hint.setObjectName("muted")
        self._rating_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._rating_hint)

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
        button_row.setSpacing(10)
        button_row.addStretch(1)
        # 检查点：继续下一轮；总结页：继续复习 / 加练一轮。同一个按钮按状态分支。
        self._continue_button = QPushButton("继续")
        self._continue_button.setObjectName("primaryButton")
        self._continue_button.clicked.connect(self._continue_from_summary)
        button_row.addWidget(self._continue_button)
        # 只在检查点出现：结束本轮，直接看部分总结。
        self._finish_button = QPushButton("今天到此为止")
        self._finish_button.setObjectName("secondaryButton")
        self._finish_button.clicked.connect(self._finish_session)
        button_row.addWidget(self._finish_button)
        self._overview_button = QPushButton("回到概览")
        self._overview_button.setObjectName("secondaryButton")
        self._overview_button.clicked.connect(self.refresh)
        button_row.addWidget(self._overview_button)
        button_row.addStretch(1)
        box.addLayout(button_row)

        box.addStretch(1)
        return page

    def _install_shortcuts(self) -> None:
        """空格/回车翻面，1~4 评分；只在复习页及其子控件获得焦点时生效。"""
        self._shortcuts: list[QShortcut] = []
        self._reveal_shortcuts: list[QShortcut] = []
        self._rating_shortcuts: list[QShortcut] = []
        for key in ("Space", "Return"):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._reveal)
            self._reveal_shortcuts.append(shortcut)
            self._shortcuts.append(shortcut)

        for rating, _text, _object_name in _RATING_SPECS:
            shortcut = QShortcut(QKeySequence(str(rating)), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda r=rating: self._rate(r))
            self._rating_shortcuts.append(shortcut)
            self._shortcuts.append(shortcut)

    def _set_shortcuts_enabled(self, enabled: bool) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(enabled)

    def _set_reveal_shortcuts_enabled(self, enabled: bool) -> None:
        """空格/回车在拼写输入阶段必须禁用，否则会被 QShortcut 抢走。"""
        for shortcut in self._reveal_shortcuts:
            shortcut.setEnabled(enabled)

    def _set_rating_shortcuts_enabled(self, enabled: bool) -> None:
        for shortcut in self._rating_shortcuts:
            shortcut.setEnabled(enabled)

    # -------------------------------------------------------------- 生命周期
    def on_activated(self) -> None:
        """主窗口切到本页时刷新；会话进行中或停在检查点时切走再回来不打断。"""
        if self._is_session_active() or self._at_checkpoint:
            return
        self.refresh()

    def _review_config(self) -> ReviewConfig:
        """读取复习偏好；文件损坏时 ConfigStore 已经回退默认值。"""
        return self._context.store.load_review()

    def refresh(self) -> None:
        """回到概览态：显示今日目标与到期数量。"""
        review_config = self._review_config()
        goal = review_config.daily_limit
        due_count = self._context.review_service().count_due()
        reviews_done = self._context.stats_repo.get().reviews_done
        self._due_label.setText(
            f"今天到期 {due_count} 词 · 今日已复习 {reviews_done} / 目标 {goal}"
        )

        if due_count == 0:
            self._idle_title.setText("今天没有到期的词")
            self._idle_hint.setText("读完今天的文章、标几个新词，明天它们就会出现在这里。")
            self._start_button.setText("开始复习")
            self._start_button.setEnabled(False)
        elif reviews_done >= goal:
            batch = min(due_count, goal, SESSION_LIMIT)
            self._idle_title.setText("今日目标已完成")
            self._idle_hint.setText(
                f"还有 {due_count} 个到期词，可以加练一轮（{batch} 个）；不练也不会影响明天的安排。"
            )
            self._start_button.setText(f"加练一轮（{batch} 个）")
            self._start_button.setEnabled(True)
        else:
            remaining_goal = goal - reviews_done
            batch = min(due_count, remaining_goal, SESSION_LIMIT)
            self._idle_title.setText(f"今天先复习 {batch} 个词")
            base_hint = (
                _IDLE_HINT
                if review_config.spelling_enabled
                else "系统按到期顺序安排看词回忆：先想意思，翻面后按 1~4 评分；"
                "忘记或模糊的词会更早再次出现。"
            )
            if due_count > batch:
                hint = (
                    f"{base_hint}\n今天到期 {due_count} 个，本轮先做 {batch} 个；"
                    f"完成每日目标还差 {remaining_goal} 个，剩下的会留在积压里。"
                )
            elif due_count < remaining_goal:
                hint = (
                    f"{base_hint}\n今天到期 {due_count} 个，全部做完即可；"
                    f"距离每日目标还差 {remaining_goal} 个（暂时没有更多到期词）。"
                )
            else:
                hint = (
                    f"{base_hint}\n今天到期 {due_count} 个，完成每日目标还差 {remaining_goal} 个。"
                )
            if not review_config.spelling_enabled:
                hint += "\n当前只做看词回忆；需要默写可在「设置 → 复习」里打开拼写输入。"
            self._idle_hint.setText(hint)
            self._start_button.setText("开始复习")
            self._start_button.setEnabled(True)

        self._current = None
        self._current_mode = CARD_RECOGNITION
        self._revealed = False
        self._prompt = None
        self._recall_result = None
        self._spell_input_active = False
        # 回到概览等于放弃本轮内存状态；已评分的卡片早已落库，不受影响。
        self._session = []
        self._index = 0
        self._at_checkpoint = False
        self._stack.setCurrentWidget(self._idle_page)

    # ------------------------------------------------------------------ 会话
    def _is_session_active(self) -> bool:
        return (
            bool(self._session)
            and self._index < len(self._session)
            and self._stack.currentWidget() is self._card_page
        )

    def _start_session(self) -> None:
        review_config = self._review_config()
        goal = review_config.daily_limit
        reviews_done = self._context.stats_repo.get().reviews_done
        remaining_goal = max(0, goal - reviews_done)
        # 目标内按「还差多少」取词；目标已完成时点按钮表示加练，再取一轮目标数。
        batch = remaining_goal if remaining_goal > 0 else goal
        words = self._context.review_service().due_words(limit=min(batch, SESSION_LIMIT))
        if not words:
            self.refresh()
            set_status(self._status, "今天没有到期的词，读一篇文章再回来吧。", "hint")
            return

        # 一次查完所有词最近一次复习记录，用于决定卡片形态（避免 N+1 查询）。
        history = self._context.review_repo.last_reviews(
            [word.id for word in words if word.id is not None]
        )
        self._session = build_session_plan(
            words, history, spelling_enabled=review_config.spelling_enabled
        )
        self._index = 0
        self._reviewed = 0
        self._recalled = 0
        self._mode_counts = {CARD_RECOGNITION: 0, CARD_SPELLING: 0, CARD_DUAL: 0}
        self._round_start = 0
        self._round_no = 1
        self._round_reviewed = 0
        self._round_recalled = 0
        self._at_checkpoint = False
        self._current = None
        self._revealed = False
        set_status(self._status, "", "hint")
        self._stack.setCurrentWidget(self._card_page)
        self._show_current()
        # 拼写正面把焦点留在输入框，不能再用页面抢焦点。
        if not self._spell_input_active:
            self.setFocus()

    def _show_current(self) -> None:
        card = self._session[self._index]
        word = card.word
        self._current = word
        self._current_mode = card.mode
        self._revealed = False
        self._recall_result = None
        self._spell_input_active = False
        self._prompt = (
            build_spelling_prompt(word) if card.mode in (CARD_SPELLING, CARD_DUAL) else None
        )

        # 换卡时回到正文顶部，避免沿用上一张卡翻到底后的滚动位置。
        self._scroll.verticalScrollBar().setValue(0)
        self._progress_label.setText(f"第 {self._index + 1} / {len(self._session)} 个")
        self._mode_label.setText(_MODE_TITLES.get(card.mode, ""))
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
        self._spelling_result.clear()
        self._spelling_result.setVisible(False)
        self._spelling_input.clear()
        self._spelling_input.setReadOnly(False)
        self._spelling_input.setEnabled(True)
        self._spelling_submit.setVisible(True)
        self._spelling_submit.setEnabled(True)

        if card.mode == CARD_SPELLING:
            self._show_spelling_front()
        else:
            self._show_recognition_front()
            if card.mode == CARD_DUAL:
                self._reveal_button.setText("显示释义并默写（空格）")

    def _show_spelling_front(self) -> None:
        """拼写模式的正脸：释义 + 挖空例句 + 输入框。"""
        prompt = self._prompt
        if prompt is None:
            # 编排阶段已经确认可拼写；理论上不会走到这里，兜底回退看词。
            self._current_mode = CARD_RECOGNITION
            self._mode_label.setText(_MODE_TITLES[CARD_RECOGNITION])
            self._show_recognition_front()
            return

        hint = "根据释义写出英文单词"
        if prompt.pos:
            hint = f"{hint} · {prompt.pos}"
        self._fill_spelling_prompt(hint)
        self._spelling_box.setVisible(True)
        self._spell_input_active = True

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

    def _show_recognition_front(self) -> None:
        """看词回忆的正脸：原词 + 音标 + 上下文。"""
        self._spelling_box.setVisible(False)
        self._word_label.setVisible(True)
        self._lemma_label.setVisible(bool(self._lemma_label.text()))
        self._phonetic_label.setVisible(bool(self._current and self._current.phonetic))
        self._pos_label.setVisible(bool(self._current and self._current.pos))
        self._context_label.setVisible(bool(self._current and self._current.context_sentence))
        self._reveal_button.setText("显示答案（空格）")
        self._reveal_button.setVisible(True)
        self._answer_box.setVisible(False)
        self._rating_bar.setVisible(False)
        self._rating_hint.setVisible(False)
        self._set_shortcuts_enabled(True)

    def _fill_spelling_prompt(self, hint: str) -> None:
        """把题面写进拼写区；拼写正面与双阶段默写共用。"""
        prompt = self._prompt
        if prompt is None:
            return
        self._prompt_hint.setText(hint)
        self._prompt_meaning.setText(prompt.meaning)
        self._prompt_meaning.setVisible(True)
        self._prompt_context.setText(prompt.masked_sentence)
        self._prompt_context.setVisible(bool(prompt.masked_sentence))

    # ------------------------------------------------------------------ 翻面
    def _reveal(self) -> None:
        """翻面看释义；拼写模式用于“跳过输入直接看答案”，双阶段进入默写。"""
        if self._stack.currentWidget() is not self._card_page:
            return
        if self._current is None or self._revealed:
            return
        self._revealed = True

        if self._current_mode == CARD_SPELLING:
            self._reveal_spelling_answer()
            self._answer_box.setVisible(True)
            self._show_rating_bar()
            self._scroll_to(self._word_label)
        elif self._current_mode == CARD_DUAL and self._prompt is not None:
            self._show_dual_spell_phase()
        else:
            # 双阶段题面异常时回退成普通看词翻面，不能让界面卡住。
            self._answer_box.setVisible(True)
            self._show_rating_bar()
            self._scroll_to(self._answer_box)

    def _scroll_to(self, widget: QWidget) -> None:
        """把翻面后要看的控件滚进视野。

        控件刚 setVisible(True) 时布局还没刷新，同步调 ensureWidgetVisible 会用旧几何，
        所以延后到下一拍事件循环再滚。
        """
        QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(widget, 0, 8))

    def _show_rating_bar(self) -> None:
        self._reveal_button.setVisible(False)
        self._rating_bar.setVisible(True)
        self._rating_hint.setVisible(True)
        self._set_shortcuts_enabled(True)

    def _show_dual_spell_phase(self) -> None:
        """双阶段翻面：遮住词形，只留挖空释义，让用户再默写一次。"""
        word = self._current
        if word is None:
            return
        self._fill_spelling_prompt("遮住词形，再默写一次（回车检查；也可直接按 1~4 评分）")
        self._spelling_box.setVisible(True)
        self._spelling_result.clear()
        self._spelling_result.setVisible(False)
        self._spelling_input.clear()
        self._spelling_input.setReadOnly(False)
        self._spelling_input.setEnabled(True)
        self._spelling_submit.setVisible(True)
        self._spelling_submit.setEnabled(True)
        self._spell_input_active = True

        # 默写阶段不能露词：原词、原形、音标、上下文、答案区全部藏起来。
        self._word_label.setVisible(False)
        self._lemma_label.setVisible(False)
        self._phonetic_label.setVisible(False)
        self._pos_label.setVisible(False)
        self._context_label.setVisible(False)
        self._answer_box.setVisible(False)

        self._reveal_button.setVisible(False)
        self._rating_bar.setVisible(True)
        self._rating_hint.setText("按 1~4 直接评分（跳过默写），或先写一遍再评分")
        self._rating_hint.setVisible(True)

        # 回车交给输入框，空格禁用；1~4 保留，方便直接跳过默写。
        self._set_reveal_shortcuts_enabled(False)
        self._set_rating_shortcuts_enabled(True)
        self._spelling_input.setFocus()

    def _reveal_spelling_answer(self) -> None:
        """拼写模式翻面：停用输入框，露出原词与音标。"""
        word = self._current
        if word is None:
            return
        self._spell_input_active = False
        self._spelling_input.setReadOnly(True)
        self._spelling_submit.setVisible(False)
        self._word_label.setVisible(True)
        self._lemma_label.setVisible(bool(self._lemma_label.text()))
        self._phonetic_label.setVisible(bool(word.phonetic))
        self._pos_label.setVisible(bool(word.pos))
        if self._recall_result is None:
            set_status(self._spelling_result, "已跳过输入，按实际记忆程度评分。", "hint")
            self._spelling_result.setVisible(True)

    # ------------------------------------------------------------------ 拼写
    def _submit_spelling(self) -> None:
        """判分当前拼写答案；结果只给建议，最终仍由用户按 1~4 确认。"""
        if self._stack.currentWidget() is not self._card_page:
            return
        # 双阶段默写发生在翻面之后，所以这里不能用 _revealed 拦截；
        # 只有输入框仍可编辑（正面拼写 / 双阶段默写）时才接受提交。
        if self._current is None or not self._spell_input_active:
            return
        answer = self._spelling_input.text().strip()
        if not answer:
            set_status(self._status, "先输入一个答案；也可以直接按 1~4 评分跳过默写。", "hint")
            self._spelling_input.setFocus()
            return

        set_status(self._status, "", "hint")
        result = check_spelling(self._current, answer)
        self._recall_result = result

        if self._current_mode == CARD_DUAL:
            # 双阶段已经在默写阶段，不需要再走一次 _reveal。
            self._finish_dual_spelling()
        else:
            self._reveal()

        self._show_spelling_feedback(result)

    def _finish_dual_spelling(self) -> None:
        """双阶段默写结束：停用输入框，恢复完整答案，等待 1~4 评分。"""
        word = self._current
        if word is None:
            return
        self._spell_input_active = False
        self._spelling_input.setReadOnly(True)
        self._spelling_submit.setVisible(False)
        self._prompt_hint.setText("默写结果")
        self._prompt_meaning.setVisible(False)
        self._prompt_context.setVisible(False)

        self._word_label.setVisible(True)
        self._lemma_label.setVisible(bool(self._lemma_label.text()))
        self._phonetic_label.setVisible(bool(word.phonetic))
        self._pos_label.setVisible(bool(word.pos))
        self._context_label.setVisible(bool(word.context_sentence))
        self._answer_box.setVisible(True)
        self._rating_bar.setVisible(True)
        self._rating_hint.setVisible(True)
        self._set_shortcuts_enabled(True)
        self._scroll_to(self._word_label)

    def _show_spelling_feedback(self, result: RecallResult) -> None:
        expected = self._answer_display(self._current) if self._current else result.expected
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

    # ------------------------------------------------------------------ 评分
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

        # 拼写证据：真正提交过答案才有；看词回忆、拼写跳过、双阶段未默写
        # 统一记 skipped（M5.1）。
        answer_text = ""
        verdict = VERDICT_SKIPPED
        distance = 0
        result = self._recall_result
        if result is not None:
            answer_text = result.answer
            verdict = result.status
            distance = result.distance

        try:
            self._context.review_service().review(
                self._current,
                rating,
                note=note,
                advice=advice,
                answer_text=answer_text,
                verdict=verdict,
                distance=distance,
            )
        except Exception as exc:  # noqa: BLE001 - 本地写库失败不能让界面崩掉
            logger.exception("复习评分失败")
            set_status(self._status, f"保存复习进度失败：{exc}", "error")
            return

        self._reviewed += 1
        self._round_reviewed += 1
        self._mode_counts[self._current_mode] = self._mode_counts.get(self._current_mode, 0) + 1
        if rating >= RATING_KNOWN:
            self._recalled += 1
            self._round_recalled += 1

        self._index += 1
        if self._index >= len(self._session):
            self._finish_session()
        elif self._index - self._round_start >= ROUND_SIZE:
            self._show_checkpoint()
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
        # 弹窗打开期间禁用全部快捷键，避免输入原因时误触发评分。
        # 关闭后必须按快照恢复：双阶段默写阶段本来就禁用空格/回车，
        # 无条件 setEnabled(True) 会让回车被页面快捷键截获、无法提交默写。
        previous = [(shortcut, shortcut.isEnabled()) for shortcut in self._shortcuts]
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            for shortcut, enabled in previous:
                shortcut.setEnabled(enabled)

        if not accepted:
            return None
        return dialog.result_reason, dialog.result_advice

    def _show_checkpoint(self) -> None:
        """每 ROUND_SIZE 张卡暂停一次，给「继续 / 到此为止」的选择。"""
        self._at_checkpoint = True
        self._current = None
        self._revealed = False
        self._prompt = None
        self._recall_result = None
        self._spell_input_active = False

        done = self._context.stats_repo.get().reviews_done
        goal = self._review_config().daily_limit
        total = self._round_reviewed
        recalled = self._round_recalled
        percent = round(recalled * 100 / total) if total else 0
        remaining = len(self._session) - self._index

        self._summary_title.setText(f"第 {self._round_no} 轮完成")
        self._summary_detail.setText(
            f"本轮复习 {total} 个词，其中 {recalled} 个记得（{percent}%）。\n"
            f"今日已完成 {done} / 目标 {goal}。"
        )
        self._continue_button.setText(f"继续下一轮（还剩 {remaining} 个）")
        self._continue_button.setVisible(True)
        self._finish_button.setVisible(True)
        self._overview_button.setVisible(True)
        self._stack.setCurrentWidget(self._summary_page)
        self._refresh_due_label()
        set_status(self._status, "进度已保存，可以继续，也可以先停在这里。", "hint")

    def _continue_from_summary(self) -> None:
        """检查点继续本轮；总结页重新取「还差的目标」或加练一轮。"""
        if self._at_checkpoint:
            self._continue_round()
        else:
            self._start_session()

    def _continue_round(self) -> None:
        if self._index >= len(self._session):
            self._start_session()
            return
        self._round_no += 1
        self._round_start = self._index
        self._round_reviewed = 0
        self._round_recalled = 0
        self._at_checkpoint = False
        self._stack.setCurrentWidget(self._card_page)
        self._show_current()

    def _end_session_early(self) -> None:
        """随时收工：已评分的卡片都已落库，直接展示部分总结。"""
        if self._stack.currentWidget() is not self._card_page:
            return
        if self._reviewed == 0:
            self.refresh()
            set_status(self._status, "还没有复习任何词，进度没有变化。", "hint")
            return
        self._finish_session()

    def _finish_session(self) -> None:
        self._at_checkpoint = False
        self._current = None
        self._revealed = False
        self._prompt = None
        self._recall_result = None
        self._spell_input_active = False
        total = self._reviewed
        recalled = self._recalled
        percent = round(recalled * 100 / total) if total else 0

        parts: list[str] = []
        for mode in (CARD_RECOGNITION, CARD_SPELLING, CARD_DUAL):
            count = self._mode_counts.get(mode, 0)
            if count:
                parts.append(f"{_MODE_TITLES[mode]} {count} 个")
        breakdown = f"（{'、'.join(parts)}）" if parts else ""

        done = self._context.stats_repo.get().reviews_done
        goal = self._review_config().daily_limit
        due_count = self._context.review_service().count_due()

        if due_count == 0:
            self._summary_title.setText("今天的复习已完成")
            self._summary_detail.setText(
                f"本轮复习了 {total} 个词{breakdown}，其中 {recalled} 个记得（{percent}%）。\n"
                "今天到期的词已全部复习完。"
            )
            self._continue_button.setVisible(False)
        elif done >= goal:
            self._summary_title.setText("今日目标已完成")
            self._summary_detail.setText(
                f"本轮复习了 {total} 个词{breakdown}，其中 {recalled} 个记得（{percent}%）。\n"
                f"今日已完成 {done} / {goal}；还有 {due_count} 个到期词，可以加练一轮。"
            )
            self._continue_button.setText(f"加练一轮（{min(due_count, goal, SESSION_LIMIT)} 个）")
            self._continue_button.setVisible(True)
        else:
            remaining = goal - done
            self._summary_title.setText("本轮已结束")
            self._summary_detail.setText(
                f"本轮复习了 {total} 个词{breakdown}，其中 {recalled} 个记得（{percent}%）。\n"
                f"今日已完成 {done} / {goal}，还差 {remaining} 个；进度已保存，随时可以继续。"
            )
            self._continue_button.setText(
                f"继续复习（还差 {min(due_count, remaining, SESSION_LIMIT)} 个）"
            )
            self._continue_button.setVisible(True)

        self._finish_button.setVisible(False)
        self._overview_button.setVisible(True)
        self._stack.setCurrentWidget(self._summary_page)
        self._refresh_due_label()
        if due_count == 0:
            set_status(self._status, "复习进度已保存，今天到期的词已全部复习完。", "success")
        elif done >= goal:
            set_status(self._status, "复习进度已保存，今日复习目标完成。", "success")
        else:
            set_status(
                self._status,
                f"复习进度已保存，今日已完成 {done} / {goal}。",
                "hint",
            )

    def _refresh_due_label(self) -> None:
        due_count = self._context.review_service().count_due()
        reviews_done = self._context.stats_repo.get().reviews_done
        goal = self._review_config().daily_limit
        self._due_label.setText(
            f"今天到期 {due_count} 词 · 今日已复习 {reviews_done} / 目标 {goal}"
        )
