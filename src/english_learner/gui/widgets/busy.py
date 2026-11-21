"""共享的“AI 处理中”提示：一个不确定进度的进度条。

各页面布局差异较大，取消按钮仍由调用方自己提供，这里只统一进度条的
外观与初始状态，避免每个页面重复写 QProgressBar 配置。
"""

from __future__ import annotations

from PySide6.QtWidgets import QProgressBar, QWidget


def busy_progress_bar(parent: QWidget | None = None, *, width: int = 120) -> QProgressBar:
    """创建默认隐藏的不确定进度条；调用方用 setVisible 控制显隐。"""
    bar = QProgressBar(parent)
    bar.setObjectName("busyProgress")
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    bar.setFixedWidth(width)
    bar.setVisible(False)
    return bar
