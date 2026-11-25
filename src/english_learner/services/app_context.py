"""应用级依赖容器（AppContext）。

Java 对照：≈ 一个极简的 Spring 容器——把配置、数据库、AI 客户端和各个
Service 组装好，页面只依赖它，不自己 new 这些东西，方便测试时整体替换。

约定：
- `config` 可通过 `set_config()` 更新（用户在设置页重新配置后）；
- 每次 `client()` / `*_service()` 都基于当前配置新建实例，配置一改立即生效；
- 本模块不依赖 PySide6，可以脱离界面单测。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from ..models.entities import Profile, today_str
from ..storage.db import Database
from ..storage.repositories import (
    AICallRepo,
    ArticleRepo,
    ContentSourceRepo,
    LevelEvidenceRepo,
    ProfileRepo,
    QuizRepo,
    RealTextRepo,
    ReviewRepo,
    StatsRepo,
    WeeklyReviewRepo,
    WordRepo,
    WritingAttemptRepo,
)
from .ai_client import BUSINESS_TIMEOUT, AIClient
from .article_service import ArticleService
from .assessment_service import AssessmentService
from .back_translation_service import BackTranslationService
from .config_store import AIConfig, ConfigStore
from .dictionary_service import DictionaryService
from .export_service import ExportService
from .learner_memory import LearnerMemoryService
from .level_feedback_service import LevelFeedbackService
from .level_progress import DifficultyHint, LevelProgress, build_level_progress
from .level_progress import difficulty_hint as build_difficulty_hint
from .metrics_service import MetricsService
from .readability import ReadabilityThresholds
from .reading_quiz_service import ReadingQuizService
from .real_text_library import load_corpus, seed_real_texts, validate_corpus
from .real_text_supply import RealTextSupplyService
from .review_help_service import ReviewHelpService
from .srs import ReviewService
from .stats_overview import StatsOverview, StatsOverviewService
from .weekly_review_service import WeeklyReviewService

logger = logging.getLogger(__name__)


class AppContext:
    def __init__(self, config: AIConfig, store: ConfigStore, db: Database) -> None:
        self.config = config.normalized()
        self.store = store
        self.db = db
        self._client: AIClient | None = None
        self._retired_clients: list[AIClient] = []
        self._client_lock = threading.Lock()

    # ------------------------------------------------------------------ 配置
    def set_config(self, config: AIConfig) -> None:
        """更新配置；旧客户端先退役，避免关闭正在被后台线程使用的连接。"""
        normalized = config.normalized()
        with self._client_lock:
            if self._client is not None and self._client.config != normalized:
                # 后台任务可能还持有旧 AIClient 的引用，立刻 close 会让请求失败；
                # 先放进退役列表，等应用退出、线程都结束后统一关闭。
                self._retired_clients.append(self._client)
                self._client = None
            self.config = normalized

    def client(self) -> AIClient:
        """返回可复用的 AIClient；一次检测的多轮请求共享同一个连接池。

        客户端注入调用记录仓库：每次 AI 请求的 token / 耗时 / 错误都落库，
        设置页用它展示用量。记录失败不会影响业务请求。

        业务调用使用 `BUSINESS_TIMEOUT`（读 300s）：带 thinking 的模型生成
        长文章 / 讲评时经常超过 30s；连接测试仍由调用方用默认的 30s 快速失败。
        """
        with self._client_lock:
            if self._client is None:
                self._client = AIClient(
                    self.config,
                    timeout=BUSINESS_TIMEOUT,
                    recorder=self.db.ai_calls,
                )
            return self._client

    def close(self) -> None:
        """关闭 AI 连接池（应用退出时调用，避免残留 socket）。"""
        with self._client_lock:
            clients = list(self._retired_clients)
            self._retired_clients.clear()
            if self._client is not None:
                clients.append(self._client)
                self._client = None
        for client in clients:
            client.close()

    # ------------------------------------------------------------------ 仓库
    @property
    def profile_repo(self) -> ProfileRepo:
        return self.db.profiles

    @property
    def article_repo(self) -> ArticleRepo:
        return self.db.articles

    @property
    def word_repo(self) -> WordRepo:
        return self.db.words

    @property
    def review_repo(self) -> ReviewRepo:
        return self.db.reviews

    @property
    def stats_repo(self) -> StatsRepo:
        return self.db.stats

    @property
    def quiz_repo(self) -> QuizRepo:
        return self.db.quizzes

    @property
    def level_evidence_repo(self) -> LevelEvidenceRepo:
        return self.db.evidence

    @property
    def weekly_review_repo(self) -> WeeklyReviewRepo:
        return self.db.weekly_reviews

    @property
    def ai_call_repo(self) -> AICallRepo:
        return self.db.ai_calls

    @property
    def writing_attempt_repo(self) -> WritingAttemptRepo:
        return self.db.writing_attempts

    @property
    def real_text_repo(self) -> RealTextRepo:
        return self.db.real_texts

    @property
    def content_source_repo(self) -> ContentSourceRepo:
        return self.db.content_sources

    # ------------------------------------------------------------ 启动任务
    def prepare_startup(self) -> None:
        """启动准备（M7.0 / M7.1）：种子 → 弃读结算 → 今日真实文章决策。

        全部是本地 SQLite 操作，不发网络请求；任何失败只记日志，
        绝不阻塞启动。内容为空（version <= 0）时静默跳过种子。
        """
        self._seed_real_texts()
        self._prepare_real_text_day()

    def _seed_real_texts(self) -> None:
        try:
            corpus = load_corpus()
        except Exception:  # noqa: BLE001 - 内容问题不能影响启动
            logger.warning("真实文章库加载失败，跳过种子", exc_info=True)
            return

        if corpus.version <= 0 or not corpus.texts:
            logger.info("真实文章库为空（version=%s），跳过种子", corpus.version)
            return

        errors = validate_corpus(corpus, require_levels=False)
        if errors:
            logger.warning("真实文章库校验未通过：%s", "; ".join(errors))
            return

        try:
            seed_real_texts(self.db, corpus)
        except Exception:  # noqa: BLE001 - 种子失败不影响 app 使用
            logger.warning("真实文章库种子失败", exc_info=True)

    def _prepare_real_text_day(self) -> None:
        """M7.1：结算超期弃读，再决定今天是否安排一篇真实文章。"""
        try:
            today = today_str()
            self.real_text_repo.expire_abandoned(today)
            profile = self.profile()
            config = self.store.load_real_text()
            if profile is None or not config.auto_enabled:
                return

            decision = self.real_text_supply_service().decide(profile, config, today=today)
            logger.info("真实文章供给决策：%s reason=%s", decision.action, decision.reason)
            if not decision.should_serve:
                if config.last_decision_on != today:
                    self.store.save_real_text(replace(config, last_decision_on=today))
                return

            article = self.article_service().serve_real_text(decision.text, today=today)
            if article is None:
                # 上架失败时当天回退 AI 文章；不写 last_decision_on，
                # 让用户重启后还有机会再试一次。
                return
            self.store.save_real_text(replace(config, last_decision_on=today))
            logger.info("真实文章已安排：%s", decision.text.id)
        except Exception:  # noqa: BLE001 - 供给失败必须回退 AI 文章
            logger.warning("真实文章供给失败，回退 AI 文章", exc_info=True)

    # ------------------------------------------------------------------ 服务
    def learner_memory_service(self) -> LearnerMemoryService:
        """M5.2 学习者记忆：纯本地派生，0 次 AI 调用。"""
        return LearnerMemoryService(
            self.profile_repo,
            self.review_repo,
            self.word_repo,
            self.weekly_review_repo,
            self.level_evidence_repo,
            self.writing_attempt_repo,
        )

    def assessment_service(self) -> AssessmentService:
        return AssessmentService(self.client(), self.profile_repo)

    def readability_thresholds(self) -> ReadabilityThresholds:
        """本地可读性阈值：config.json 的 `readability` 段，缺省用默认值。"""
        return self.store.load_readability()

    def article_service(self) -> ArticleService:
        memory = self.learner_memory_service()
        return ArticleService(
            self.client(),
            self.article_repo,
            self.word_repo,
            memory_provider=memory.build,
            hint_provider=self.difficulty_hint,
            readability=self.readability_thresholds(),
            # M7.1：真实文章上架时顺带种预置题目、写供给历史。
            quiz_seeder=lambda article, text: self.reading_quiz_service().seed_bundled_quiz(
                article, text
            ),
            real_text_repo=self.real_text_repo,
        )

    def dictionary_service(self) -> DictionaryService:
        memory = self.learner_memory_service()
        return DictionaryService(
            self.client(),
            self.word_repo,
            self.stats_repo,
            memory_provider=memory.build,
        )

    def level_feedback_service(self) -> LevelFeedbackService:
        return LevelFeedbackService(
            self.client(),
            self.profile_repo,
            self.article_repo,
            self.word_repo,
            hint_provider=self.difficulty_hint,
            readability=self.readability_thresholds(),
            # M7.2：调整难度替换真实文章时，由 ArticleService 统一记 overridden。
            real_text_repo=self.real_text_repo,
        )

    def reading_quiz_service(self) -> ReadingQuizService:
        """读完文章后的自测：自适应出题、判分、AI 讲评与本地后验调级。"""
        memory = self.learner_memory_service()
        return ReadingQuizService(
            self.client(),
            self.quiz_repo,
            self.profile_repo,
            self.level_evidence_repo,
            self.review_repo,
            memory_provider=memory.build,
            real_text_repo=self.real_text_repo,
        )

    def weekly_review_service(self) -> WeeklyReviewService:
        """每周复盘：汇总一周数据，AI 只建议，用户确认后才改等级。"""
        return WeeklyReviewService(
            self.client(),
            self.profile_repo,
            self.quiz_repo,
            self.review_repo,
            self.stats_repo,
            self.weekly_review_repo,
            self.word_repo,
            self.level_evidence_repo,
        )

    def review_service(self) -> ReviewService:
        """复习调度完全本地化：不注入 AI 客户端，断网也能用。"""
        return ReviewService(self.word_repo, self.review_repo, self.stats_repo)

    def back_translation_service(self) -> BackTranslationService:
        """回译练习：本地抽句 + 两次 AI 调用（出题翻译 / 批量批改）。"""
        return BackTranslationService(self.client(), self.writing_attempt_repo)

    def review_help_service(self) -> ReviewHelpService:
        """点“忘记/模糊”后的 AI 记忆帮助（可选，失败可跳过）。"""
        memory = self.learner_memory_service()
        return ReviewHelpService(
            self.client(),
            self.word_repo,
            memory_provider=memory.build,
        )

    def metrics_service(self) -> MetricsService:
        """M5.5 学习效果度量：纯本地聚合，0 次 AI 调用。"""
        return MetricsService(
            self.review_repo,
            self.stats_repo,
            self.quiz_repo,
            self.weekly_review_repo,
            self.ai_call_repo,
            self.writing_attempt_repo,
        )

    def stats_overview(self) -> StatsOverview:
        """统计页快照：热力图 / 累计曲线 / 阅读剂量全部本地计算，0 次 AI 调用。"""
        return StatsOverviewService(
            self.stats_repo,
            self.word_repo,
            self.quiz_repo,
            self.article_repo,
        ).build()

    def real_text_supply_service(self) -> RealTextSupplyService:
        """M7.1 自动供给决策：只读本地数据，0 次 AI / 0 次网络。"""
        return RealTextSupplyService(
            self.article_repo,
            self.stats_repo,
            self.quiz_repo,
            self.real_text_repo,
            self.word_repo,
        )

    def export_service(self) -> ExportService:
        """数据导出同样是纯本地操作。"""
        return ExportService(
            self.profile_repo,
            self.article_repo,
            self.word_repo,
            self.review_repo,
            self.stats_repo,
            self.quiz_repo,
            self.level_evidence_repo,
            self.weekly_review_repo,
            self.ai_call_repo,
            self.writing_attempt_repo,
            self.real_text_repo,
            self.content_source_repo,
        )

    # ------------------------------------------------------------ 等级进度
    def level_progress(self) -> LevelProgress | None:
        """今日页的档内进度（M6.4）：纯本地读取，0 次 AI 调用。"""
        profile = self.profile()
        if profile is None:
            return None
        # 进度只用正式检测；但“最近一次是否只做了练习”要看含练习的最新一条。
        quizzes = self.quiz_repo.list_recent_formal_completed(limit=8)
        known_ids = {quiz.id for quiz in quizzes}
        for quiz, _ in self.quiz_repo.list_recent_completed_with_titles(limit=1):
            if quiz.id not in known_ids:
                quizzes.append(quiz)
        return build_level_progress(
            profile,
            quizzes,
            self.review_repo.list_band_stats(),
            # M7：真实文章是预估难度，不参与档内把握 / 生词趋势口径（D7）。
            [a for a in self.article_repo.list_recent(limit=12) if _is_generated(a)],
        )

    def difficulty_hint(self) -> DifficultyHint:
        """文章档内位置（M6.5）：供文章生成服务注入，不直接暴露给界面。"""
        profile = self.profile()
        if profile is None:
            return DifficultyHint()
        thresholds = self.readability_thresholds()
        return build_difficulty_hint(
            profile,
            self.quiz_repo.list_recent_formal_completed(limit=8),
            [a for a in self.article_repo.list_recent(limit=6) if _is_generated(a)],
            max_unknown_ratio=thresholds.max_unknown_ratio,
        )

    # ------------------------------------------------------------------ 便捷
    def profile(self) -> Profile | None:
        return self.profile_repo.get()

    def level(self) -> str:
        """当前学习等级；没测评过时先按 B1 处理（只影响 prompt 难度）。"""
        profile = self.profile()
        return profile.cefr_level if profile is not None else "B1"


def _is_generated(article) -> bool:
    """M7：真实文章不参与档内进度 / 难度提示的聚合（D7）。"""
    return (getattr(article, "source", "") or "generated") == "generated"
