"""内置字体与英文字体偏好的测试。

注意：环境变量必须在 import PySide6 之前设置。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from english_learner.gui import fonts  # noqa: E402
from english_learner.gui.theme import build_stylesheet  # noqa: E402
from english_learner.services.config_store import (  # noqa: E402
    REVIEW_MODE_RECOGNITION,
    REVIEW_MODE_SPELLING,
    AIConfig,
    ConfigStore,
    UiConfig,
)

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_config() -> AIConfig:
    return AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test-model")


def test_font_files_exist() -> None:
    names = {path.name for path in fonts.FONT_DIR.glob("*.ttf")}
    assert {"Inter_400Regular.ttf", "Inter_600SemiBold.ttf", "Inter_700Bold.ttf"} <= names
    assert {"Lexend_400Regular.ttf", "Literata_400Regular.ttf"} <= names
    assert "Literata_400Regular_Italic.ttf" in names


def test_every_choice_has_font_files() -> None:
    """每个可选项都必须有对应的 TTF，否则用户切过去会看到回退字体。"""
    names = " ".join(path.name for path in fonts.FONT_DIR.glob("*.ttf"))
    for choice in fonts.ENGLISH_FONT_CHOICES:
        if choice.family == "Georgia":  # 系统自带，不打包
            continue
        assert f"{choice.family}_400Regular.ttf" in names, choice.key


def test_every_stack_keeps_chinese_fallback() -> None:
    # 英文字体不含 CJK 字形，字体链里必须留雅黑，否则中文会变成方块。
    for choice in fonts.ENGLISH_FONT_CHOICES:
        stack = fonts.english_font_stack(choice.key)
        assert stack.startswith(f'"{choice.family}"')
        assert "Microsoft YaHei UI" in stack
        # 族名 + 雅黑 + 通用族名，顺序不能乱。
        assert stack.endswith(choice.generic)


def test_unknown_font_key_falls_back_to_default() -> None:
    """配置写错 / 字体被删掉时不能崩，回退到默认字体。"""
    default = fonts.get_english_font(fonts.DEFAULT_ENGLISH_FONT)
    assert fonts.get_english_font("not-a-font") == default
    assert fonts.get_english_font("") == default
    assert fonts.get_english_font(None) == default


def test_default_font_is_lexend() -> None:
    assert fonts.DEFAULT_ENGLISH_FONT == "lexend"
    assert fonts.get_english_font(None).family == "Lexend"


def test_build_stylesheet_uses_selected_font_everywhere() -> None:
    """全局 QSS 的 `*` 规则必须用选中的字体链 —— 这是“所有英文统一”的唯一入口。"""
    css = build_stylesheet("literata")
    assert '"Literata", "Microsoft YaHei UI"' in css
    # * 规则（全应用默认字体）里出现的就是它，且没有残留占位符。
    star_rule = css.split("}")[0]
    assert '"Literata"' in star_rule
    assert "__ENGLISH_FONT__" not in css


def test_load_application_fonts_registers_all_families() -> None:
    get_app()
    families = fonts.load_application_fonts()
    assert {"Inter", "Lexend", "Literata"} <= set(families)


def test_ui_config_roundtrip_keeps_ai_section(tmp_path: Path) -> None:
    """保存字体偏好不能把 AI 配置冲掉。"""
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())

    store.save_ui(UiConfig(english_font="literata"))

    assert store.load_ui().english_font == "literata"
    assert store.load() == make_config()


def test_ui_config_reads_legacy_key(tmp_path: Path) -> None:
    """旧版本存的是 reading_font，升级后要能读出来。"""
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    payload = store._read_payload()
    payload["ui"] = {"reading_font": "georgia"}
    store._write_payload(payload)

    assert store.load_ui().english_font == "georgia"


def test_load_ui_defaults_when_file_missing(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "missing.json")
    assert store.load_ui().english_font == ""
    assert store.load_ui().review_mode == REVIEW_MODE_RECOGNITION


def test_ui_config_review_mode_roundtrip_and_invalid_fallback(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")

    store.save_ui(UiConfig(review_mode=REVIEW_MODE_SPELLING))
    assert store.load_ui().review_mode == REVIEW_MODE_SPELLING

    payload = store._read_payload()
    payload["ui"] = {"review_mode": "typing"}
    store._write_payload(payload)
    assert store.load_ui().review_mode == REVIEW_MODE_RECOGNITION


def test_settings_font_change_preserves_review_mode(tmp_path: Path) -> None:
    """改字体只改字体字段，不能把复习模式偏好冲掉。"""
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    store.save_ui(UiConfig(review_mode=REVIEW_MODE_SPELLING))

    from english_learner.gui.pages.settings import SettingsPage

    page = SettingsPage(store.load(), store)
    page._font_combo.setCurrentIndex(1)

    ui = store.load_ui()
    assert ui.english_font == fonts.ENGLISH_FONT_CHOICES[1].key
    assert ui.review_mode == REVIEW_MODE_SPELLING


def test_settings_page_switches_and_saves_english_font(tmp_path: Path) -> None:
    """下拉框切换后要立刻落盘，下次启动才能记住。"""
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())

    from english_learner.gui.pages.settings import SettingsPage

    page = SettingsPage(store.load(), store)
    assert page._font_combo.count() == len(fonts.ENGLISH_FONT_CHOICES)
    assert page._font_combo.currentData() == fonts.DEFAULT_ENGLISH_FONT

    page._font_combo.setCurrentIndex(1)
    assert store.load_ui().english_font == fonts.ENGLISH_FONT_CHOICES[1].key
