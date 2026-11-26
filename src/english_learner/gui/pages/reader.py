"""阅读页：今日文章 + 双击查词 + 加入生词本高亮。

线程约定：
- 生成文章、查词都会发网络请求，必须走 TaskWorker；
- 加入生词本 / 重绘高亮是本地 SQLite 操作，直接在 UI 线程做（毫秒级）。

状态约定：
- 页面只有一份 `_article`；进入页面时如果当天没有文章会自动生成；
- 查词与生成共用同一个 `_worker` 槽位，同一时刻只允许一个后台任务；
- 预取（`_prefetch_worker`）是独立槽位：加载文章后在后台批量查词、只写缓存，
  不阻塞查词与生成；失败静默，切换文章时自动为当前文章重新发起；
- 补全（`_enrich_worker`）也是独立槽位：加入生词本后，若该词只有预生成简释，
  在后台按完整查词 prompt 补一次释义（M6.3），完成后原地刷新词条与卡片；
  同样不阻塞阅读，失败静默；
- “换一篇”先弹窗收集主题（可留空）；弹窗里的「调整难度」继续走原来的
  阅读反馈调级流程。确认后会连带删除当天正文里的高亮。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import Article, Highlight, Word, now_iso, today_str
from ...services.app_context import AppContext
from ...services.dictionary_service import LookupResult, normalize_lemma
from ...services.readability import ReadabilityMetrics, threshold_warnings
from ...services.reading_quiz_service import UPGRADE_EVIDENCE_REQUIRED
from ..level_feedback_dialog import LevelFeedbackDialog
from ..reading_quiz_dialog import ReadingQuizDialog
from ..real_text_source_dialog import RealTextSourceDialog
from ..regenerate_dialog import RegenerateDialog
from ..widgets import ArticleView, WordPanel, busy_progress_bar
from ..workers import TaskWorker, retire_worker
from .common import PageHeader, set_status

logger = logging.getLogger(__name__)


class ReaderPage(QWidget):
    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._article: Article | None = None
        self._highlights: list[Highlight] = []
        self._pending: tuple[str, int, int, str] | None = None
        self._current_word_id: int | None = None
        self._worker: TaskWorker | None = None
        # 预取是独立线程：加载文章后批量查词写缓存，不能占用查词/生成的槽位。
        self._prefetch_worker: TaskWorker | None = None
        self._prefetched_article_id: int | None = None
        # 后台补全：加入生词本后把预生成简释补成完整查词，串行执行。
        self._enrich_worker: TaskWorker | None = None
        self._enrich_queue: list[tuple[int, str]] = []
        # 本次会话里自动生成失败/取消过：切页回来不再自动重发，等用户点“重试生成”。
        self._generation_failed = False
        # 当前 UI 槽位里的任务类型，取消后据此恢复不同状态。
        self._busy_phase = ""
        # M7.1：用户点「换回 AI 文章」后暂存 text_id，生成成功再标记 overridden。
        self._real_override_ref = ""

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        # 页头压缩：阅读页的主体是正文，标题区只留必要高度。
        layout.setContentsMargins(32, 18, 32, 18)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_row.addWidget(
            PageHeader(
                # 页面名已在侧边栏高亮，这里不再重复大标题，避免白占一行高度。
                "",
                "双击单词查看语境释义；读完后点「阅读完成」做几道题，AI 会讲评并动态调整等级。",
                compact=True,
            ),
            1,
        )

        self._generate_button = QPushButton("生成今日文章")
        self._generate_button.setObjectName("primaryButton")
        self._generate_button.clicked.connect(self._on_generate_clicked)
        header_row.addWidget(self._generate_button, 0, Qt.AlignmentFlag.AlignBottom)

        self._quiz_button = QPushButton("阅读完成")
        self._quiz_button.setObjectName("primaryButton")
        self._quiz_button.setToolTip(
            "读完后做几道题，AI 会讲评并根据答题情况调整等级；首次检测会先记录这篇读起来的感受"
        )
        self._quiz_button.clicked.connect(self._on_quiz_clicked)
        header_row.addWidget(self._quiz_button, 0, Qt.AlignmentFlag.AlignBottom)

        self._regenerate_button = QPushButton("换一篇")
        self._regenerate_button.setObjectName("secondaryButton")
        self._regenerate_button.setToolTip(
            "写想读的主题，或直接说这篇太难/太简单；有输入时会先重新评估等级"
        )
        self._regenerate_button.clicked.connect(self._on_regenerate_clicked)
        header_row.addWidget(self._regenerate_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header_row)

        self._title_label = QLabel("今日文章")
        self._title_label.setObjectName("articleTitle")
        layout.addWidget(self._title_label)

        self._meta_label = QLabel("")
        self._meta_label.setObjectName("mutedCompact")
        layout.addWidget(self._meta_label)

        # M7.1：真实文章顶部细横幅：说明来源 + 换回 AI 文章的逃生口。
        self._real_banner = QWidget()
        banner_row = QHBoxLayout(self._real_banner)
        banner_row.setContentsMargins(0, 0, 0, 0)
        banner_row.setSpacing(10)
        self._real_banner_label = QLabel("")
        self._real_banner_label.setObjectName("muted")
        self._real_banner_label.setWordWrap(True)
        banner_row.addWidget(self._real_banner_label, 1)
        self._real_source_button = QPushButton("来源")
        self._real_source_button.setObjectName("linkButton")
        self._real_source_button.clicked.connect(self._on_real_source_clicked)
        banner_row.addWidget(self._real_source_button)
        self._real_override_button = QPushButton("换回 AI 文章")
        self._real_override_button.setObjectName("secondaryButton")
        self._real_override_button.setToolTip("替换今天的真实文章，之后不会再自动出现")
        self._real_override_button.clicked.connect(self._on_real_override_clicked)
        banner_row.addWidget(self._real_override_button)
        self._real_banner.setVisible(False)
        layout.addWidget(self._real_banner)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._status = QLabel("")
        status_row.addWidget(self._status, 1)
        self._busy_progress = busy_progress_bar()
        status_row.addWidget(self._busy_progress, 0)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setObjectName("linkButton")
        self._cancel_button.setToolTip("取消正在进行的 AI 请求；已生成的内容不会丢失")
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._cancel_button.hide()
        status_row.addWidget(self._cancel_button, 0)
        layout.addLayout(status_row)

        # 正文占满整页；释义卡是浮在页面上的子控件，不再占用固定右栏。
        self._view = ArticleView()
        self._view.word_selected.connect(self._on_word_selected)
        self._view.background_clicked.connect(self._hide_word_card)
        self._view.verticalScrollBar().valueChanged.connect(self._hide_word_card)
        layout.addWidget(self._view, 1)

        self._panel = WordPanel(self)
        self._panel.add_requested.connect(self._on_add_requested)
        self._panel.refresh_requested.connect(self._on_refresh_requested)
        self._panel.close_requested.connect(self._hide_word_card)
        shadow = QGraphicsDropShadowEffect(self._panel)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(15, 23, 42, 70))
        shadow.setOffset(0, 8)
        self._panel.setGraphicsEffect(shadow)
        self._panel.hide()

        self._close_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._close_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._close_shortcut.activated.connect(self._hide_word_card)

        self._generate_button.setVisible(False)
        self._quiz_button.setEnabled(False)
        self._regenerate_button.setEnabled(False)
        set_status(self._status, "进入本页会自动准备今天的文章。", "hint")

    # ------------------------------------------------------------------ 字体
    def apply_english_font(self, key: str | None = None, *, mono: bool = False) -> None:
        """设置页切换字体/主题后由主窗口调用（QSS 管不到 QTextDocument）。"""
        self._view.set_english_font(key, mono=mono)

    def apply_low_profile(self, low_profile: bool) -> None:
        """主题切换：同步正文生词高亮的配色（同一套逻辑见 ArticleView）。"""
        self._view.set_low_profile(low_profile)

    # -------------------------------------------------------------- 生命周期
    def on_activated(self) -> None:
        """主窗口切到本页时调用。"""
        self.refresh()

    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self._hide_word_card()
        self._generate_button.setVisible(False)
        article = self._context.article_repo.get_by_date(today_str())
        if article is None:
            self._article = None
            self._view.set_article(None)
            self._title_label.setText("今日文章 · 尚未生成")
            self._meta_label.setText("")
            self._real_banner.setVisible(False)
            self._quiz_button.setText("阅读完成")
            self._quiz_button.setEnabled(False)
            if self._generation_failed:
                # 自动生成失败过：只把按钮亮出来，等用户主动重试。
                # 否则每次切回阅读页都会再发一轮请求（超时时一轮要等好几分钟）。
                self._generate_button.setText("重试生成")
                self._generate_button.setVisible(True)
                self._generate_button.setEnabled(True)
            else:
                self._start_generation(regenerate=False)
            return
        self._load_article(article)

    # ------------------------------------------------------------------ 文章
    def _load_article(self, article: Article) -> None:
        self._article = article
        self._generation_failed = False
        self._view.set_article(article)
        self._reload_highlights()
        self._hide_word_card()
        self._panel.show_placeholder()
        self._pending = None
        self._current_word_id = None

        self._title_label.setText(article.title)
        self._render_meta()
        is_real = (article.source or "") == "real"
        metrics = ReadabilityMetrics.from_dict(article.metrics)

        self._real_banner.setVisible(is_real)
        if is_real:
            ratio_text = ""
            if metrics is not None and metrics.wordlist_size > 0 and metrics.word_total:
                ratio_text = f" · 预计生词 {metrics.unknown_ratio:.0%}"
            self._real_banner_label.setText(f"为你安排的真实文章{ratio_text}")

        self._generate_button.setVisible(False)
        self._regenerate_button.setEnabled(True)
        self._refresh_quiz_button(article)
        set_status(self._status, "双击单词查看释义，加入生词本即自动高亮。", "hint")

        if article.id is not None and self._context.article_repo.mark_read(article.id, now_iso()):
            self._context.stats_repo.bump(article.article_date, article_read=1)

        self._start_prefetch(article)

    # ------------------------------------------------------------------ 预取
    def _start_prefetch(self, article: Article) -> None:
        """后台批量预查文章生词，写入查词缓存（双击时 0 等待）。

        只查“用户大概率不认识、且还没缓存”的词，一次批量请求；失败不弹错，
        用户仍然可以双击后走正常查词。每篇文章只预取一次。
        """
        if article.id is None or article.id == self._prefetched_article_id:
            return
        # 只要上一个预取 worker 还在（包括已跑完、finished 信号尚未处理的情况），
        # 就不新建：等 _on_prefetch_finished 回收后，它会为当前文章补一次。
        if self._prefetch_worker is not None:
            return
        if not self._context.store.load_ui().prefetch_enabled:
            return

        self._prefetched_article_id = article.id
        content = article.content
        level = self._context.level()
        article_id = article.id
        service = self._context.dictionary_service()
        worker = TaskWorker(lambda: service.prefetch(content, level=level, article_id=article_id))
        # 预取没有界面状态：失败信号不连接，成功也无需回填界面。
        worker.finished.connect(self._on_prefetch_finished)
        self._prefetch_worker = worker
        worker.start()

    def _on_prefetch_finished(self) -> None:
        self._prefetch_worker = None
        # 预取期间换了文章（换一篇 / 调整难度）：为当前文章补一次预取。
        if self._article is not None and self._article.id != self._prefetched_article_id:
            self._start_prefetch(self._article)

    # ------------------------------------------------------------------ 补全
    def _start_enrich(self, word_id: int | None, sentence: str) -> None:
        """加入生词本后，后台把预生成简释补成与双击查词一致的完整词条。

        不检查词条是否真的需要补全：`DictionaryService.enrich_definition()`
        会在子线程里按最新数据判断（完整词条直接返回 None，不花 token），
        这样也天然避免了“用户先补全、队列里的旧任务再跑一遍”。
        """
        if word_id is None:
            return
        if any(item_id == word_id for item_id, _ in self._enrich_queue):
            return
        self._enrich_queue.append((int(word_id), sentence))
        self._pump_enrich()

    def _pump_enrich(self) -> None:
        if self._enrich_worker is not None or not self._enrich_queue:
            return
        word_id, sentence = self._enrich_queue.pop(0)
        level = self._context.level()
        service = self._context.dictionary_service()
        worker = TaskWorker(lambda: service.enrich_definition(word_id, sentence, level))
        worker.succeeded.connect(self._on_enrich_ready)
        # 补全是增强信息：失败只记日志，卡片与词条保留原来的简释。
        worker.failed.connect(self._on_enrich_failed)
        worker.finished.connect(self._on_enrich_finished)
        self._enrich_worker = worker
        worker.start()

    def _on_enrich_ready(self, result: object) -> None:
        if not isinstance(result, Word) or result.id is None:
            return
        # 用户已经收起卡片或点了别的词：只更新库里的词条，不打扰当前阅读。
        if self._pending is None or self._current_word_id != result.id:
            return
        self._panel.update_definition(result, self._pending[3])
        set_status(self._status, "已加入生词本，释义已在后台补全。", "success")

    @staticmethod
    def _on_enrich_failed(message: str) -> None:
        logger.info("后台补全释义失败，保留简释：%s", message)

    def _on_enrich_finished(self) -> None:
        self._enrich_worker = None
        self._pump_enrich()

    def _start_generation(self, *, regenerate: bool, user_request: str = "") -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self._hide_word_card()

        profile = self._context.profile()
        if profile is None:
            self._generate_button.setVisible(True)
            self._generate_button.setEnabled(True)
            set_status(
                self._status,
                "还没有完成水平测评，请先在「今日」页做一次测评，再生成文章。",
                "hint",
            )
            return

        service = self._context.article_service()
        if regenerate and self._article is not None:
            # 无论是「换一篇」还是横幅上的「换回 AI」，替换掉真实文章后
            # 都要在生成成功时记 overridden/skip（M7.2）。
            if (self._article.source or "") == "real":
                self._real_override_ref = self._real_override_ref or self._article.source_ref
        if regenerate:
            self._busy_phase = "regenerate"
            if user_request:
                set_status(self._status, "正在按你的要求换一篇，请稍候…", "loading")
            else:
                set_status(self._status, "正在换一篇，请稍候…", "loading")
            task = lambda: service.regenerate_today(  # noqa: E731
                profile, user_request=user_request
            )
        else:
            self._busy_phase = "generate"
            set_status(self._status, "正在生成今日文章，请稍候…", "loading")
            task = lambda: service.get_or_create_today(profile)  # noqa: E731

        self._set_busy(True)
        worker = TaskWorker(task)
        worker.succeeded.connect(self._on_article_ready)
        worker.failed.connect(self._on_article_failed)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    # ------------------------------------------------------------ 真实文章
    def _on_real_source_clicked(self) -> None:
        """展示真实文章的来源 / 许可（只读，不发网络请求）。"""
        if self._article is None or (self._article.source or "") != "real":
            return
        text = self._context.real_text_repo.get(self._article.source_ref)
        RealTextSourceDialog(text, parent=self).exec()

    def _on_real_override_clicked(self) -> None:
        """用户主动换回 AI 文章：确认后走原「换一篇」生成流程。"""
        if self._article is None or (self._article.source or "") != "real":
            return
        if self._worker is not None and self._worker.isRunning():
            return
        confirm = QMessageBox.question(
            self,
            "换回 AI 文章",
            "今天这篇真实文章会被替换，之后不会再出现。确定换回 AI 文章吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._real_override_ref = self._article.source_ref
        self._start_generation(regenerate=True)

    def _on_generate_clicked(self) -> None:
        # 用户主动重试：清掉失败标记，允许本次会话继续自动生成。
        self._generation_failed = False
        self._start_generation(regenerate=False)

    def _on_regenerate_clicked(self) -> None:
        """弹窗收集主题（可留空）；选择「调整难度」时转走原来的调级流程。"""
        if self._article is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._hide_word_card()
        dialog = RegenerateDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.mode() == "adjust":
            self._open_level_feedback()
            return
        self._start_generation(regenerate=True, user_request=dialog.request_text())

    def _open_level_feedback(self) -> None:
        """原来的「调整难度」流程：写阅读感受 → 重新定级并换一篇。"""
        if self._article is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if self._context.profile() is None:
            set_status(self._status, "请先在「今日」页完成水平测评，再调整难度。", "hint")
            return

        self._hide_word_card()
        dialog = LevelFeedbackDialog(self._context, self._article, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_outcome is None:
            return

        outcome = dialog.result_outcome
        previous = self._article
        if (
            previous is not None
            and (previous.source or "") == "real"
            and (outcome.article.source or "") == "generated"
            and previous.source_ref
        ):
            try:
                self._context.real_text_repo.mark_overridden(previous.source_ref)
            except Exception:  # noqa: BLE001 - 标记失败不影响新文章
                logger.warning("标记真实文章已换回失败", exc_info=True)
        self._real_override_ref = ""
        self._load_article(outcome.article)
        if outcome.level_changed:
            set_status(
                self._status,
                f"已按你的反馈把等级从 {outcome.previous_level} 调整为 "
                f"{outcome.profile.cefr_level}，并重新生成了文章。",
                "success",
            )
        else:
            set_status(
                self._status,
                f"已按你的反馈重写文章，等级保持 {outcome.profile.cefr_level}。",
                "success",
            )

    def _on_quiz_clicked(self) -> None:
        """阅读完成 → 出题 / 查看结果。AI 调用都在弹窗自己的 worker 里。"""
        if self._article is None or self._article.id is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if self._context.profile() is None:
            set_status(self._status, "请先在「今日」页完成水平测评，再做阅读检测。", "hint")
            return

        self._hide_word_card()
        dialog = ReadingQuizDialog(self._context, self._article, parent=self)
        dialog.exec()
        if dialog.self_report_cancelled:
            # 首次检测先弹阅读自评；用户点「暂不检测」时本次不开始。
            self._refresh_quiz_button(self._article)
            set_status(self._status, "已取消本次阅读检测，随时可以再做。", "hint")
            return

        quiz = dialog.result_quiz
        if quiz is None:
            return
        self._refresh_quiz_button(self._article)
        if not quiz.completed:
            return

        if quiz.practice_mode:
            text = (
                f"阅读检测完成：得分 {quiz.score}/{quiz.total}；"
                "今天已完成过一次正式检测，本次为练习模式，等级保持"
                f" {quiz.result_level}。"
            )
        elif quiz.waiting_for_upgrade:
            text = (
                f"阅读检测完成：得分 {quiz.score}/{quiz.total}，"
                f"后验建议升到 {quiz.pending_level}；已积累 "
                f"{quiz.pending_count}/{UPGRADE_EVIDENCE_REQUIRED} 次向上证据，"
                f"等级暂时保持 {quiz.result_level}。"
            )
        elif quiz.level_changed:
            text = (
                f"阅读检测完成：得分 {quiz.score}/{quiz.total}，"
                f"等级 {quiz.previous_level} → {quiz.result_level}。"
            )
        else:
            text = (
                f"阅读检测完成：得分 {quiz.score}/{quiz.total}，"
                f"等级保持 {quiz.result_level or self._context.level()}。"
            )
        set_status(self._status, text, "success")

    def _on_article_ready(self, result: object) -> None:
        if not isinstance(result, Article):
            set_status(self._status, "生成完成，但返回了意外数据。", "error")
            return
        if self._real_override_ref and (result.source or "") == "generated":
            try:
                self._context.real_text_repo.mark_overridden(self._real_override_ref)
            except Exception:  # noqa: BLE001 - 标记失败不影响新文章
                logger.warning("标记真实文章已换回失败", exc_info=True)
        self._real_override_ref = ""
        self._load_article(result)

    def _on_article_failed(self, message: str) -> None:
        # 换回 AI 失败：保留真实文章，下次还能再点。
        self._real_override_ref = ""
        # 已有文章时只是“换一篇/重写”失败，旧文章还在，不要显示“重试生成”。
        if self._article is None:
            self._generation_failed = True
            self._generate_button.setVisible(True)
            self._generate_button.setText("重试生成")
        set_status(self._status, message, "error")

    # ------------------------------------------------------------------ 查词
    def _on_word_selected(self, word: str, start: int, end: int, sentence: str) -> None:
        if self._article is None or self._article.id is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return

        lemma = normalize_lemma(word)
        if not lemma:
            set_status(self._status, f"「{word}」不是英文单词，请重新选择。", "hint")
            return

        self._pending = (lemma, start, end, sentence)
        self._current_word_id = None
        self._start_lookup(word, sentence)

    def _start_lookup(self, word: str, sentence: str, *, refresh: bool = False) -> None:
        """发起查词请求；refresh=True 时跳过缓存，按当前句子重新查询。"""
        if self._article is None or self._article.id is None:
            return
        article_id = self._article.id
        level = self._context.level()
        service = self._context.dictionary_service()
        self._busy_phase = "lookup"
        self._panel.show_loading(word)
        self._show_word_card()
        set_status(
            self._status,
            f"正在按本句重查 {word} …" if refresh else f"正在查询 {word} …",
            "loading",
        )
        worker = TaskWorker(
            lambda: service.lookup(word, sentence, article_id, level, refresh=refresh)
        )
        worker.succeeded.connect(self._on_lookup_ready)
        worker.failed.connect(self._on_lookup_failed)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_refresh_requested(self) -> None:
        """用户发现缓存释义与当前语境不符时，按本句重查。"""
        if self._worker is not None and self._worker.isRunning():
            return
        if self._pending is None:
            return
        lemma, _, _, sentence = self._pending
        self._start_lookup(lemma, sentence, refresh=True)

    def _on_lookup_ready(self, result: object) -> None:
        if self._pending is None:
            # 用户已经收起卡片（点击别处/滚动/切文章），迟到的结果不再弹出。
            return
        if not isinstance(result, LookupResult):
            self._panel.show_error("查词返回了意外数据。")
            return
        sentence = self._pending[3]
        self._current_word_id = result.word.id
        self._panel.show_definition(
            result.word,
            sentence,
            cached=result.cached,
            context_mismatch=result.context_mismatch,
        )
        self._show_word_card()

        if self._pending is not None and self._has_highlight_at(self._pending[1], self._pending[2]):
            self._panel.mark_added(False)
            set_status(self._status, "这个位置已经标注过了。", "hint")
        else:
            source = "已命中释义缓存" if result.cached else "AI 释义已缓存"
            set_status(self._status, f"{source}，确认后点「加入生词本」。", "hint")

    def _on_lookup_failed(self, message: str) -> None:
        if self._pending is None:
            return
        self._panel.show_error(message)
        self._show_word_card()
        set_status(self._status, message, "error")

    def _on_add_requested(self) -> None:
        if self._article is None or self._article.id is None:
            return
        if self._current_word_id is None or self._pending is None:
            return

        word = self._context.word_repo.get_by_id(self._current_word_id)
        if word is None:
            set_status(self._status, "词条已不存在，请重新查词。", "error")
            return

        _, start, end, sentence = self._pending
        service = self._context.dictionary_service()
        try:
            inserted = service.add_to_vocabulary(
                word,
                article_id=self._article.id,
                start_offset=start,
                end_offset=end,
                sentence=sentence,
            )
        except ValueError as exc:
            set_status(self._status, f"加入生词本失败：{exc}", "error")
            return

        self._reload_highlights()
        self._panel.mark_added(inserted)
        if inserted:
            set_status(self._status, "已加入生词本，今日生词 +1。", "success")
        # 简释词条（文章难词 / 预取）在后台补一次完整查词，阅读不中断。
        self._start_enrich(word.id, sentence)

    # ------------------------------------------------------------------ 辅助
    def _refresh_quiz_button(self, article: Article) -> None:
        """根据当天是否已完成检测，切换“阅读完成 / 查看答题结果”。"""
        quiz = (
            self._context.quiz_repo.get_by_article(article.id) if article.id is not None else None
        )
        completed = quiz is not None and quiz.completed
        if completed:
            self._quiz_button.setText("查看答题结果")
        elif quiz is not None:
            self._quiz_button.setText("继续检测")
        else:
            self._quiz_button.setText("阅读完成")
        self._quiz_button.setEnabled(self._context.profile() is not None)

    def _render_meta(self) -> None:
        """重绘标题下的信息行：文章属性 + 本篇已加入生词本的词数。

        词数按词去重，同一个词换位置再标一次也只算一个；没有文章时清空。
        """
        article = self._article
        if article is None:
            self._meta_label.setText("")
            return

        meta_parts = [article.level, f"{article.word_count} 词"]
        if (article.source or "") == "real":
            meta_parts.append(f"真实文章 · 约 {article.level}（预估）")
        if article.topic:
            meta_parts.append(article.topic)
        # M5.3：本地可读性指标（旧文章没有 metrics 时整段不显示）。
        metrics = ReadabilityMetrics.from_dict(article.metrics)
        if metrics is not None and metrics.wordlist_size > 0 and metrics.word_total:
            meta_parts.append(f"生词 {metrics.unknown_ratio:.1%}")
            if metrics.target_total:
                meta_parts.append(f"目标词 {metrics.target_total} 个")
            if threshold_warnings(metrics, self._context.readability_thresholds()):
                meta_parts.append("偏难")
        count = len({item.word_id for item in self._highlights})
        meta_parts.append(f"本篇生词本 {count} 个")
        self._meta_label.setText(" · ".join(meta_parts))

    def _reload_highlights(self) -> None:
        if self._article is None or self._article.id is None:
            self._highlights = []
            self._view.set_highlights([])
            self._render_meta()
            return
        self._highlights = self._context.word_repo.list_highlights(self._article.id)
        self._view.set_highlights(self._highlights)
        self._render_meta()

    def _has_highlight_at(self, start: int, end: int) -> bool:
        return any(
            item.start_offset == start and item.end_offset == end for item in self._highlights
        )

    def _set_busy(self, busy: bool) -> None:
        has_article = self._article is not None
        self._generate_button.setEnabled(not busy)
        self._quiz_button.setEnabled(
            (not busy) and has_article and self._context.profile() is not None
        )
        self._regenerate_button.setEnabled((not busy) and has_article)
        self._busy_progress.setVisible(busy)
        self._cancel_button.setVisible(busy)
        self._cancel_button.setEnabled(busy)
        self._cancel_button.setText("取消")

    def _on_cancel_clicked(self) -> None:
        """取消当前查词/生成：旧请求转入后台自行结束，界面立刻可继续操作。"""
        # 取消后这次「换回 AI / 换一篇」没有发生，不能把标记留到后续生成。
        self._real_override_ref = ""
        worker = self._worker
        if worker is None or worker.isFinished():
            self._set_busy(False)
            return

        phase = self._busy_phase
        if not retire_worker(worker):
            return
        self._worker = None
        self._busy_phase = ""
        self._set_busy(False)

        if phase == "lookup":
            self._pending = None
            self._current_word_id = None
            self._panel.hide()
            set_status(self._status, "已取消查词。", "hint")
            return
        if self._article is None:
            # 没有旧文章时，取消后必须把“重试生成”亮出来，否则用户没有入口。
            self._generation_failed = True
            self._generate_button.setText("重试生成")
            self._generate_button.setVisible(True)
            self._generate_button.setEnabled(True)
            set_status(self._status, "已取消生成，可以点「重试生成」再试一次。", "hint")
        else:
            set_status(self._status, "已取消换一篇，当前文章保留。", "hint")

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._busy_phase = ""
        # 兜底：任务以任何终态结束时都不应留下“待标记 overridden”的旧引用。
        self._real_override_ref = ""
        self._set_busy(False)
        if self._article is not None:
            self._regenerate_button.setEnabled(True)

    # ------------------------------------------------------------ 悬浮释义卡
    def _show_word_card(self) -> None:
        """按最近一次取词的位置摆放释义卡，并置于最上层。"""
        self._position_word_card()
        self._panel.show()
        self._panel.raise_()

    def _hide_word_card(self) -> None:
        """收起卡片，并让进行中的查词结果作废（避免迟到的结果又弹出来）。

        查词是最常见的“等不及”场景：用户关掉卡片后应当能立刻双击下一个词。
        所以这里要把仍在运行的查词任务移出 `_worker` 槽位（`retire_worker`），
        否则 `_on_word_selected` 会因为“槽位被占用”直接返回，直到 AI 返回前
        双击任何单词都没反应。
        """
        had_pending = self._pending is not None
        self._panel.hide()
        self._pending = None
        self._current_word_id = None

        if self._busy_phase == "lookup":
            worker = self._worker
            if worker is not None and not worker.isFinished() and retire_worker(worker):
                self._worker = None
                self._busy_phase = ""
                self._set_busy(False)

        if had_pending:
            set_status(self._status, "已收起释义卡。", "hint")

    def _position_word_card(self) -> None:
        margin = 12
        width = self._panel.width()
        max_height = max(220, min(480, self.height() - margin * 2))
        preferred = self._panel.sizeHint().height()
        height = min(max_height, max(self._panel.minimumHeight(), preferred))
        self._panel.resize(width, height)

        rect = self._view.last_word_rect()
        if rect is None:
            self._panel.move(max(margin, self.width() - width - margin), margin)
            return

        anchor = self.mapFromGlobal(self._view.viewport().mapToGlobal(rect.bottomLeft()))
        max_x = max(margin, self.width() - width - margin)
        x = min(max(margin, anchor.x() - width // 2), max_x)
        y = anchor.y() + 10
        if y + height > self.height() - margin:
            # 下方放不下就翻到单词上方。
            top_left = self.mapFromGlobal(self._view.viewport().mapToGlobal(rect.topLeft()))
            y = top_left.y() - height - 10
        y = max(margin, min(y, max(margin, self.height() - height - margin)))
        self._panel.move(x, y)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 约定
        if getattr(self, "_panel", None) is not None:
            self._hide_word_card()
        super().resizeEvent(event)

    # ------------------------------------------------------------------ 关闭
    def shutdown(self, timeout_ms: int = 3000) -> TaskWorker | None:
        """窗口关闭时收尾：请求取消并等待；返回超时后仍在运行的线程。"""
        remaining: TaskWorker | None = None
        for worker in (self._worker, self._prefetch_worker, self._enrich_worker):
            # 不能用 isRunning() 判断：start() 后线程可能还没真正跑起来，
            # 此时 isRunning() 是 False，但线程仍会在稍后执行。
            if worker is None or worker.isFinished():
                continue
            if worker.stop(timeout_ms):
                continue
            if worker is self._prefetch_worker or worker is self._enrich_worker:
                # 预取 / 补全请求自带 35s / 45s 超时，且不承载用户可见结果：
                # 在这里等它退出，避免返回多个“卡住”的线程而 MainWindow 只等得及一个。
                worker.wait()
                continue
            remaining = worker
        return remaining
