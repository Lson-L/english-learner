"""拼写回忆判分：看中文释义，输入英文单词。

为什么单独一个文件：
- 判分是纯函数，不碰数据库、不碰 PySide6，和 `srs.py` 一样方便单测；
- 界面只负责展示，不把“大小写、词形、编辑距离”这些规则散落在 UI 代码里。

V1 规则（全部本地，不调用 AI）：
- 忽略首尾空白、大小写、连续空白和首尾标点，弯引号按直引号处理；
- 接受 lemma 与 display_form：文中是 went、原形是 go，输入两者都算对；
- 编辑距离为 1 视为“差一点”，只用于界面上的评分建议，不自动改 SM-2；
- 含空格的短语不适合逐字拼写，返回 None，由复习页回退到“看词回忆”。

Java 对照：
- `normalize_spelling()` / `edit_distance()` ≈ 静态工具方法；
- `check_spelling()` ≈ 无状态的判分服务，返回结果对象；
- 真正的调度仍由 `srs.schedule()` 负责，这里只给建议评分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models.entities import Word
from .srs import (
    RATING_FORGOT,
    RATING_KNOWN,
    RATING_VAGUE,
    VERDICT_CORRECT,
    VERDICT_NEAR,
    VERDICT_WRONG,
)

# 拼写判定的三种结论与 review_logs.verdict 共用同一套字符串，避免统计口径漂移。
STATUS_CORRECT = VERDICT_CORRECT
STATUS_NEAR = VERDICT_NEAR
STATUS_WRONG = VERDICT_WRONG

# 挖空占位符：长度固定，避免不同词形导致题面抖动。
MASK = "____"
# 1~2 个字母的词容错 1 个字母太宽松（a / i），只对 3 个字母以上启用“差一点”。
NEAR_MIN_LENGTH = 3

_WHITESPACE = re.compile(r"\s+")
_QUOTES = str.maketrans({"’": "'", "‘": "'"})
_SURROUNDING = "\"'“”‘’()[]{}<>.,;:!?…—–- "


@dataclass(frozen=True)
class SpellingPrompt:
    """拼写模式的题面：中文释义 + 挖空例句 + 词性。"""

    meaning: str
    masked_sentence: str = ""
    pos: str = ""


@dataclass(frozen=True)
class RecallResult:
    """一次拼写判分的结果；`suggested_rating` 只用于界面提示。"""

    status: str
    answer: str
    expected: str
    lemma: str
    distance: int
    suggested_rating: int


def normalize_spelling(text: str) -> str:
    """统一大小写、空白和首尾标点，供判分时比较。"""
    value = str(text or "").strip().translate(_QUOTES)
    value = value.strip(_SURROUNDING)
    return _WHITESPACE.sub(" ", value).lower()


def accepted_forms(word: Word) -> tuple[str, ...]:
    """词条可接受的拼写：原形 + 文中词形（去重、保序）。"""
    forms: list[str] = []
    seen: set[str] = set()
    for raw in (word.lemma, word.display_form):
        value = str(raw or "").strip()
        key = normalize_spelling(value)
        if value and key and key not in seen:
            seen.add(key)
            forms.append(value)
    return tuple(forms)


def mask_occurrences(text: str, forms: tuple[str, ...] | list[str]) -> tuple[str, int]:
    """把文本里出现的词形替换成 `____`，返回 (替换后文本, 替换次数)。

    用单词边界匹配，避免把 go 替换进 good / going；forms 按长度倒序，
    先匹配较长的词形。
    """
    source = str(text or "")
    patterns = sorted(
        {re.escape(form.strip()) for form in forms if form.strip()},
        key=len,
        reverse=True,
    )
    if not source.strip() or not patterns:
        return source, 0
    pattern = re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.IGNORECASE)
    return pattern.subn(MASK, source)


def build_spelling_prompt(word: Word) -> SpellingPrompt | None:
    """生成拼写题面；数据不足或词条是短语时返回 None（调用方回退）。"""
    forms = accepted_forms(word)
    if not forms or any(" " in form for form in forms):
        return None

    meaning = word.meaning_cn.strip()
    if meaning:
        meaning, _ = mask_occurrences(meaning, forms)
    if not meaning:
        meaning, _ = mask_occurrences(word.meaning_en, forms)
    if not meaning.strip():
        return None

    # 优先用 AI 例句；例句没出现目标词时退回文章原句。
    # 两者都无法挖空就只显示释义，宁可不给例句也不泄题。
    sentence = ""
    for raw in (word.example_en, word.context_sentence):
        masked, count = mask_occurrences(raw, forms)
        if count:
            sentence = masked
            break

    return SpellingPrompt(
        meaning=meaning.strip(),
        masked_sentence=sentence.strip(),
        pos=word.pos.strip(),
    )


def edit_distance(left: str, right: str) -> int:
    """Levenshtein 距离；单词长度很小，O(n*m) 的动态规划足够。"""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, ch_left in enumerate(left, 1):
        current = [i]
        for j, ch_right in enumerate(right, 1):
            cost = 0 if ch_left == ch_right else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def check_spelling(word: Word, answer: str) -> RecallResult:
    """判定拼写答案，并给出建议评分（不落库、不影响 SM-2）。"""
    forms = accepted_forms(word)
    expected = word.display_form.strip() or word.lemma.strip()
    lemma = word.lemma.strip()
    raw_answer = str(answer or "").strip()
    normalized = normalize_spelling(raw_answer)

    if not normalized or not forms:
        return RecallResult(
            status=STATUS_WRONG,
            answer=raw_answer,
            expected=expected,
            lemma=lemma,
            distance=len(normalized),
            suggested_rating=RATING_FORGOT,
        )

    normalized_forms = [normalize_spelling(form) for form in forms]
    distance = min(edit_distance(normalized, form) for form in normalized_forms)
    longest = max(len(normalized), *(len(form) for form in normalized_forms))

    if distance == 0:
        status, rating = STATUS_CORRECT, RATING_KNOWN
    elif distance == 1 and longest >= NEAR_MIN_LENGTH:
        status, rating = STATUS_NEAR, RATING_VAGUE
    else:
        status, rating = STATUS_WRONG, RATING_FORGOT

    return RecallResult(
        status=status,
        answer=raw_answer,
        expected=expected,
        lemma=lemma,
        distance=distance,
        suggested_rating=rating,
    )
