"""阅读检测：AI 输出的解析与本地归一化。"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import replace

from ...models.entities import (
    CEFR_LEVELS,
    QuizProduction,
    QuizQuestion,
    ReadingQuiz,
    normalize_cefr_level,
)
from ..assessment_bank import UNKNOWN_ANSWER
from ..json_utils import JsonParseError, as_int, as_text, parse_json_object
from .constants import (
    _ALLOWED_ERROR_TYPES,
    MAX_CHOICE_NOTE_CHARS,
    MAX_CHOICE_QUESTIONS,
    MAX_QUIZ_OPTIONS,
    MIN_CHOICE_QUESTIONS,
    MIN_QUIZ_OPTIONS,
    STAGE_ONE_CHOICE_COUNT,
    STAGE_TWO_CHOICE_COUNT,
    STAGE_TWO_PRODUCTION_COUNT,
)
from .models import InvalidQuizError


def _normalize_entry(entry: dict[str, object], default_level: str) -> QuizQuestion | None:
    """把 AI 返回的单道题归一化（选项写法、答案字母、难度、技能）。"""
    normalized_entry = dict(entry)
    options = _parse_options(entry.get("options"))
    if options:
        normalized_entry["options"] = list(options)
        answer_index = _resolve_answer_index(entry, options)
        if answer_index >= 0:
            normalized_entry["answer_index"] = answer_index
    question = QuizQuestion.from_dict(normalized_entry)
    if question is None:
        return None
    return replace(
        question,
        difficulty=normalize_cefr_level(question.difficulty, default_level),
        skill=question.skill or "unknown",
    )


def parse_quiz_questions(
    text: str,
    level: str = "B1",
) -> tuple[QuizQuestion, ...]:
    """解析旧版“一整卷”题目 JSON（3~6 选择题 + 1 短答）。

    自适应流程使用 `parse_stage_one_questions` / `parse_stage_two_questions`；
    这个函数保留给历史调用与回归测试。
    """
    try:
        data = parse_json_object(text)
    except JsonParseError as exc:
        raise InvalidQuizError(str(exc)) from exc

    raw = data.get("questions")
    if not isinstance(raw, list):
        raise InvalidQuizError("返回里没有 questions 数组")

    default_level = normalize_cefr_level(level, "B1")
    choices: list[QuizQuestion] = []
    production: list[QuizQuestion] = []

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        question = _normalize_entry(entry, default_level)
        if question is None:
            continue
        if question.is_short:
            production.append(question)
            continue
        if MIN_QUIZ_OPTIONS <= len(question.options) <= MAX_QUIZ_OPTIONS:
            choices.append(question)

    if not (MIN_CHOICE_QUESTIONS <= len(choices) <= MAX_CHOICE_QUESTIONS):
        raise InvalidQuizError(
            f"选择题数量不在 {MIN_CHOICE_QUESTIONS}~{MAX_CHOICE_QUESTIONS} 之间"
            f"（实际 {len(choices)}）"
        )
    if not production:
        raise InvalidQuizError("缺少短答题（kind=short）")
    return tuple(choices + [production[0]])


def parse_stage_one_questions(text: str, level: str = "B1") -> tuple[QuizQuestion, ...]:
    """第一轮：恰好 3 道当前等级选择题；多余的题忽略。

    难度标签一律由本地回写为当前等级：这些标签后续会直接进入 Rasch 后验，
    不能完全相信模型自报的 difficulty（模型经常复制成上一轮的等级）。
    """
    try:
        data = parse_json_object(text)
    except JsonParseError as exc:
        raise InvalidQuizError(str(exc)) from exc

    raw = data.get("questions")
    if not isinstance(raw, list):
        raise InvalidQuizError("返回里没有 questions 数组")

    default_level = normalize_cefr_level(level, "B1")
    choices: list[QuizQuestion] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        question = _normalize_entry(entry, default_level)
        if question is None or question.is_short:
            continue
        if MIN_QUIZ_OPTIONS <= len(question.options) <= MAX_QUIZ_OPTIONS:
            choices.append(replace(question, difficulty=default_level))
        if len(choices) >= STAGE_ONE_CHOICE_COUNT:
            break

    if len(choices) < STAGE_ONE_CHOICE_COUNT:
        raise InvalidQuizError(
            f"第一轮需要 {STAGE_ONE_CHOICE_COUNT} 道选择题（实际 {len(choices)}）"
        )
    return tuple(choices[:STAGE_ONE_CHOICE_COUNT])


def parse_stage_two_questions(
    text: str,
    level: str = "B1",
    target_level: str = "B1",
) -> tuple[QuizQuestion, ...]:
    """第二轮：2 道边界选择题 + 2 道英译中题。

    选择题一律回写为边界等级 `target`；英译中题保留模型给出的难度，但收敛到
    “当前等级 ~ 边界等级”区间内；模型漏标时用 target 兜底。
    这样既不让自报标签乱跳，也避免模型把上一轮的等级复制过来污染证据。

    产出题的 skill 一律强制为 `translation`。新题面只写英文原句（方向由界面
    中文说明与中文参考译文体现）；模型偶尔会退回“英文短答/改写”，这类题会
    让用户重新开始练写作，与当前产品目标（只练阅读理解）冲突，直接判为
    不合格并触发重试。旧格式题面里的英文出题指令会在 `QuizQuestion.from_dict`
    里剥掉。
    """
    try:
        data = parse_json_object(text)
    except JsonParseError as exc:
        raise InvalidQuizError(str(exc)) from exc

    raw = data.get("questions")
    if not isinstance(raw, list):
        raise InvalidQuizError("返回里没有 questions 数组")

    default_level = normalize_cefr_level(level, "B1")
    target = normalize_cefr_level(target_level, default_level)
    current_index = CEFR_LEVELS.index(default_level)
    target_index = CEFR_LEVELS.index(target)
    production_low, production_high = sorted((current_index, target_index))

    choices: list[QuizQuestion] = []
    productions: list[QuizQuestion] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        explicit = normalize_cefr_level(entry.get("difficulty"))
        # 第二轮题目缺少 difficulty 时回退到边界等级 target，而不是本卷等级；
        # 否则边界探测题会被错误地当成当前难度计入证据。
        question = _normalize_entry(entry, target)
        if question is None:
            continue
        if question.is_short:
            if explicit:
                index = CEFR_LEVELS.index(explicit)
                index = min(max(index, production_low), production_high)
                difficulty = CEFR_LEVELS[index]
            else:
                difficulty = target
            productions.append(replace(question, difficulty=difficulty, skill="translation"))
        elif MIN_QUIZ_OPTIONS <= len(question.options) <= MAX_QUIZ_OPTIONS:
            choices.append(replace(question, difficulty=target))

    if len(choices) < STAGE_TWO_CHOICE_COUNT:
        raise InvalidQuizError(
            f"第二轮需要 {STAGE_TWO_CHOICE_COUNT} 道选择题（实际 {len(choices)}）"
        )

    # 只接受题面明确要求“译成中文”的产出题；英文短答/改写会让用户重新练写作。
    translations = [question for question in productions if question.is_translation]
    if len(translations) < STAGE_TWO_PRODUCTION_COUNT:
        raise InvalidQuizError(
            f"第二轮需要 {STAGE_TWO_PRODUCTION_COUNT} 道英译中题"
            f"（题面必须要求翻译成中文，实际 {len(translations)}）"
        )
    return tuple(choices[:STAGE_TWO_CHOICE_COUNT] + translations[:STAGE_TWO_PRODUCTION_COUNT])


def parse_question_review(
    text: str,
    count: int,
    expected_answers: Sequence[int | None] | None = None,
) -> tuple[int, ...]:
    """解析题目审核结果，返回需要丢弃的下标（0 开始，越界忽略）。

    `expected_answers` 是出题人标注的答案键（产出题填 None）。审核模型会
    独立作答并返回 `answers`；如果它的答案与标注不一致，这道题也判为不合格。
    这能发现“题目本身没错、但答案键标错”的情况，而只问“答案是否唯一”
    很难发现这种错误。
    """
    try:
        data = parse_json_object(text)
    except JsonParseError as exc:
        raise InvalidQuizError(str(exc)) from exc

    raw = data.get("invalid_indices")
    if raw is None:
        raw = data.get("invalid")
    indices: list[int] = []
    if isinstance(raw, list):
        for item in raw:
            try:
                index = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= index < count and index not in indices:
                indices.append(index)

    raw_answers = data.get("answers")
    if expected_answers is not None and isinstance(raw_answers, list):
        for index, expected in enumerate(expected_answers[:count]):
            if expected is None or index >= len(raw_answers):
                continue
            answer = _parse_answer_index(raw_answers[index])
            if answer is None or answer == expected:
                continue
            if index not in indices:
                indices.append(index)
    return tuple(indices)


def shuffle_question_options(
    question: QuizQuestion,
    rng: random.Random | None = None,
) -> QuizQuestion:
    """打乱选择题选项顺序，并把 `answer_index` 映射到新位置。

    目的：避免正确答案长期落在 B/C 等固定位置，降低“蒙答案位置”的收益。
    产出题没有选项，原样返回。
    """
    if question.is_short or len(question.options) < 2:
        return question
    order = list(range(len(question.options)))
    (rng or random).shuffle(order)
    options = tuple(question.options[index] for index in order)
    answer_index = question.answer_index
    if 0 <= answer_index < len(order):
        answer_index = order.index(answer_index)
    return replace(question, options=options, answer_index=answer_index)


def _parse_options(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items: list[object] = re.split(r"[\n|]", value)
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return ()
    return tuple(text for text in (as_text(item) for item in items) if text)


def _resolve_answer_index(entry: dict[str, object], options: tuple[str, ...]) -> int:
    """从 answer_index / correct_index / answer 中尽力解析正确选项下标。"""
    for key in ("answer_index", "correct_index", "answer"):
        if key not in entry:
            continue
        value = entry[key]
        index = _parse_answer_index(value)
        if index is not None and 0 <= index < len(options):
            return index
        if isinstance(value, str):
            text = value.strip()
            for position, option in enumerate(options):
                if text in (option, f"{chr(ord('A') + position)}. {option}"):
                    return position
    return -1


def _parse_answer_index(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    upper = text.upper()
    if len(upper) == 1 and "A" <= upper <= "Z":
        return ord(upper) - ord("A")
    match = re.match(r"^([A-Z])(?:[.)、:：\s]|$)", upper)
    if match:
        return ord(match.group(1)) - ord("A")
    try:
        return int(text)
    except ValueError:
        return None


def _normalize_answers(answers: tuple[int, ...], quiz: ReadingQuiz) -> tuple[int, ...]:
    """把作答整理成与选择题等长的下标元组。

    - `UNKNOWN_ANSWER`（-2）是有效作答：用户主动表示“不知道”，按答错计分；
    - 其余越界 / 缺答统一记为 -1（未作答），提交时会被拦下。
    """
    normalized: list[int] = []
    for index, question in enumerate(quiz.choice_questions):
        try:
            chosen = int(answers[index]) if index < len(answers) else -1
        except (TypeError, ValueError):
            chosen = -1
        if chosen == UNKNOWN_ANSWER:
            normalized.append(chosen)
            continue
        if not (0 <= chosen < len(question.options)):
            chosen = -1
        normalized.append(chosen)
    return tuple(normalized)


def _normalize_choice_notes(
    notes: Sequence[str],
    quiz: ReadingQuiz,
    answers: tuple[int, ...],
) -> tuple[str, ...]:
    """把补充说明整理成与选择题等长的元组；非「不知道」的题一律清空。"""
    normalized: list[str] = []
    for position, _ in enumerate(quiz.choice_questions):
        chosen = answers[position] if position < len(answers) else -1
        text = as_text(notes[position])[:MAX_CHOICE_NOTE_CHARS] if position < len(notes) else ""
        normalized.append(text if chosen == UNKNOWN_ANSWER else "")
    return tuple(normalized)


def _normalize_production_texts(
    quiz: ReadingQuiz,
    texts: tuple[str, ...] | list[str] | None,
    fallback: str,
) -> list[str]:
    total = quiz.short_total
    if total <= 0:
        return []
    raw = list(texts) if texts is not None else [fallback]
    normalized = [str(item or "").strip()[:2000] for item in raw[:total]]
    normalized += [""] * (total - len(normalized))
    return normalized


def _parse_productions(
    data: dict[str, object],
    quiz: ReadingQuiz,
    texts: list[str],
) -> tuple[QuizProduction, ...]:
    """解析每道产出题的 0/1 分与反馈。

    空作答强制 0 分（这是真实结果）；模型漏给分数时标记 `scored=False`，
    调用方必须把这类题排除在等级证据之外，不能静默当成答错。
    """
    total = quiz.short_total
    if total <= 0:
        return ()

    raw_scores = data.get("production_scores")
    raw_values: list[object] = []
    if isinstance(raw_scores, list):
        raw_values = raw_scores[:total]
    elif total == 1 and "short_answer_score" in data:
        raw_values = [data.get("short_answer_score")]

    scores: list[int] = []
    scored_flags: list[bool] = []
    for item in raw_values:
        if _is_valid_production_score(item):
            scores.append(1 if as_int(item, 0) >= 1 else 0)
            scored_flags.append(True)
        else:
            scores.append(0)
            scored_flags.append(False)
    scores += [0] * (total - len(scores))
    scored_flags += [False] * (total - len(scored_flags))

    raw_feedbacks = data.get("production_feedbacks")
    feedbacks: list[str] = []
    if isinstance(raw_feedbacks, list):
        feedbacks = [as_text(item)[:500] for item in raw_feedbacks[:total]]
    feedbacks += [""] * (total - len(feedbacks))
    if total == 1 and not feedbacks[0]:
        feedbacks[0] = as_text(data.get("short_answer_feedback"))[:500]

    productions: list[QuizProduction] = []
    for index, text in enumerate(texts[:total]):
        score = scores[index]
        scored = scored_flags[index]
        if not text.strip():
            # 未作答是确定的 0 分，不是“模型没评分”。
            score = 0
            scored = True
        productions.append(
            QuizProduction(
                text=text,
                score=score,
                feedback=feedbacks[index],
                scored=scored,
            )
        )
    return tuple(productions)


def _is_valid_production_score(value: object) -> bool:
    """产出题分数必须是 0 或 1；缺字段、"abc"、0.5、2 都算无效。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value in (0, 1)
    text = str(value or "").strip()
    if not text:
        return False
    try:
        number = float(text)
    except ValueError:
        return False
    return number in (0.0, 1.0)


def _missing_production_scores(
    data: dict[str, object],
    quiz: ReadingQuiz,
    texts: list[str],
) -> tuple[int, ...]:
    """找出“有作答但 AI 没给有效分数”的产出题下标。"""
    total = quiz.short_total
    if total <= 0:
        return ()
    raw = data.get("production_scores")
    if isinstance(raw, list):
        values: list[object] = raw[:total]
    elif total == 1 and "short_answer_score" in data:
        values = [data.get("short_answer_score")]
    else:
        values = []

    missing: list[int] = []
    for index, text in enumerate(texts[:total]):
        if not text.strip():
            continue
        if index >= len(values) or not _is_valid_production_score(values[index]):
            missing.append(index)
    return tuple(missing)


def _parse_explanations(data: dict[str, object], quiz: ReadingQuiz) -> tuple[str, ...]:
    """把 AI 讲评整理成与题目等长的中文解析，缺失的题用空串补齐。"""
    raw = data.get("explanations")
    total = len(quiz.questions)
    if isinstance(raw, list) and raw and all(isinstance(item, dict) for item in raw):
        mapping: dict[int, str] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                position = int(item.get("index", 0))
            except (TypeError, ValueError):
                continue
            text = as_text(item.get("explanation") or item.get("text"))
            if 1 <= position <= total and text:
                mapping[position - 1] = text[:800]
        items = [mapping.get(index, "") for index in range(total)]
    elif isinstance(raw, list):
        items = [as_text(item)[:800] for item in raw]
    elif isinstance(raw, str):
        items = [raw.strip()[:800]] if raw.strip() else []
    else:
        items = []

    items = items[:total]
    items += [""] * (total - len(items))

    for index, question in enumerate(quiz.questions):
        if not question.is_short or items[index]:
            continue
        feedbacks = data.get("production_feedbacks")
        if isinstance(feedbacks, list):
            position = sum(1 for item in quiz.questions[:index] if item.is_short)
            if position < len(feedbacks):
                items[index] = as_text(feedbacks[position])[:800]
        if not items[index]:
            items[index] = as_text(data.get("short_answer_feedback"))[:800]
    return tuple(items)


def _parse_error_types(
    data: dict[str, object],
    quiz: ReadingQuiz,
    answers: tuple[int, ...],
) -> tuple[str, ...]:
    """把 AI 的错因整理成与题目等长的元组；缺失时按对错给默认值。"""
    raw = data.get("error_types")
    total = len(quiz.questions)
    if isinstance(raw, list):
        items = [as_text(item).lower()[:40] for item in raw]
    elif isinstance(raw, str):
        items = [raw.strip().lower()[:40]] if raw.strip() else []
    else:
        items = []
    items = items[:total]
    items += [""] * (total - len(items))

    result: list[str] = []
    choice_position = 0
    for index, question in enumerate(quiz.questions):
        if question.is_short:
            result.append("translation" if question.is_translation else "short_answer")
            continue
        chosen = answers[choice_position] if choice_position < len(answers) else -1
        choice_position += 1
        correct = chosen == question.answer_index
        value = items[index]
        if correct:
            value = "none"
        elif value in {"", "none"}:
            value = "unknown"
        elif value not in _ALLOWED_ERROR_TYPES:
            value = "unknown"
        result.append(value)
    return tuple(result)
