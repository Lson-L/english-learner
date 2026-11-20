"""回译练习的纯本地部分：抽句、批改结果解析与错误类型口径。

产品动机（AI_TASKS.md M5.4）：
- M3.17 之后整个产品里用户不再写英文句子，回译补上唯一的产出练习；
- 从当天文章里**本地**抽 3 句（优先含目标词、长度适中偏长），
  让 AI 只做两件事：把句子翻成中文（出题）、对照原句批改用户英文；
- 批改只给“最小修改 + 原因 + 0/2 分”，只作练习反馈，不影响等级与 SRS。

本模块不调用 AI、不碰数据库，方便单测；`back_translation_service` 负责编排。

Java 对照：≈ 一组静态工具方法 + 一个不可变 DTO（`SentenceGrade`），
类似 `BackTranslationRules`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .json_utils import JsonParseError, as_int, as_text, parse_json_object

# 每次练习抽几句；任务卡固定 3 句。
SENTENCES_PER_ROUND = 3

# 抽句的长度区间：太短没有练习价值，太长超出一次批改的舒适区。
MIN_SENTENCE_WORDS = 7
MAX_SENTENCE_WORDS = 40
IDEAL_MIN_WORDS = 12
IDEAL_MAX_WORDS = 28

# 单句得分只有 0 / 2 两档（任务卡口径）；解析时容忍模型给 1，但归一化为 0。
SCORE_PER_SENTENCE = 2

# 批改错误类型：固定词表，写进 verdict_json 与学习者记忆，禁止散落字符串。
WRITING_ERROR_TYPES: tuple[str, ...] = (
    "tense",
    "agreement",
    "article",
    "preposition",
    "collocation",
    "word_form",
    "word_order",
    "vocabulary",
    "spelling",
    "other",
)

WRITING_ERROR_LABELS: dict[str, str] = {
    "tense": "时态",
    "agreement": "主谓一致",
    "article": "冠词",
    "preposition": "介词",
    "collocation": "搭配",
    "word_form": "词形",
    "word_order": "语序",
    "vocabulary": "用词",
    "spelling": "拼写",
    "other": "其他",
}

_ERROR_TYPE_SET = set(WRITING_ERROR_TYPES)
_LABEL_TO_ERROR_TYPE = {label: key for key, label in WRITING_ERROR_LABELS.items()}

# 常见缩写：句点后面不是句末，不能在这里切句。
_ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "st",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "u.s",
    "u.k",
    "a.m",
    "p.m",
    "no",
    "fig",
    "approx",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_CJK_RE = re.compile(r"[一-鿿]")


class InvalidBackTranslationError(ValueError):
    """AI 返回的译文 / 批改结果不满足本地契约。"""


# ------------------------------------------------------------------ 抽句
def split_sentences(text: str) -> tuple[str, ...]:
    """把文章切成句子（本地规则，不追求完美，只要覆盖常见英文散文）。

    处理：句末 . ! ?（含连续标点与后引号）、常见缩写、段落换行；
    不处理分号，也不把换行当句子边界（文章正文段落用空行分隔）。
    """
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return ()

    sentences: list[str] = []
    start = 0
    index = 0
    length = len(normalized)
    while index < length:
        char = normalized[index]
        if char not in ".!?":
            index += 1
            continue

        end = index + 1
        # 连续标点（?!、...）和紧跟的后引号一起算进本句。
        while end < length and normalized[end] in ".!?\"')]":
            end += 1
        if end < length and normalized[end] != " ":
            # 3.5 / example.com 之类：标点不在句末。
            index = end
            continue
        if char == "." and _is_abbreviation(normalized, index):
            index = end
            continue

        sentence = normalized[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
        while start < length and normalized[start] == " ":
            start += 1
        index = start

    tail = normalized[start:].strip()
    if tail:
        sentences.append(tail)
    return tuple(sentences)


def _is_abbreviation(text: str, dot_index: int) -> bool:
    word_start = dot_index
    while word_start > 0 and (text[word_start - 1].isalpha() or text[word_start - 1] == "."):
        word_start -= 1
    word = text[word_start:dot_index].lower()
    if word in _ABBREVIATIONS:
        return True
    # 单字母缩写（人名首字母 / U.S. 的第一个点）。
    letters = word.replace(".", "")
    return len(letters) == 1 and letters.isalpha()


def pick_sentences(
    content: str,
    target_words: tuple[str, ...] | list[str] = (),
    *,
    limit: int = SENTENCES_PER_ROUND,
) -> tuple[str, ...]:
    """从正文里挑适合回译的句子，按原文顺序返回。

    评分：含目标词（每个 +3）> 长度落在理想区间（+2）；
    长度只是“适中偏长”，太短（<7 词）与太长（>40 词）直接排除。
    排序后取前 limit 句，再按原文顺序返回，保证题面阅读顺序自然。
    """
    targets = [word.strip().lower() for word in target_words if word and word.strip()]
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    for position, sentence in enumerate(split_sentences(content)):
        words = _WORD_RE.findall(sentence)
        if not (MIN_SENTENCE_WORDS <= len(words) <= MAX_SENTENCE_WORDS):
            continue
        fingerprint = " ".join(word.lower() for word in words)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        lowered = sentence.lower()
        hits = sum(
            1 for target in targets if re.search(rf"\b{re.escape(target)}\w{{0,4}}\b", lowered)
        )
        if IDEAL_MIN_WORDS <= len(words) <= IDEAL_MAX_WORDS:
            length_score = 2
        elif len(words) <= IDEAL_MAX_WORDS + 10:
            length_score = 1
        else:
            length_score = 0
        candidates.append((hits * 3 + length_score, position, sentence))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen = sorted(candidates[: max(0, int(limit))], key=lambda item: item[1])
    return tuple(sentence for _, _, sentence in chosen)


# ------------------------------------------------------------- 译文解析
def parse_translations(text: str, expected: int) -> tuple[str, ...]:
    """解析出题调用的中文译文：数量必须一致、非空、确实含中文。"""
    try:
        data = parse_json_object(text)
    except JsonParseError as exc:
        raise InvalidBackTranslationError(str(exc)) from exc

    items = data.get("translations")
    if not isinstance(items, list):
        raise InvalidBackTranslationError("缺少 translations 数组")
    values = [_translation_text(item) for item in items]
    if len(values) != expected:
        raise InvalidBackTranslationError(f"译文数量不符：要求 {expected}，返回 {len(values)}")
    if any(not value for value in values):
        raise InvalidBackTranslationError("存在空译文")
    if any(not _CJK_RE.search(value) for value in values):
        raise InvalidBackTranslationError("译文里没有中文")
    return tuple(values)


def _translation_text(item: Any) -> str:
    """兼容 `["中文"]` 与 `[{"translation": "中文"}]` 两种返回形态。"""
    if isinstance(item, dict):
        for key in ("translation", "translation_cn", "chinese", "text"):
            value = as_text(item.get(key))
            if value:
                return value
        return ""
    return as_text(item)


# ------------------------------------------------------------- 批改解析
@dataclass(frozen=True)
class SentenceGrade:
    """一句回译的批改结果；`verdict` 落库时就是它的 `to_dict()`。"""

    score: int = 0
    edited: str = ""
    reason: str = ""
    error_types: tuple[str, ...] = ()

    @property
    def correct(self) -> bool:
        return self.score >= SCORE_PER_SENTENCE

    @classmethod
    def from_dict(cls, data: Any) -> SentenceGrade | None:
        if not isinstance(data, dict):
            return None
        score = SCORE_PER_SENTENCE if as_int(data.get("score")) >= SCORE_PER_SENTENCE else 0
        return cls(
            score=score,
            edited=as_text(data.get("edited")),
            reason=as_text(data.get("reason")),
            error_types=normalize_error_types(data.get("error_types")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": int(self.score),
            "edited": self.edited,
            "reason": self.reason,
            "error_types": list(self.error_types),
        }


def normalize_error_types(value: Any) -> tuple[str, ...]:
    """把模型返回的错误类型收敛到固定词表：支持英文 key 与中文标签。"""
    if isinstance(value, str):
        raw_items: list[Any] = re.split(r"[,，、;；/]", value)
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        return ()

    result: list[str] = []
    for item in raw_items:
        text = as_text(item)
        if not text:
            continue
        key = text.lower()
        if key not in _ERROR_TYPE_SET:
            key = _LABEL_TO_ERROR_TYPE.get(text, "")
        if key and key not in result:
            result.append(key)
    return tuple(result)


def parse_grades(text: str, expected: int) -> tuple[SentenceGrade, ...]:
    """解析批改结果：必须逐句给出有效分数与原因；得分归一化为 0 / 2。"""
    try:
        data = parse_json_object(text)
    except JsonParseError as exc:
        raise InvalidBackTranslationError(str(exc)) from exc

    items = data.get("items")
    if not isinstance(items, list):
        raise InvalidBackTranslationError("缺少 items 数组")
    if len(items) != expected:
        raise InvalidBackTranslationError(f"批改条数不符：要求 {expected}，返回 {len(items)}")

    grades: list[SentenceGrade | None] = [None] * expected
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise InvalidBackTranslationError(f"第 {position + 1} 条批改不是对象")
        index = as_int(item.get("index"), position)
        if not (0 <= index < expected):
            raise InvalidBackTranslationError(f"批改下标越界：{index}")
        if grades[index] is not None:
            raise InvalidBackTranslationError(f"批改下标重复：{index}")
        reason = as_text(item.get("reason"))
        if not reason:
            raise InvalidBackTranslationError(f"第 {index + 1} 句缺少原因")
        # 分数必须显式给出且能转成非负整数；缺失 / null / "两分" 视为不合格，
        # 交给调用方带原因重试，避免静默把模型漏评分当成 0 分。
        raw_score = as_int(item.get("score"), -1)
        if raw_score < 0:
            raise InvalidBackTranslationError(f"第 {index + 1} 句缺少有效分数")
        grades[index] = SentenceGrade(
            score=SCORE_PER_SENTENCE if raw_score >= SCORE_PER_SENTENCE else 0,
            edited=as_text(item.get("edited")),
            reason=reason,
            error_types=normalize_error_types(item.get("error_types")),
        )

    if any(grade is None for grade in grades):
        raise InvalidBackTranslationError("批改没有覆盖全部句子")
    return tuple(grade for grade in grades if grade is not None)


__all__ = [
    "IDEAL_MAX_WORDS",
    "IDEAL_MIN_WORDS",
    "InvalidBackTranslationError",
    "MAX_SENTENCE_WORDS",
    "MIN_SENTENCE_WORDS",
    "SCORE_PER_SENTENCE",
    "SENTENCES_PER_ROUND",
    "SentenceGrade",
    "WRITING_ERROR_LABELS",
    "WRITING_ERROR_TYPES",
    "normalize_error_types",
    "parse_grades",
    "parse_translations",
    "pick_sentences",
    "split_sentences",
]
