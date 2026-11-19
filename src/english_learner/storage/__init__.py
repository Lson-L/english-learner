"""存储层：SQLite 连接、建表迁移与仓库。只写 SQL，不依赖 PySide6。"""

from .db import SCHEMA_VERSION, Database, DuplicateError, StorageError
from .repositories import ArticleRepo, ProfileRepo, StatsRepo, WordRepo

__all__ = [
    "SCHEMA_VERSION",
    "ArticleRepo",
    "Database",
    "DuplicateError",
    "ProfileRepo",
    "StatsRepo",
    "StorageError",
    "WordRepo",
]
