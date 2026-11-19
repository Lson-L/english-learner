"""应用级依赖容器（AppContext）。

Java 对照：≈ 一个极简的 Spring 容器——把配置、数据库、AI 客户端和各个
Service 组装好，页面只依赖它，不自己 new 这些东西，方便测试时整体替换。

约定：
- `config` 可通过 `set_config()` 更新（用户在设置页重新配置后）；
- 每次 `client()` / `*_service()` 都基于当前配置新建实例，配置一改立即生效；
- 本模块不依赖 PySide6，可以脱离界面单测。
"""

from __future__ import annotations

from ..models.entities import Profile
from ..storage.db import Database
from ..storage.repositories import (
    ArticleRepo,
    LevelEvidenceRepo,
    ProfileRepo,
    QuizRepo,
    ReviewRepo,
    StatsRepo,
    WeeklyReviewRepo,
    WordRepo,
)
from .ai_client import AIClient
from .article_service import ArticleService
from .assessment_service import AssessmentService
from .config_store import AIConfig, ConfigStore
from .dictionary_service import DictionaryService
from .export_service import ExportService
from .level_feedback_service import LevelFeedbackService
from .reading_quiz_service import ReadingQuizService
from .review_help_service import ReviewHelpService
from .srs import ReviewService
from .weekly_review_service import WeeklyReviewService


class AppContext:
    def __init__(self, config: AIConfig, store: ConfigStore, db: Database) -> None:
        self.config = config.normalized()
        self.store = store
        self.db = db

    # ------------------------------------------------------------------ 配置
    def set_config(self, config: AIConfig) -> None:
        self.config = config.normalized()

    def client(self) -> AIClient:
        return AIClient(self.config)

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

    # ------------------------------------------------------------------ 服务
    def assessment_service(self) -> AssessmentService:
        return AssessmentService(self.client(), self.profile_repo)

    def article_service(self) -> ArticleService:
        return ArticleService(self.client(), self.article_repo, self.word_repo)

    def dictionary_service(self) -> DictionaryService:
        return DictionaryService(self.client(), self.word_repo, self.stats_repo)

    def level_feedback_service(self) -> LevelFeedbackService:
        return LevelFeedbackService(
            self.client(), self.profile_repo, self.article_repo, self.word_repo
        )

    def reading_quiz_service(self) -> ReadingQuizService:
        """读完文章后的自测：自适应出题、判分、AI 讲评与本地后验调级。"""
        return ReadingQuizService(
            self.client(),
            self.quiz_repo,
            self.profile_repo,
            self.level_evidence_repo,
            self.review_repo,
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

    def review_help_service(self) -> ReviewHelpService:
        """点“忘记/模糊”后的 AI 记忆帮助（可选，失败可跳过）。"""
        return ReviewHelpService(self.client(), self.word_repo)

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
        )

    # ------------------------------------------------------------------ 便捷
    def profile(self) -> Profile | None:
        return self.profile_repo.get()

    def level(self) -> str:
        """当前学习等级；没测评过时先按 B1 处理（只影响 prompt 难度）。"""
        profile = self.profile()
        return profile.cefr_level if profile is not None else "B1"
