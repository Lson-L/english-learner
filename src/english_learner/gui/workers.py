"""后台任务线程。

PySide6 的铁律：任何网络请求都不能在 UI 线程执行，否则窗口直接卡死。
这里用 QThread 包一层，任务函数在子线程跑，结果通过信号回到 UI 线程。

取消语义（M3）：
- `cancel()` 只是**协作式**取消：置标志并请求中断；
- 任务返回后如果已取消，就不发 `succeeded`，改发 `cancelled`，界面据此忽略结果；
- 长循环任务可以在循环里调用 `is_cancellation_requested()` 主动退出；
- 底层 SDK 的 HTTP 请求无法被打断，最多等它自己的超时（业务调用读超时 300s、
  连接 10s；连接测试 30s）；
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
import threading
import warnings
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..services.ai_client import AIError

logger = logging.getLogger(__name__)


def is_cancellation_requested() -> bool:
    """供长任务在循环里检查：当前线程是否已被请求取消。"""
    thread = QThread.currentThread()
    return isinstance(thread, TaskWorker) and thread.is_cancelled()


def retire_worker(worker: TaskWorker | None) -> bool:
    """把仍在运行的任务移出界面槽位，让用户立刻可以重试或做别的事。

    取消是协作式的：底层 HTTP 不会被打断，线程会继续跑到请求返回。这里做两件事：
    1. 断开成功 / 失败 / 取消信号，旧结果不会覆盖新任务的状态；
    2. 把 worker 放进全局列表，保证线程运行期间不会被 GC。
    """
    if worker is None or worker.isFinished():
        return False
    worker.cancel()
    # PySide 对“没有连接”的信号会发 RuntimeWarning 而不是抛异常，这里统一静音。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for signal in (worker.succeeded, worker.failed, worker.cancelled, worker.finished):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
    with _ORPHANS_LOCK:
        _ORPHANS[:] = [item for item in _ORPHANS if not item.isFinished()]
        _ORPHANS.append(worker)
    logger.info("后台任务已放弃，等待其网络请求自行结束")
    return True


def wait_for_orphans() -> None:
    """退出前等待被放弃的任务结束，避免 QThread 运行中被销毁。"""
    with _ORPHANS_LOCK:
        workers = list(_ORPHANS)
    for worker in workers:
        if not worker.isFinished():
            logger.info("退出前等待被放弃的后台任务…")
            worker.wait()


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


# 被界面“放弃”的任务：底层 HTTP 无法中断，线程会自己跑完。
# 这里保证它们在运行期间始终有引用，避免 QThread 被 GC 导致进程崩溃。
_ORPHANS: list[TaskWorker] = []
_ORPHANS_LOCK = threading.Lock()
