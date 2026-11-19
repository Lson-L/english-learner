"""AI JSON 兜底解析的单元测试。"""

from __future__ import annotations

import pytest

from english_learner.services.json_utils import (
    JsonParseError,
    as_int,
    as_str_list,
    as_text,
    parse_json_object,
)


def test_plain_json() -> None:
    assert parse_json_object('{"cefr": "B1"}') == {"cefr": "B1"}


def test_fenced_json() -> None:
    text = '```json\n{"title": "Hello", "level": "B1"}\n```'
    assert parse_json_object(text)["title"] == "Hello"


def test_fence_without_language() -> None:
    text = '```\n{"a": 1}\n```'
    assert parse_json_object(text) == {"a": 1}


def test_json_with_leading_and_trailing_noise() -> None:
    text = '好的，以下是结果：\n{"a": [1, 2], "b": null}\n希望有帮助！'
    assert parse_json_object(text) == {"a": [1, 2], "b": None}


def test_non_object_json_is_rejected() -> None:
    with pytest.raises(JsonParseError):
        parse_json_object("[1, 2, 3]")
    with pytest.raises(JsonParseError):
        parse_json_object("just some words")


def test_empty_text_is_rejected() -> None:
    with pytest.raises(JsonParseError):
        parse_json_object("   ")


def test_field_coercion() -> None:
    assert as_text("  hi  ") == "hi"
    assert as_text(None, "fallback") == "fallback"
    assert as_int("3800") == 3800
    assert as_int("3800.0") == 3800
    assert as_int(object(), 7) == 7
    assert as_int(True, 9) == 9
    assert as_str_list(["a", " ", "b"]) == ("a", "b")
    assert as_str_list('["x", "y"]') == ("x", "y")
    assert as_str_list("solo") == ("solo",)
    assert as_str_list(None) == ()
