"""各功能页面。每个页面只负责展示，不直接发网络请求（交给 TaskWorker）。"""

from .reader import ReaderPage
from .review import ReviewPage
from .settings import SettingsPage
from .today import TodayPage
from .vocabulary import VocabularyPage

__all__ = [
    "ReaderPage",
    "ReviewPage",
    "SettingsPage",
    "TodayPage",
    "VocabularyPage",
]
