"""纯 SQL 仓库：每个表一个 Repository。

Java 对照：
- 本文件 ≈ MyBatis 的 Mapper 接口 + XML，只是 SQL 以 Python 字符串形式内联；
- Repository 不写业务判断，只做“对象 ⇄ 行”的搬运；
- 事务边界由 Database.transaction() 提供，业务层负责组合多个仓库调用。

命名约定：
- `get_*` / `list_*` / `count_*` 只读；
- `insert_*` / `save_*` / `upsert_*` / `bump_*` 写入；
- 查不到时返回 None 或空列表，不抛异常；违反唯一约束抛 DuplicateError。
"""

from __future__ import annotations

from .articles import ArticleRepo
from .levels import LevelEvidenceRepo, WeeklyReviewRepo
from .profile import ProfileRepo
from .quizzes import QuizRepo
from .real_texts import ContentSourceRepo, RealTextRepo
from .review import ReviewRepo
from .stats import AICallRepo, StatsRepo
from .words import WordRepo
from .writing import WritingAttemptRepo

__all__ = [
    "AICallRepo",
    "ArticleRepo",
    "ContentSourceRepo",
    "LevelEvidenceRepo",
    "ProfileRepo",
    "QuizRepo",
    "RealTextRepo",
    "ReviewRepo",
    "StatsRepo",
    "WeeklyReviewRepo",
    "WordRepo",
    "WritingAttemptRepo",
]
