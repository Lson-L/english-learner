"""主窗口几何状态：配置读写、失效快照兜底、MainWindow 关闭后记住。

环境变量必须在 import PySide6 之前设置。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from english_learner.gui.main_window import MainWindow  # noqa: E402
from english_learner.gui.window_state import (  # noqa: E402
    DEFAULT_WINDOW_SIZE,
    capture_window_state,
    restore_window_state,
)
from english_learner.services.config_store import (  # noqa: E402
    AIConfig,
    ConfigStore,
    WindowState,
)
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_config() -> AIConfig:
    return AIConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )


def make_store(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    return store


class ModelWindow(QMainWindow):
    """不受 MainWindow 最小尺寸约束，方便验证几何往返。"""


# ---------------------------------------------------------------- 纯数据
def test_window_state_roundtrip() -> None:
    state = WindowState(geometry="QUJD")
    assert WindowState.from_dict(state.to_dict()) == state


def test_window_state_defaults_when_missing() -> None:
    assert WindowState.from_dict({}) == WindowState()
    assert WindowState.from_dict({"geometry": None}) == WindowState()


def test_window_state_normalizes_non_string_and_whitespace() -> None:
    assert WindowState(geometry=123).normalized() == WindowState()  # type: ignore[arg-type]
    assert WindowState(geometry="  QUJD \n").normalized() == WindowState(geometry="QUJD")


# ---------------------------------------------------------------- 配置文件
def test_config_store_window_section_roundtrip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    saved = store.save_window_state(WindowState(geometry="QUJD"))

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["window"] == {"geometry": "QUJD"}
    assert store.load_window_state() == saved


def test_saving_window_state_keeps_ai_and_ui_sections(tmp_path: Path) -> None:
    """写窗口状态不能把 AI / 界面偏好冲掉。"""
    store = make_store(tmp_path)
    store.save_ui(store.load_ui())
    store.save_window_state(WindowState(geometry="QUJD"))

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["ai"]["model"] == "test-model"
    assert "ui" in payload


def test_load_window_state_falls_back_on_missing_or_corrupt_file(tmp_path: Path) -> None:
    assert ConfigStore(tmp_path / "missing.json").load_window_state() == WindowState()

    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    assert ConfigStore(path).load_window_state() == WindowState()


# ---------------------------------------------------------------- Qt 辅助
def test_restore_empty_state_uses_default_size() -> None:
    get_app()
    window = ModelWindow()

    restore_window_state(window, WindowState())

    assert window.size() == DEFAULT_WINDOW_SIZE


@pytest.mark.parametrize("geometry", ["not-base64!!", "@@@@", "5L2g5aW9"])
def test_restore_invalid_snapshot_falls_back_to_default_size(geometry: str) -> None:
    get_app()
    window = ModelWindow()

    restore_window_state(window, WindowState(geometry=geometry))

    assert window.size() == DEFAULT_WINDOW_SIZE


def test_geometry_roundtrip_including_position() -> None:
    app = get_app()
    window = ModelWindow()
    window.resize(720, 520)
    window.move(30, 40)
    window.show()
    app.processEvents()
    expected_frame = window.frameGeometry()
    state = capture_window_state(window)
    window.close()

    restored = ModelWindow()
    restore_window_state(restored, state)
    restored.show()
    app.processEvents()

    # 比较窗口框架而不是客户区：Qt 的快照带框架边距，隐藏窗口的 pos 会差 2px。
    assert restored.frameGeometry() == expected_frame
    restored.close()


def test_geometry_roundtrip_keeps_maximized_state() -> None:
    app = get_app()
    window = ModelWindow()
    window.resize(720, 520)
    window.showMaximized()
    app.processEvents()
    state = capture_window_state(window)
    assert state.geometry
    window.close()

    restored = ModelWindow()
    restore_window_state(restored, state)

    assert restored.isMaximized()


# ---------------------------------------------------------------- 主窗口
def test_main_window_remembers_geometry_across_restart(tmp_path: Path) -> None:
    """关闭时存快照，下次启动恢复：高度来自快照，而不是首次启动的默认值。"""
    app = get_app()
    store = make_store(tmp_path)
    db = Database(tmp_path / "data.db")
    window = MainWindow(make_config(), store, db)
    window.resize(1160, 760)
    window.move(120, 90)
    window.show()
    app.processEvents()
    window.close()

    state = store.load_window_state()
    assert state.geometry  # 关闭时确实写了快照

    # offscreen 屏幕只有 800x800，restoreGeometry 会把宽度夹到屏幕内；
    # 高度（760）仍能证明恢复用的是快照，而不是默认 1240x800。
    db = Database(tmp_path / "data.db")
    restored = MainWindow(make_config(), store, db)
    try:
        assert restored.size().height() == 760
        assert restored.size() != DEFAULT_WINDOW_SIZE
    finally:
        restored.close()


def test_main_window_first_run_uses_default_size(tmp_path: Path) -> None:
    get_app()
    store = make_store(tmp_path)
    db = Database(tmp_path / "data.db")
    window = MainWindow(make_config(), store, db)
    try:
        assert window.size() == DEFAULT_WINDOW_SIZE
    finally:
        window.close()


def test_main_window_close_ignored_by_page_does_not_save(tmp_path: Path) -> None:
    """生词本拦截未保存释义时窗口不会关闭，也不该把当前几何写进配置。"""
    get_app()
    store = make_store(tmp_path)
    db = Database(tmp_path / "data.db")
    window = MainWindow(make_config(), store, db)
    window.resize(1150, 740)

    class Blocker:
        def can_deactivate(self) -> bool:
            return False

    pages = list(window._pages)
    index = window._stack.currentIndex()
    original = pages[index]
    pages[index] = Blocker()  # type: ignore[assignment]
    window._pages = tuple(pages)

    window.close()
    assert store.load_window_state() == WindowState()

    # 还原真实页面后正常关闭，释放数据库与后台任务。
    pages[index] = original
    window._pages = tuple(pages)
    window.close()


def test_main_window_saved_snapshot_is_valid_base64(tmp_path: Path) -> None:
    """`window.geometry` 写进 JSON 前必须是 ASCII，避免配置里出现乱码。"""
    get_app()
    store = make_store(tmp_path)
    db = Database(tmp_path / "data.db")
    window = MainWindow(make_config(), store, db)
    window.show()
    get_app().processEvents()
    window.close()

    geometry = store.load_window_state().geometry
    assert geometry.isascii()
    assert base64.b64decode(geometry)
