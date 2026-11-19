"""后台任务线程。

PySide6 的铁律：任何网络请求都不能在 UI 线程执行，否则窗口直接卡死。
这里用 QThread 包一层，任务函数在子线程跑，结果通过信号回到 UI 线程。

取消语义（M3）：
- `cancel()` 只是**协作式**取消：置标志并请求中断；
- 任务返回后如果已取消，就不发 `succeeded`，改发 `cancelled`，界面据此忽略结果；
- 长循环任务可以在循环里调用 `is_cancellation_requested()` 主动退出；
- 底层 SDK 的 HTTP 请求无法被打断，最多等它自己的超时（30s）；
  关闭窗口时 `stop()` 会先等几秒，实在等不到由 MainWindow 兜底等待。

Java 对照：
- TaskWorker ≈ new Thread(() -> ...) + Handler/回调；
- Signal ≈ 线程安全的回调注册，默认是 QueuedConnection，跨线程自动排队到 UI 线程。

用法（调用方必须持有 worker 引用，否则对象被 GC 回收，信号丢失）：

    self._worker = TaskWorker(lambda: client.test_connection())
    self._worker.succeeded.connect(self._on_success)
    self._worker.failed.connect(self._on_failed)
    self._worker.cancelled.connect(self._on_cancelled)
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


def is_cancellation_requested() -> bool:
    """供长任务在循环里检查：当前线程是否已被请求取消。"""
    thread = QThread.currentThread()
    return isinstance(thread, TaskWorker) and thread.is_cancelled()


class TaskWorker(QThread):
    """在子线程执行一个无参函数，成功 / 失败 / 取消各发一个信号。"""

    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, task: Callable[[], Any], parent: Any = None) -> None:
        super().__init__(parent)
        self._task = task
        self._cancel_requested = False

    # ------------------------------------------------------------------ 取消
    def cancel(self) -> None:
        """请求取消；不会强行终止线程。"""
        self._cancel_requested = True
        self.requestInterruption()

    def is_cancelled(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def stop(self, timeout_ms: int = 3000) -> bool:
        """取消并等待线程结束；返回是否在超时前停下。"""
        self.cancel()
        return self.wait(timeout_ms)

    # ------------------------------------------------------------------ 执行
    def run(self) -> None:  # noqa: D401 - Qt 约定的方法名
        if self.is_cancelled():
            self.cancelled.emit()
            return

        try:
            result = self._task()
        except AIError as exc:
            if self.is_cancelled():
                self.cancelled.emit()
            else:
                self.failed.emit(exc.display_message())
        except Exception as exc:  # noqa: BLE001 - 兜底，避免子线程异常静默丢失
            if self.is_cancelled():
                self.cancelled.emit()
            else:
                logger.exception("后台任务执行失败")
                self.failed.emit(f"发生未预期错误：{exc}")
        else:
            if self.is_cancelled():
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
