"""多套 AI 配置：设置页切换、向导新增 / 编辑（offscreen，无网络）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui.onboarding import OnboardingDialog  # noqa: E402
from english_learner.gui.pages.settings import SettingsPage  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_config(model: str = "model-a", api_key: str = "sk-test") -> AIConfig:
    return AIConfig(base_url="https://api.example.com/v1", api_key=api_key, model=model)


def test_settings_page_switches_profile_and_updates_context(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config("model-a"))
    first = store.load_profiles().active()
    assert first is not None
    second = store.add_profile(make_config("model-b"), name="备用").active()
    assert second is not None
    # add_profile 会把新配置设为当前，这里切回第一套作为初始状态。
    store.activate_profile(first.id)

    db = Database(tmp_path / "data.db")
    context = AppContext(first.config, store, db)
    page = SettingsPage(first.config, store, context)

    assert page._profile_combo.count() == 2
    assert page._model_value.text() == "model-a"

    changed: list[AIConfig] = []
    page.config_changed.connect(changed.append)
    index = page._profile_combo.findData(second.id)
    page._profile_combo.setCurrentIndex(index)

    assert store.load_profiles().active_id == second.id
    assert context.config.model == "model-b"
    assert page._model_value.text() == "model-b"
    assert changed and changed[-1].model == "model-b"
    assert "已切换" in page._status.text()
    db.close()


def test_onboarding_creates_and_edits_named_profile(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config("model-a"))
    first = store.load_profiles().active()
    assert first is not None

    create = OnboardingDialog(store)
    create._tested_config = make_config("model-b")
    create._name_edit.setText("第二套")
    create._save_and_accept()

    profiles = store.load_profiles()
    assert len(profiles.profiles) == 2
    active = profiles.active()
    assert active is not None
    assert active.name == "第二套"
    assert create.saved_profiles is not None
    assert create.saved_config is not None and create.saved_config.model == "model-b"

    edit = OnboardingDialog(store, profile=profiles.active())
    assert edit._name_edit.text() == "第二套"
    edit._tested_config = make_config("model-c")
    edit._name_edit.setText("改名")
    edit._save_and_accept()

    updated = store.load_profiles()
    assert len(updated.profiles) == 2
    edited = updated.find(active.id)
    assert edited is not None
    assert edited.name == "改名"
    assert edited.config.model == "model-c"
    # 原来那套没被删除改坏。
    original = updated.find(first.id)
    assert original is not None and original.config.model == "model-a"


def test_delete_only_profile_is_prevented_in_settings_page(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    page = SettingsPage(store.load(), store)

    assert page._delete_profile_button.isEnabled() is False
    page._on_delete_profile()
    assert len(store.load_profiles().profiles) == 1


def test_profile_edit_request_carries_active_id(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    active = store.load_profiles().active()
    assert active is not None
    page = SettingsPage(store.load(), store)

    requested: list[str] = []
    page.profile_edit_requested.connect(requested.append)
    page._edit_profile_button.click()

    assert requested == [active.id]
