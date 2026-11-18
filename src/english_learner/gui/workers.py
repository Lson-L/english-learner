"""后台任务线程。

PySide6 的铁律：任何网络请求都不能在 UI 线程执行，否则窗口直接卡死。
这里用 QThread 包一层，任务函数在子线程跑，结果通过信号回到 UI 线程。

Java 对照：
- TaskWorker ≈ new Thread(() -> ...) + Handler/回调；
- Signal ≈ 线程安全的回调注册，默认是 QueuedConnection，跨线程自动排队到 UI 线程。

用法（调用方必须持有 worker 引用，否则对象被 GC 回收，信号丢失）：

    self._worker = TaskWorker(lambda: client.test_connection())
    self._worker.succeeded.connect(self._on_success)
    self._worker.failed.connect(self._on_failed)
    self._worker.finished.connect(self._on_finished)
    self._worker.start()
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..services.ai_client import AIError

logger = logging.getLogger(__name__)


class TaskWorker(QThread):
    """在子线程执行一个无参函数，成功/失败各发一个信号。"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[], Any], parent: Any = None) -> None:
        super().__init__(parent)
        self._task = task

    def run(self) -> None:  # noqa: D401 - Qt 约定的方法名
        try:
            result = self._task()
        except AIError as exc:
            self.failed.emit(exc.display_message())
        except Exception as exc:  # noqa: BLE001 - 兜底，避免子线程异常静默丢失
            logger.exception("后台任务执行失败")
            self.failed.emit(f"发生未预期错误：{exc}")
        else:
            self.succeeded.emit(result)
