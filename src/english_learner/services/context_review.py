"""语境识别复习（M7.4）：把例句挖空，让用户在 4 个词里选回原词。

设计：
- **纯函数**，不碰数据库、不碰 PySide6、0 次 AI 调用；
- 原句优先用 `word.context_sentence`（当初阅读时的句子），没有再用
  `word.example_en`；用 `recall.mask_occurrences()` 挖空，保证与拼写题同一套词形规则；
- 干扰项优先取“同词性 + 同 CEFR 词档”的词，且必须不出现在原句中，
  避免泄题；不足 3 个时依次放宽到“同词档”“任意词”，仍不足则返回 None，
  由复习页回退看词回忆；
- `random.Random(seed)` 保证同一词每次生成的选项稳定可复现。

Java 对照：≈ 一个无状态的 `ContextQuestionFactory`，输入词条 + 干扰项池，
输出不可变 DTO；调度与评分仍由复习页 / SRS 负责。
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..models.entities import Word
from .recall import accepted_forms, mask_occurrences

DISTRACTOR_COUNT = 3


@dataclass(frozen=True)
class ContextQuestion:
    """一道语境识别题：挖空句 + 4 个选项 + 正确下标。"""

    masked_sentence: str
    options: tuple[str, ...]
    answer_index: int
    word_id: int
    answer: str = ""
    sentence: str = ""


def _appears_in(form: str, text: str) -> bool:
    """词形是否出现在文本中（单词边界、大小写不敏感）。"""
    value = str(form or "").strip()
    if not value:
        return False
    return re.search(rf"\b{re.escape(value)}\b", text, re.IGNORECASE) is not None


def _candidate_label(word: Word) -> str:
    return str(word.display_form or word.lemma or "").strip()


def build_context_question(
    word: Word,
    distractor_pool: Iterable[Word],
    *,
    seed: int = 0,
) -> ContextQuestion | None:
    """生成一道语境识别题；数据不足时返回 None（调用方回退看词回忆）。"""
    sentence = str(word.context_sentence or "").strip()
    if not sentence:
        sentence = str(word.example_en or "").strip()
    if not sentence:
        return None

    forms = accepted_forms(word)
    if not forms or any(" " in form for form in forms):
        # 短语没有可靠的“词形”边界，硬挖空容易出错，直接回退。
        return None
    masked, count = mask_occurrences(sentence, forms)
    if count <= 0:
        return None

    answer = _candidate_label(word)
    if not answer:
        return None

    lowered_sentence = sentence.lower()
    primary: list[Word] = []
    same_band: list[Word] = []
    others: list[Word] = []
    for item in distractor_pool:
        if word.id is not None and item.id == word.id:
            continue
        if str(item.lemma or "").strip().lower() == str(word.lemma or "").strip().lower():
            continue
        label = _candidate_label(item)
        if not label or label.lower() == answer.lower():
            continue
        # 干扰项的词形出现在原句里会直接泄题。
        if any(_appears_in(form, lowered_sentence) for form in accepted_forms(item)):
            continue
        if item.pos == word.pos and item.cefr_band == word.cefr_band:
            primary.append(item)
        elif item.cefr_band == word.cefr_band:
            same_band.append(item)
        else:
            others.append(item)

    for pool in (primary, same_band, others):
        if len(pool) >= DISTRACTOR_COUNT:
            rng = random.Random(seed)
            shuffled = list(pool)
            rng.shuffle(shuffled)
            picked: list[str] = []
            seen = {answer.lower()}
            for item in shuffled:
                label = _candidate_label(item)
                if label.lower() in seen:
                    continue
                seen.add(label.lower())
                picked.append(label)
                if len(picked) >= DISTRACTOR_COUNT:
                    break
            if len(picked) < DISTRACTOR_COUNT:
                continue
            options = [answer, *picked]
            rng.shuffle(options)
            return ContextQuestion(
                masked_sentence=masked,
                options=tuple(options),
                answer_index=options.index(answer),
                word_id=int(word.id or 0),
                answer=answer,
                sentence=sentence,
            )
    return None


__all__ = ["DISTRACTOR_COUNT", "ContextQuestion", "build_context_question"]
