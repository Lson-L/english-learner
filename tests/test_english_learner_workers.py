"""TaskWorker 的取消语义测试（无网络，真实 QThread）。

信号用 DirectConnection 接到普通 list：槽在子线程执行，测试无需跑事件循环，
避免“信号发了但队列还没处理”的时序问题。
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.workers import TaskWorker, is_cancellation_requested  # noqa: E402
from english_learner.services.ai_client import AIError  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def collect(worker: TaskWorker) -> list[tuple[str, object]]:
    events: list[tuple[str, object]] = []
    worker.succeeded.connect(
        lambda result: events.append(("ok", result)),
        Qt.ConnectionType.DirectConnection,
    )
    worker.failed.connect(
        lambda message: events.append(("fail", message)),
        Qt.ConnectionType.DirectConnection,
    )
    worker.cancelled.connect(
        lambda: events.append(("cancel", None)),
        Qt.ConnectionType.DirectConnection,
    )
    return events


def test_task_success_emits_result() -> None:
    get_app()
    worker = TaskWorker(lambda: "done")
    events = collect(worker)

    worker.start()
    assert worker.wait(3000) is True

    assert events == [("ok", "done")]


def test_ai_error_is_translated_to_message() -> None:
    get_app()

    def task() -> str:
        raise AIError("连接失败", "请检查网络")

    worker = TaskWorker(task)
    events = collect(worker)
    worker.start()
    assert worker.wait(3000) is True

    assert events == [("fail", "连接失败\n请检查网络")]


def test_unexpected_exception_is_wrapped() -> None:
    get_app()
    worker = TaskWorker(lambda: (_ for _ in ()).throw(ValueError("bad")))
    events = collect(worker)
    worker.start()
    assert worker.wait(3000) is True

    assert len(events) == 1
    kind, message = events[0]
    assert kind == "fail"
    assert "发生未预期错误" in str(message)
    assert "bad" in str(message)


def test_cancel_before_start_emits_cancelled() -> None:
    get_app()
    worker = TaskWorker(lambda: "should not run")
    events = collect(worker)

    worker.cancel()
    worker.start()
    assert worker.wait(3000) is True

    assert events == [("cancel", None)]


def test_cancel_during_task_discards_result() -> None:
    get_app()
    started = threading.Event()

    def task() -> str:
        started.set()
        time.sleep(0.3)
        return "late result"

    worker = TaskWorker(task)
    events = collect(worker)
    worker.start()
    assert started.wait(2.0) is True

    worker.cancel()
    assert worker.wait(3000) is True

    assert events == [("cancel", None)]


def test_stop_cancels_and_waits() -> None:
    get_app()
    worker = TaskWorker(lambda: time.sleep(0.1) or "ok")
    events = collect(worker)
    worker.start()

    assert worker.stop(3000) is True
    assert events == [("cancel", None)]


def test_cancellation_helper_is_false_outside_cancel() -> None:
    get_app()
    worker = TaskWorker(lambda: is_cancellation_requested())
    events = collect(worker)
    worker.start()
    assert worker.wait(3000) is True

    assert events == [("ok", False)]
