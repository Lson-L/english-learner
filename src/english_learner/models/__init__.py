"""领域模型包：只放数据结构，不依赖 PySide6、不写 SQL。"""

from .entities import (
    CEFR_LEVELS,
    WORD_STATUSES,
    Article,
    AssessmentResult,
    DailyStats,
    Highlight,
    Profile,
    Word,
    WordDefinition,
    dump_str_list,
    load_str_list,
    now_iso,
    today_str,
)

__all__ = [
    "CEFR_LEVELS",
    "WORD_STATUSES",
    "Article",
    "AssessmentResult",
    "DailyStats",
    "Highlight",
    "Profile",
    "Word",
    "WordDefinition",
    "dump_str_list",
    "load_str_list",
    "now_iso",
    "today_str",
]
