"""阅读页：今日文章 + 双击查词 + 加入生词本高亮。

线程约定：
- 生成文章、查词都会发网络请求，必须走 TaskWorker；
- 加入生词本 / 重绘高亮是本地 SQLite 操作，直接在 UI 线程做（毫秒级）。

状态约定：
- 页面只有一份 `_article`；进入页面时如果当天没有文章会自动生成；
- 查词与生成共用同一个 `_worker` 槽位，同一时刻只允许一个后台任务；
- “换一篇”先弹窗收集方向（可留空），确认后会连带删除当天正文里的高亮。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...models.entities import Article, Highlight, now_iso, today_str
from ...services.app_context import AppContext
from ...services.dictionary_service import LookupResult, normalize_lemma
from ..level_feedback_dialog import LevelFeedbackDialog
from ..regenerate_dialog import RegenerateDialog
from ..widgets import ArticleView, WordPanel
from ..workers import TaskWorker
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

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_row.addWidget(
            PageHeader(
                "阅读",
                "双击任意英文单词查看语境释义；加入生词本后正文会永久高亮。",
            ),
            1,
        )

        self._generate_button = QPushButton("生成今日文章")
        self._generate_button.setObjectName("primaryButton")
        self._generate_button.clicked.connect(self._on_generate_clicked)
        header_row.addWidget(self._generate_button, 0, Qt.AlignmentFlag.AlignBottom)

        self._regenerate_button = QPushButton("换一篇")
        self._regenerate_button.setObjectName("secondaryButton")
        self._regenerate_button.setToolTip("写下想读的内容再换一篇；留空则由 AI 自己选题")
        self._regenerate_button.clicked.connect(self._on_regenerate_clicked)
        header_row.addWidget(self._regenerate_button, 0, Qt.AlignmentFlag.AlignBottom)

        self._feedback_button = QPushButton("调整难度")
        self._feedback_button.setObjectName("secondaryButton")
        self._feedback_button.setToolTip("读完写一句感受，AI 重新定级并换一篇更合适的文章")
        self._feedback_button.clicked.connect(self._on_feedback_clicked)
        header_row.addWidget(self._feedback_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header_row)

        self._title_label = QLabel("今日文章")
        self._title_label.setObjectName("cardTitle")
        layout.addWidget(self._title_label)

        self._meta_label = QLabel("")
        self._meta_label.setObjectName("muted")
        layout.addWidget(self._meta_label)

        self._status = QLabel("")
        layout.addWidget(self._status)

        self._view = ArticleView()
        self._view.word_selected.connect(self._on_word_selected)

        self._panel = WordPanel()
        self._panel.add_requested.connect(self._on_add_requested)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._view)
        splitter.addWidget(self._panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)
        layout.addWidget(splitter, 1)

        self._generate_button.setVisible(False)
        self._regenerate_button.setEnabled(False)
        self._feedback_button.setEnabled(False)
        set_status(self._status, "进入本页会自动准备今天的文章。", "hint")

    # ------------------------------------------------------------------ 字体
    def apply_english_font(self, key: str | None = None) -> None:
        """设置页切换英文字体后由主窗口调用（QSS 管不到 QTextDocument）。"""
        self._view.set_english_font(key)

    # -------------------------------------------------------------- 生命周期
    def on_activated(self) -> None:
        """主窗口切到本页时调用。"""
        self.refresh()

    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self._generate_button.setVisible(False)
        article = self._context.article_repo.get_by_date(today_str())
        if article is None:
            self._article = None
            self._view.set_article(None)
            self._title_label.setText("今日文章 · 尚未生成")
            self._meta_label.setText("")
            self._feedback_button.setEnabled(False)
            self._start_generation(regenerate=False)
            return
        self._load_article(article)

    # ------------------------------------------------------------------ 文章
    def _load_article(self, article: Article) -> None:
        self._article = article
        self._view.set_article(article)
        self._reload_highlights()
        self._panel.show_placeholder()
        self._pending = None
        self._current_word_id = None

        self._title_label.setText(article.title)
        meta_parts = [article.level, f"{article.word_count} 词"]
        if article.topic:
            meta_parts.append(article.topic)
        self._meta_label.setText(" · ".join(meta_parts))

        self._generate_button.setVisible(False)
        self._regenerate_button.setEnabled(True)
        self._feedback_button.setEnabled(self._context.profile() is not None)
        set_status(self._status, "双击单词查看释义，加入生词本即自动高亮。", "hint")

        if article.id is not None and self._context.article_repo.mark_read(article.id, now_iso()):
            self._context.stats_repo.bump(article.article_date, article_read=1)

    def _start_generation(self, *, regenerate: bool, user_request: str = "") -> None:
        if self._worker is not None and self._worker.isRunning():
            return

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
        if regenerate:
            if user_request:
                set_status(self._status, "正在按你的要求换一篇，请稍候…", "loading")
            else:
                set_status(self._status, "正在换一篇，请稍候…", "loading")
            task = lambda: service.regenerate_today(  # noqa: E731
                profile, user_request=user_request
            )
        else:
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

    def _on_generate_clicked(self) -> None:
        self._start_generation(regenerate=False)

    def _on_regenerate_clicked(self) -> None:
        """弹窗收集方向（可留空），再交给后台任务换一篇。"""
        if self._article is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        dialog = RegenerateDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_generation(regenerate=True, user_request=dialog.request_text())

    def _on_feedback_clicked(self) -> None:
        """写下阅读感受 → AI 重新定级并换一篇。"""
        if self._article is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if self._context.profile() is None:
            set_status(self._status, "请先在「今日」页完成水平测评，再调整难度。", "hint")
            return

        dialog = LevelFeedbackDialog(self._context, self._article, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_outcome is None:
            return

        outcome = dialog.result_outcome
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

    def _on_article_ready(self, result: object) -> None:
        if not isinstance(result, Article):
            set_status(self._status, "生成完成，但返回了意外数据。", "error")
            return
        self._load_article(result)

    def _on_article_failed(self, message: str) -> None:
        # 已有文章时只是“换一篇/重写”失败，旧文章还在，不要显示“重试生成”。
        if self._article is None:
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
        self._panel.show_loading(lemma)
        set_status(self._status, f"正在查询 {lemma} …", "loading")

        article_id = self._article.id
        level = self._context.level()
        service = self._context.dictionary_service()
        worker = TaskWorker(lambda: service.lookup(word, sentence, article_id, level))
        worker.succeeded.connect(self._on_lookup_ready)
        worker.failed.connect(self._on_lookup_failed)
        worker.cancelled.connect(self._on_worker_finished)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_lookup_ready(self, result: object) -> None:
        if not isinstance(result, LookupResult):
            self._panel.show_error("查词返回了意外数据。")
            return
        sentence = self._pending[3] if self._pending is not None else ""
        self._current_word_id = result.word.id
        self._panel.show_definition(result.word, sentence, cached=result.cached)

        if self._pending is not None and self._has_highlight_at(self._pending[1], self._pending[2]):
            self._panel.mark_added(False)
            set_status(self._status, "这个位置已经标注过了。", "hint")
        else:
            source = "已命中词库缓存" if result.cached else "AI 释义已缓存"
            set_status(self._status, f"{source}，确认后点「加入生词本」。", "hint")

    def _on_lookup_failed(self, message: str) -> None:
        self._panel.show_error(message)
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

    # ------------------------------------------------------------------ 辅助
    def _reload_highlights(self) -> None:
        if self._article is None or self._article.id is None:
            self._highlights = []
            self._view.set_highlights([])
            return
        self._highlights = self._context.word_repo.list_highlights(self._article.id)
        self._view.set_highlights(self._highlights)

    def _has_highlight_at(self, start: int, end: int) -> bool:
        return any(
            item.start_offset == start and item.end_offset == end for item in self._highlights
        )

    def _set_busy(self, busy: bool) -> None:
        has_article = self._article is not None
        self._generate_button.setEnabled(not busy)
        self._regenerate_button.setEnabled((not busy) and has_article)
        self._feedback_button.setEnabled(
            (not busy) and has_article and self._context.profile() is not None
        )

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._set_busy(False)
        if self._article is not None:
            self._regenerate_button.setEnabled(True)

    # ------------------------------------------------------------------ 关闭
    def shutdown(self, timeout_ms: int = 3000) -> TaskWorker | None:
        """窗口关闭时收尾：请求取消并等待；返回超时后仍在运行的线程。"""
        worker = self._worker
        # 不能用 isRunning() 判断：start() 后线程可能还没真正跑起来，
        # 此时 isRunning() 是 False，但线程仍会在稍后执行。
        if worker is None or worker.isFinished():
            return None
        if worker.stop(timeout_ms):
            return None
        return worker
