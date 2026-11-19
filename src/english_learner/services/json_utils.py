"""AI 返回 JSON 的兜底解析与字段容错。

背景：我们要求模型“只输出 JSON”，但各家兼容网关的表现并不一致：
- 有的老老实实返回 `{"title": ...}`；
- 有的套一层 ```json 围栏；
- 有的在 JSON 前后加一句“好的，以下是结果：”。

所以业务层不能直接 `json.loads`，统一走 `parse_json_object`：
原样 → 去围栏 → 截取首个 `{` 到最后一个 `}`，三档依次尝试。
"""

from __future__ import annotations

import json
import re
from typing import Any

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class JsonParseError(ValueError):
    """AI 返回的内容无法解析成 JSON 对象。"""


def parse_json_object(text: str) -> dict[str, Any]:
    """把 AI 文本解析成 dict；失败抛 JsonParseError。"""
    raw = (text or "").strip()
    if not raw:
        raise JsonParseError("AI 返回了空内容")

    for candidate in _candidates(raw):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data

    raise JsonParseError("AI 返回的内容不是合法 JSON 对象")


def _candidates(raw: str):
    """按可信度从高到低给出候选文本。"""
    yield raw

    fence = _CODE_FENCE_RE.search(raw)
    if fence:
        yield fence.group(1).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        yield raw[start : end + 1]


# ------------------------------------------------------------ 字段类型转换
def as_text(value: Any, default: str = "") -> str:
    """任何值 → 去掉首尾空白的字符串；None / 空值回退默认值。"""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def as_int(value: Any, default: int = 0) -> int:
    """尽量转 int：兼容 3800、"3800"、"3800.0"；失败用默认值。"""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return default


def as_str_list(value: Any) -> tuple[str, ...]:
    """任何值 → 字符串元组：支持列表、JSON 数组字符串、普通字符串。"""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return (text,)
        if isinstance(data, list):
            return tuple(str(item).strip() for item in data if str(item).strip())
    return (text,)
