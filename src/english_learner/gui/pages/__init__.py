"""各功能页面。每个页面只负责展示，不直接发网络请求（交给 TaskWorker）。

这里用模块级 `__getattr__` 做**按需导入**：弹窗模块会 import `pages.common`
（Card / divider / set_status），如果本文件在导入时就急切加载所有页面，
会形成 “弹窗 → pages 包 → reader → 弹窗” 的循环导入，单独跑某个测试时直接失败。
按需加载后，`from .pages import ReaderPage` 仍然照常工作。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ReaderPage",
    "ReviewPage",
    "SettingsPage",
    "TodayPage",
    "VocabularyPage",
]

_EXPORTS: dict[str, str] = {
    "ReaderPage": ".reader",
    "ReviewPage": ".review",
    "SettingsPage": ".settings",
    "TodayPage": ".today",
    "VocabularyPage": ".vocabulary",
}


def __getattr__(name: str) -> Any:
    """首次访问页面类时才导入对应模块，并缓存到 globals()。"""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
