"""各功能页面。每个页面只负责展示，不直接发网络请求（交给 TaskWorker）。"""

from .placeholders import ReaderPage, ReviewPage, VocabularyPage
from .settings import SettingsPage
from .today import TodayPage

__all__ = [
    "ReaderPage",
    "ReviewPage",
    "SettingsPage",
    "TodayPage",
    "VocabularyPage",
]
