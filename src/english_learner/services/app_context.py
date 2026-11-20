"""应用级依赖容器（AppContext）。

Java 对照：≈ 一个极简的 Spring 容器——把配置、数据库、AI 客户端和各个
Service 组装好，页面只依赖它，不自己 new 这些东西，方便测试时整体替换。

约定：
- `config` 可通过 `set_config()` 更新（用户在设置页重新配置后）；
- 每次 `client()` / `*_service()` 都基于当前配置新建实例，配置一改立即生效；
- 本模块不依赖 PySide6，可以脱离界面单测。
"""

from __future__ import annotations

import threading

from ..models.entities import Profile
from ..storage.db import Database
from ..storage.repositories import (
    AICallRepo,
    ArticleRepo,
    LevelEvidenceRepo,
    ProfileRepo,
    QuizRepo,
    ReviewRepo,
    StatsRepo,
    WeeklyReviewRepo,
    WordRepo,
    WritingAttemptRepo,
)
from .ai_client import AIClient
from .article_service import ArticleService
from .assessment_service import AssessmentService
from .back_translation_service import BackTranslationService
from .config_store import AIConfig, ConfigStore
from .dictionary_service import DictionaryService
from .export_service import ExportService
from .learner_memory import LearnerMemoryService
from .level_feedback_service import LevelFeedbackService
from .metrics_service import MetricsService
from .readability import ReadabilityThresholds
from .reading_quiz_service import ReadingQuizService
from .review_help_service import ReviewHelpService
from .srs import ReviewService
from .weekly_review_service import WeeklyReviewService


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
        """
        with self._client_lock:
            if self._client is None:
                self._client = AIClient(self.config, recorder=self.db.ai_calls)
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
            readability=self.readability_thresholds(),
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
            readability=self.readability_thresholds(),
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
        )

    # ------------------------------------------------------------------ 便捷
    def profile(self) -> Profile | None:
        return self.profile_repo.get()

    def level(self) -> str:
        """当前学习等级；没测评过时先按 B1 处理（只影响 prompt 难度）。"""
        profile = self.profile()
        return profile.cefr_level if profile is not None else "B1"
