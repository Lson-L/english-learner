"""主窗口几何状态的恢复与采集。

偏好本身（`WindowState`）定义在 `services.config_store`，这里只做 PySide6 与
纯数据之间的转换，方便 services 层保持不依赖 Qt。

为什么不自己存 width/x/maximized：这些坐标带窗口框架（标题栏、边框），
而 `setGeometry()` 用的是客户区坐标，手写换算会每次启动偏移一个标题栏高度。
`saveGeometry()/restoreGeometry()` 由 Qt 负责框架换算、最大化状态和多屏兜底。

Java 对照：类似把窗口 bounds 交给一个 `WindowStateCodec`，窗口类只负责在
创建 / 关闭时各调一次。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize
from PySide6.QtWidgets import QMainWindow

from ..services.config_store import DEFAULT_WINDOW_HEIGHT, DEFAULT_WINDOW_WIDTH, WindowState

DEFAULT_WINDOW_SIZE = QSize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)


def restore_window_state(window: QMainWindow, state: WindowState) -> None:
    """恢复上次关闭时的几何；没有记录或数据失效时回退默认尺寸。

    `restoreGeometry()` 返回 False 表示快照无法解析（手改坏了配置），
    这时必须显式 resize —— 否则窗口会停在 Qt 的默认小尺寸上。
    调用时机与 `saveGeometry()` 对称：在 `show()` 之前。
    """
    blob = _decode(state.normalized().geometry)
    if blob is None or not window.restoreGeometry(blob):
        window.resize(DEFAULT_WINDOW_SIZE)


def capture_window_state(window: QMainWindow) -> WindowState:
    """采集当前窗口几何：尺寸、位置、最大化 / 全屏状态都在快照里。"""
    return WindowState(geometry=bytes(window.saveGeometry().toBase64()).decode("ascii"))


def _decode(text: str) -> QByteArray | None:
    """base64 → QByteArray；非 ASCII 或非法 base64 返回 None。"""
    if not text:
        return None
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError:
        return None
    blob = QByteArray.fromBase64(raw)
    return blob if not blob.isEmpty() else None
