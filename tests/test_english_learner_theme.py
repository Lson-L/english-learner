"""低调主题（深色 + 等宽）与界面偏好解析的测试。

注意：环境变量必须在 import PySide6 之前设置。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCharFormat  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton  # noqa: E402

from english_learner.gui import fonts  # noqa: E402
from english_learner.gui.i18n import refresh_buttons, set_low_profile  # noqa: E402
from english_learner.gui.main_window import MainWindow  # noqa: E402
from english_learner.gui.pages.settings import SettingsPage  # noqa: E402
from english_learner.gui.theme import LIGHT_PALETTE, LOW_PROFILE_PALETTE, build_stylesheet  # noqa: E402
from english_learner.gui.widgets.article_view import ArticleView  # noqa: E402
from english_learner.models.entities import Article, Highlight  # noqa: E402
from english_learner.services.config_store import (  # noqa: E402
    AIConfig,
    ConfigStore,
    UiConfig,
)
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def make_config() -> AIConfig:
    return AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test-model")


# ------------------------------------------------------------------ 调色板
def test_both_palettes_define_every_placeholder() -> None:
    """两套调色板的 key 必须一致，否则某个主题会残留 `__X__` 占位符。"""
    assert set(LIGHT_PALETTE) == set(LOW_PROFILE_PALETTE)


def test_stylesheets_have_no_unresolved_placeholders() -> None:
    for low_profile in (False, True):
        css = build_stylesheet("lexend", low_profile=low_profile)
        assert "__" not in css


def test_light_stylesheet_keeps_original_colors_and_font() -> None:
    css = build_stylesheet("literata")
    assert "#F5F7FA" in css  # 窗口背景
    assert '"Literata"' in css
    assert "Consolas" not in css


def test_low_profile_stylesheet_is_dark_and_mono() -> None:
    css = build_stylesheet("literata", low_profile=True)
    assert "#1E1E1E" in css  # VS Code 风格窗口背景
    assert "#F5F7FA" not in css  # 浅色主题的窗口背景不能残留
    assert '"Consolas"' in css
    # 低调主题强制等宽，用户选的无衬线/衬线字体不再出现在字体链里。
    assert '"Literata"' not in css and '"Lexend"' not in css


def test_radio_indicators_have_explicit_checked_style() -> None:
    """选择题的选中态不能依赖系统默认圆点：两套主题都要有显式 indicator 样式。"""
    for low_profile in (False, True):
        css = build_stylesheet("lexend", low_profile=low_profile)
        assert "QRadioButton::indicator:checked" in css
        assert "QRadioButton::indicator:hover" in css
        assert "QRadioButton:checked" in css


def test_mono_stack_keeps_chinese_fallback() -> None:
    stack = fonts.font_stack("literata", mono=True)
    assert stack.startswith('"Consolas"')
    assert "Microsoft YaHei UI" in stack
    assert stack.endswith("monospace")
    assert fonts.font_families("literata", mono=True)[0] == "Consolas"


def test_article_view_switches_to_mono_font() -> None:
    get_app()
    view = ArticleView()
    view.set_english_font("literata", mono=True)
    assert view.document().defaultFont().families()[0] == "Consolas"


def test_highlight_colors_are_theme_aware() -> None:
    """暗色主题下生词高亮必须同时换背景和前景，否则浅色正文落在浅黄底上看不清。"""
    get_app()
    view = ArticleView()
    view.set_article(
        Article(article_date="2026-11-21", title="t", content="hello world", level="B1")
    )
    view.set_highlights([Highlight(article_id=1, word_id=1, start_offset=0, end_offset=5)])

    # ExtraSelection.format 只是 Qt 对象的临时包装，先拷成独立副本再读。
    light_selections = view.extraSelections()
    light = QTextCharFormat(light_selections[0].format)
    assert light.background().color().name().upper() == "#FDE68A"
    assert light.foreground().color().name().upper() == "#111827"

    view.set_low_profile(True)

    assert view.highlight_count() == 1
    dark_selections = view.extraSelections()
    dark = QTextCharFormat(dark_selections[0].format)
    assert dark.background().color().name().upper() == "#5C4A22"
    assert dark.foreground().color().name().upper() == "#FFE9A8"


# -------------------------------------------------------------- 配置解析
def test_ui_config_parses_low_profile() -> None:
    assert UiConfig.from_dict({}).low_profile is False
    assert UiConfig.from_dict({"low_profile": True}).low_profile is True
    assert UiConfig.from_dict({"low_profile": "true"}).low_profile is True
    # 手写 JSON 里常见的 "false" 不能被 bool() 误判成 True。
    assert UiConfig.from_dict({"low_profile": "false"}).low_profile is False


def test_ui_config_roundtrip_keeps_low_profile(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())

    store.save_ui(UiConfig(low_profile=True))

    assert store.load_ui().low_profile is True
    assert store.load() == make_config()


# ------------------------------------------------------------------ 设置页
def test_settings_theme_switch_saves_and_disables_font_combo(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    page = SettingsPage(store.load(), store)
    emitted: list[bool] = []
    page.theme_changed.connect(emitted.append)

    page._theme_combo.setCurrentIndex(1)

    assert store.load_ui().low_profile is True
    assert emitted == [True]
    assert page._font_combo.isEnabled() is False

    page._theme_combo.setCurrentIndex(0)

    assert store.load_ui().low_profile is False
    assert emitted == [True, False]
    assert page._font_combo.isEnabled() is True


# ------------------------------------------------------------------ 按钮语言
def test_low_profile_translates_buttons_and_restores() -> None:
    get_app()
    button = QPushButton()
    button.setText("显示答案（空格）")
    assert button.text() == "显示答案（空格）"

    set_low_profile(True)
    refresh_buttons(button)
    assert button.text() == "Show answer (Space)"

    # 页面代码之后再次写中文，也要立即显示英文（动态按钮）。
    button.setText("继续")
    assert button.text() == "Continue"

    # 带数字的动态文案走模式替换。
    button.setText("加练一轮（5 个）")
    assert button.text() == "Extra round (5)"

    set_low_profile(False)
    refresh_buttons(button)
    assert button.text() == "加练一轮（5 个）"


def test_checkbox_labels_are_translated_too() -> None:
    """复选框同样属于按钮类控件，低调模式下也要英文。"""
    get_app()
    checkbox = QCheckBox()
    checkbox.setText("自动刷新")
    set_low_profile(True)
    try:
        refresh_buttons(checkbox)
        assert checkbox.text() == "Auto refresh"
    finally:
        set_low_profile(False)
        refresh_buttons(checkbox)


def test_untranslated_button_text_is_left_alone() -> None:
    get_app()
    set_low_profile(True)
    try:
        button = QPushButton()
        button.setText("自定义按钮")
        assert button.text() == "自定义按钮"
    finally:
        set_low_profile(False)


# ------------------------------------------------------------------ 主窗口
def test_main_window_starts_in_low_profile_with_neutral_copy(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    store.save_ui(UiConfig(low_profile=True))
    db = Database(tmp_path / "data.db")

    window = MainWindow(make_config(), store, db)

    assert window.windowTitle() == "Workspace"
    assert window._brand_label.text() == "Workspace"
    assert [button.text() for button in window._nav_buttons] == ["Today", "Docs", "Words", "Review"]
    assert window._settings_button.text() == "Settings"
    # 低调模式下所有页面按钮都用英文。
    assert window._reader_page._generate_button.text() == "Generate article"
    assert window._reader_page._quiz_button.text() == "Finish reading"
    assert "Daily English" not in window._footer_label.text()
    # 生词本页面顶部的大标题也要去掉，只保留下面的说明文字。
    assert window._vocabulary_page._header._title_label.isHidden() is True
    window.close()
    # 全局开关会影响之后创建的按钮，测试结束复位，避免污染其它用例。
    set_low_profile(False)


def test_main_window_switches_theme_live(tmp_path: Path) -> None:
    get_app()
    store = ConfigStore(tmp_path / "config.json")
    store.save(make_config())
    db = Database(tmp_path / "data.db")
    window = MainWindow(make_config(), store, db)
    assert window.windowTitle() == "Daily English · 每日英语"
    assert window._vocabulary_page._header._title_label.isHidden() is False

    window._settings_page._theme_combo.setCurrentIndex(1)

    assert store.load_ui().low_profile is True
    assert window.windowTitle() == "Workspace"
    assert [button.text() for button in window._nav_buttons] == ["Today", "Docs", "Words", "Review"]
    css = QApplication.instance().styleSheet()
    assert "#1E1E1E" in css and '"Consolas"' in css
    assert window._reader_page._view._low_profile is True
    assert window._vocabulary_page._header._title_label.isHidden() is True
    assert window._reader_page._generate_button.text() == "Generate article"

    window._settings_page._theme_combo.setCurrentIndex(0)
    assert window._reader_page._generate_button.text() == "生成今日文章"
    window.close()
