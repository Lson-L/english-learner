"""复习卡片自动编排：按词的学习阶段决定「看词 / 拼写 / 看词+拼写」。

为什么单独一个文件：
- 编排规则是纯函数，不碰数据库、不碰 PySide6，方便单测；
- 复习页只负责按计划展示卡片，不再让用户选择模式；
- 规则集中在本地，可解释、可复现，不引入随机数。

判定优先级（从上到下，命中即停）：

| 条件 | 卡片形态 |
|---|---|
| 拼写题面不可用（短语、缺释义等） | 看词回忆 |
| `repetitions == 0`（从未成功复习过） | 看词回忆 |
| 上次评分 ≤ 2（忘记/模糊）或上次拼写判定是 near / wrong | 看词 + 拼写（双阶段） |
| `repetitions == 1` | 看词 + 拼写（双阶段） |
| 其它（`repetitions >= 2` 且上次表现正常） | 拼写回忆 |

编排完成后会按卡面（看词类 / 拼写类）做一次稳定的“穿插”：每次优先取
剩余更多的一类，同数量时按首次出现顺序取，保证同类卡面连续不超过
`MAX_CONSECUTIVE_SAME_FRONT` 张（除非另一类已经用完），同类型内部仍保持
原来的到期顺序。

Java 对照：
- `plan_card()` ≈ 无状态的策略方法；
- `build_session_plan()` ≈ 把策略应用到一批单词上的工厂方法；
- 真正的调度仍由 `srs.schedule()` 负责，这里只决定“卡片长什么样”。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..models.entities import Word
from .recall import build_spelling_prompt
from .srs import RATING_VAGUE, VERDICT_NEAR, VERDICT_WRONG

# 卡片形态；复习页按这三个值分支展示。
CARD_RECOGNITION = "recognition"
CARD_SPELLING = "spelling"
CARD_DUAL = "dual"

# 同一种卡面连续出现的上限；超过就尝试把后面最近的异类卡提前。
MAX_CONSECUTIVE_SAME_FRONT = 2

# 历史记录：word_id -> (最近一次评分, 最近一次拼写判定)。
ReviewHistory = Mapping[int, tuple[int, str]]


@dataclass(frozen=True)
class ReviewCard:
    """一张复习卡：词 + 本次使用的形态。

    双阶段卡（`CARD_DUAL`）只算一张卡：先看词回忆，翻面后默写一次，
    最终仍只评分一次、只调度一次。
    """

    word: Word
    mode: str


def _meaning_has_hint(text: str) -> bool:
    """释义挖空后是否还剩可用线索。

    释义本身正好是目标词时（例如 meaning_cn="alpha"），`build_spelling_prompt()`
    会把它整体挖成 `____`；这样的题面没有任何提示，自动编排下应回退看词。
    只去掉掩码与标点/空白，中文和英文单词都算有效线索。
    """
    return bool(re.sub(r"[_\W]+", "", str(text or "")))


def plan_card(word: Word, *, last_rating: int = 0, last_verdict: str = "") -> str:
    """返回该词本次应使用的卡片形态。

    `last_rating` / `last_verdict` 来自 `review_logs` 最近一条记录；
    没有历史时传 0 / 空串即可。
    """
    # 拼写不可用（短语、缺释义、题面生成失败）直接回退看词。
    prompt = build_spelling_prompt(word)
    if prompt is None or not _meaning_has_hint(prompt.meaning):
        return CARD_RECOGNITION
    # 首次学习（或上次评“忘记”后重置）先认义，不要求产出。
    if int(word.repetitions) <= 0:
        return CARD_RECOGNITION
    # 上次没记牢：先重新认一遍，再默写一次。
    if last_rating and int(last_rating) <= RATING_VAGUE:
        return CARD_DUAL
    if last_verdict in (VERDICT_NEAR, VERDICT_WRONG):
        return CARD_DUAL
    # 只成功复习过一次：看词 + 拼写过渡。
    if int(word.repetitions) == 1:
        return CARD_DUAL
    # 已经熟悉：直接考产出。
    return CARD_SPELLING


def build_session_plan(
    words: list[Word] | tuple[Word, ...],
    history: ReviewHistory | None = None,
) -> list[ReviewCard]:
    """把到期词列表编排成本轮卡片序列（已做穿插）。"""
    records = history or {}
    cards: list[ReviewCard] = []
    for word in words:
        rating, verdict = records.get(word.id or -1, (0, ""))
        mode = plan_card(word, last_rating=rating, last_verdict=verdict)
        cards.append(ReviewCard(word=word, mode=mode))
    return _interleave(cards)


def _front(mode: str) -> str:
    """卡面类型：双阶段从看词开始，和看词回忆算同一类。"""
    return CARD_SPELLING if mode == CARD_SPELLING else CARD_RECOGNITION


def _interleave(cards: list[ReviewCard]) -> list[ReviewCard]:
    """限制同类卡面连续张数；同类型内部保持原来的到期顺序。

    按“卡面类型”拆成两个队列，每次优先取剩余更多的一类，数量相同时按
    首次出现顺序取。这样 4 张拼写 + 4 张看词会排成
    `S R S R S R S R`，5 张拼写 + 2 张看词会排成 `S S R S S R S`，
    而不是先把一类用完、最后剩下一长串同类卡。
    """
    if not cards:
        return []

    queues: dict[str, list[ReviewCard]] = {}
    appearance: list[str] = []
    for card in cards:
        front = _front(card.mode)
        if front not in queues:
            queues[front] = []
            appearance.append(front)
        queues[front].append(card)

    result: list[ReviewCard] = []
    while any(queues.values()):
        blocked = ""
        if len(result) >= MAX_CONSECUTIVE_SAME_FRONT:
            last, before = _front(result[-1].mode), _front(result[-2].mode)
            if last == before:
                blocked = last
        choices = [front for front in appearance if queues[front] and front != blocked]
        if not choices:  # 另一类已经用完，只能继续排同类。
            choices = [front for front in appearance if queues[front]]
        # 优先消耗剩余更多的一类；数量相同按首次出现顺序，保持稳定。
        front = max(choices, key=lambda item: (len(queues[item]), -appearance.index(item)))
        result.append(queues[front].pop(0))
    return result
