"""业务层依赖的 AI 能力接口（鸭子类型，便于测试替换）。

Java 对照：≈ 只声明一个方法的 Feign 接口，业务层只依赖它，
单元测试里传一个假的实现即可，不需要真的连网络。
"""

from __future__ import annotations

from typing import Protocol


class TextCompleter(Protocol):
    """业务层只需要“给 messages，拿回文本”这一件事。"""

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str: ...
