"""阅读检测：自适应出题、本地后验、AI 讲评与动态调整等级。

产品动机（用户提出）：
- 读完今日文章后希望有一个“阅读完成”的仪式感，而不是打开即算读完；
- 由 AI 根据本文出几道题，检查是否真的读懂；
- 答题结果要反过来影响学习画像，让等级随表现动态变化。

M3.9（第一阶段）：
- 混合难度出题、英文短答、错因归因；
- 本地证据 ratio + 连续两次向上证据才升级。

Phase 2（本地证据模型）：
- 每次检测的题目级观测写入 `level_evidence`；
- 用 Rasch/逻辑斯蒂模型计算四档后验与置信度，历史证据按时间衰减回放；
- AI 只提供讲评与盲评建议，最终等级由本地后验决定。

Phase 3（自适应与更强诊断）：
- 两阶段出题：先 3 道当前等级题，再按正确率出 2 道边界题；
- 出题后增加“题目审核”pass，不合格题丢弃/重出；
- 2 道英译中题（从原文选句翻译成中文），按「理解准确 / 中文通顺」评分；
  不再要求学习者写英文，检测目标聚焦阅读理解；
- 等级盲评（不看当前等级）与讲评分两次调用；
- 同一天只允许一次正式检测，第二次只练手、不调级。

设计要点：
- 题目落库缓存：同一篇文章的每一阶段只出一次题；
- 选择题由本地判分，AI 只负责讲评、产出题评分、题目审核与盲评；
- AI 失败时不改画像、不写结果，用户可重试，已生成的题目保留；
- 本模块是同步阻塞的，只能在 `gui.workers.TaskWorker` 里调用。
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from ..models.entities import (
    CEFR_LEVELS,
    Article,
    AssessmentResult,
    LevelEvidence,
    Profile,
    QuizProduction,
    QuizQuestion,
    ReadingQuiz,
    RealText,
    normalize_cefr_level,
    now_iso,
)
from ..storage.db import DuplicateError
from ..storage.repositories import (
    LevelEvidenceRepo,
    ProfileRepo,
    QuizRepo,
    RealTextRepo,
    ReviewRepo,
)
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .assessment_bank import UNKNOWN_ANSWER, UNKNOWN_LABEL
from .assessment_service import assessment_result_from_data
from .json_utils import JsonParseError, as_int, as_text, parse_json_object
from .learner_memory import LearnerMemory, load_memory
from .level_estimator import (
    LevelPosterior,
    estimate_level,
    observations_from_blind_level,
    observations_from_quiz,
    observations_from_self_report,
    posterior_to_tuple,
    replay_evidence,
    review_band_observations,
)
from .prompts import (
    PROMPT_VERSION,
    QUIZ_PRODUCTION_COUNT,
    QUIZ_STAGE_ONE_COUNT,
    QUIZ_STAGE_TWO_CHOICE_COUNT,
    QuizHistoryEntry,
    build_blind_level_messages,
    build_question_review_messages,
    build_quiz_grading_messages,
    build_quiz_stage_one_messages,
    build_quiz_stage_two_messages,
)
from .quiz_calibration import difficulty_stats, skill_stats
from .real_text_feedback import record_completion as record_real_text_completion

logger = logging.getLogger(__name__)

# 旧版整卷解析的宽松规则（保留给 `parse_quiz_questions` 与外部调用）。
MIN_CHOICE_QUESTIONS = 3
MAX_CHOICE_QUESTIONS = 6
MIN_QUIZ_OPTIONS = 3
MAX_QUIZ_OPTIONS = 6

# 自适应两阶段：第一轮 3 道当前等级题；答对 >= 2 道往上一档探测，否则往下一档确认。
STAGE_ONE_CHOICE_COUNT = QUIZ_STAGE_ONE_COUNT
STAGE_TWO_CHOICE_COUNT = QUIZ_STAGE_TWO_CHOICE_COUNT
STAGE_TWO_PRODUCTION_COUNT = QUIZ_PRODUCTION_COUNT

# 升级需要连续两次同向向上证据；降级立即生效。
UPGRADE_EVIDENCE_REQUIRED = 2

# 本地证据的判定阈值：混合难度加权后的平均分落在区间外才改变方向（仍用于展示）。
EVIDENCE_UP_THRESHOLD = 0.45
EVIDENCE_DOWN_THRESHOLD = -0.35
EVIDENCE_STRONG_THRESHOLD = 0.6

# 盲评建议只作为一条弱观测进入后验。
BLIND_OBSERVATION_WEIGHT = 0.5

# 各任务的输出额度：宁可给宽一点，避免 JSON 被截断。
# 对带 thinking 的模型，推理也会消耗额度，所以这里比“正文长度”宽不少。
STAGE_ONE_MAX_TOKENS = 4000
STAGE_TWO_MAX_TOKENS = 5000
QUESTION_REVIEW_MAX_TOKENS = 1500
GRADING_MAX_TOKENS = 6000
BLIND_MAX_TOKENS = 1000

# 选择题补充说明的截断长度：只给 AI 当错因线索，不需要长篇大论。
MAX_CHOICE_NOTE_CHARS = 200

# 薄弱技能进入出题 prompt 的最低样本数，避免一次失误就被当成长期弱点。
WEAK_SKILL_MIN_SAMPLES = 2
WEAK_SKILL_MAX_ACCURACY = 0.6
WEAK_SKILL_LIMIT = 3

# 产出题专用技能：选择题无法覆盖，不喂给选择题的“优先覆盖薄弱技能”规则。
_PRODUCTION_SKILLS = {
    "writing",
    "rewrite",
    "sentence_rewrite",
    "translation",
    "translate",
    "short_answer",
}

# 学习者记忆里的错因是“错因键”，能映射成选择题技能的才并入薄弱技能。
# careless / none / unknown 不是技能，忽略。
_MEMORY_ERROR_SKILLS = {
    "main_idea",
    "detail",
    "inference",
    "vocabulary",
    "grammar",
}

LEVEL_ORDER = CEFR_LEVELS

_ALLOWED_ERROR_TYPES = {
    "none",
    "vocabulary",
    "inference",
    "detail",
    "main_idea",
    "grammar",
    "careless",
    "unknown",
}

_SELF_REPORT_ADJUST = {
    "easy": 0.25,
    "轻松": 0.25,
    "ok": 0.0,
    "刚好": 0.0,
    "hard": -0.25,
    "吃力": -0.25,
}


class InvalidQuizError(ValueError):
    """AI 返回的题目不满足本地校验规则。"""


@dataclass(frozen=True)
class EvidenceSummary:
    """一次检测的本地证据摘要。

    `ratio` 是混合难度加权后的平均表现：答对高难度题加分更多，
    答错低难度题扣分更多；界面用它展示“偏强 / 中性 / 偏弱”。
    等级决策已改为后验模型，这个值仍保留用于解释与向后兼容。
    """

    ratio: float
    direction: str  # up / keep / down
    points: float
    max_points: float
    details: tuple[float, ...] = ()

    @property
    def label(self) -> str:
        if self.direction == "up":
            return "偏强"
        if self.direction == "down":
            return "偏弱"
        return "中性"


@dataclass(frozen=True)
class LevelDecision:
    """连续证据策略的输出：实际生效等级 + 待定状态。"""

    applied_level: str
    pending_level: str
    pending_count: int
    changed: bool
    waiting: bool


@dataclass(frozen=True)
class QuizOutcome:
    """一次“答题 + 讲评 + 调级”的完整结果，供界面展示。"""

    quiz: ReadingQuiz
    profile: Profile
    result: AssessmentResult
    previous_level: str
    recommended_level: str = ""
    pending_level: str = ""
    pending_count: int = 0
    evidence: EvidenceSummary | None = None
    posterior: LevelPosterior | None = None
    confidence: float = 0.0
    practice_mode: bool = False
    blind_level: str = ""

    @property
    def level_changed(self) -> bool:
        return self.profile.cefr_level != self.previous_level

    @property
    def waiting_for_upgrade(self) -> bool:
        return bool(self.pending_level and self.pending_count > 0)

    @property
    def score(self) -> int:
        return self.quiz.score

    @property
    def total(self) -> int:
        return self.quiz.total

    @property
    def percent(self) -> int:
        return self.quiz.percent


# ------------------------------------------------------------------ 解析
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


# ------------------------------------------------------------------ 证据
def score_quiz_evidence(
    level: str,
    quiz: ReadingQuiz,
    answers: tuple[int, ...],
    *,
    short_answer_score: int = 0,
    production_scores: tuple[int, ...] | None = None,
    production_scored: Sequence[bool] | None = None,
    self_report: str = "",
) -> EvidenceSummary:
    """把“答对/答错 + 题目难度 + 产出题 + 自评”折算成本地证据。

    单题权重：
    - 同档答对 +1，答错 -1；
    - 每高一档，答对额外 +0.5，答错少扣 0.5；
    - 每低一档，答对少加 0.5，答错多扣 0.5；
    - 自评“轻松/吃力”只做 ±0.25 的弱修正。
    """
    current_index = _level_index(normalize_cefr_level(level))
    if current_index is None:
        current_index = 1
    scores = list(production_scores) if production_scores is not None else [short_answer_score]
    scored_flags = (
        list(production_scored) if production_scored is not None else [True] * len(scores)
    )
    points = 0.0
    details: list[float] = []

    for position, question in enumerate(quiz.choice_questions):
        chosen = answers[position] if position < len(answers) else -1
        delta = _difficulty_delta(question.difficulty, current_index)
        correct = chosen == question.answer_index
        item_points = (1.0 + 0.5 * delta) if correct else (-1.0 + 0.5 * delta)
        details.append(item_points)
        points += item_points

    items = len(quiz.choice_questions)
    for position, question in enumerate(quiz.production_questions):
        if position < len(scored_flags) and not scored_flags[position]:
            # AI 漏评的产出题不计入证据，避免把格式问题算成答错。
            continue
        score = scores[position] if position < len(scores) else 0
        delta = _difficulty_delta(question.difficulty, current_index)
        correct = int(score) >= 1
        item_points = (1.0 + 0.5 * delta) if correct else (-1.0 + 0.5 * delta)
        details.append(item_points)
        points += item_points
        items += 1

    points += _SELF_REPORT_ADJUST.get((self_report or "").strip().lower(), 0.0)
    max_points = float(max(1, items))
    ratio = max(-1.0, min(1.0, points / max_points))
    if ratio >= EVIDENCE_UP_THRESHOLD:
        direction = "up"
    elif ratio <= EVIDENCE_DOWN_THRESHOLD:
        direction = "down"
    else:
        direction = "keep"
    return EvidenceSummary(
        ratio=ratio,
        direction=direction,
        points=points,
        max_points=max_points,
        details=tuple(details),
    )


def decide_recommended_level(
    previous: str,
    ai_suggested: str,
    evidence: EvidenceSummary,
) -> str:
    """旧版“AI 建议 + 证据”组合规则（保留给回放/测试）。

    Phase 2 起实际决策由 `level_estimator.estimate_level()` 完成；
    这个函数仍可用于解释旧数据，不参与新检测。
    """
    previous = previous.strip().upper()
    previous_index = _level_index(previous)
    if previous_index is None:
        return previous

    ai_index = _level_index(normalize_cefr_level(ai_suggested))
    if ai_index is None:
        ai_index = previous_index
    ai_direction = _sign(ai_index - previous_index)
    evidence_direction = {"up": 1, "down": -1, "keep": 0}.get(evidence.direction, 0)

    if ai_direction == 0:
        direction = evidence_direction
    elif evidence_direction == 0:
        direction = ai_direction
    elif ai_direction == evidence_direction:
        direction = ai_direction
    else:
        direction = evidence_direction if abs(evidence.ratio) >= EVIDENCE_STRONG_THRESHOLD else 0

    target = max(0, min(len(LEVEL_ORDER) - 1, previous_index + direction))
    return LEVEL_ORDER[target]


def apply_level_policy(
    current: str,
    recommended: str,
    pending_level: str = "",
    pending_count: int = 0,
) -> LevelDecision:
    """连续证据策略：向上要连续两次，向下立即生效。

    例：当前 B1，第一次建议 B2 → 保持 B1，记录 pending=B2 / count=1；
    第二次仍建议 B2 → 真正升到 B2。若中间一次建议保持 B1，则清空待定。
    """
    current = current.strip().upper()
    recommended = recommended.strip().upper()
    current_index = _level_index(current)
    recommended_index = _level_index(recommended)
    if current_index is None or recommended_index is None:
        return LevelDecision(current, "", 0, False, False)

    if recommended_index == current_index:
        return LevelDecision(current, "", 0, False, False)

    if recommended_index < current_index:
        return LevelDecision(recommended, "", 0, True, False)

    count = pending_count + 1 if pending_level.strip().upper() == recommended else 1
    if count >= UPGRADE_EVIDENCE_REQUIRED:
        return LevelDecision(recommended, "", 0, True, False)
    return LevelDecision(current, recommended, count, False, True)


def choose_boundary_level(level: str, correct: int, total: int) -> str:
    """第一轮正确率决定第二轮往哪边探测：>= 2/3 升一档，否则降一档。"""
    current = normalize_cefr_level(level, "B1")
    index = CEFR_LEVELS.index(current)
    if total <= 0:
        return current
    if correct * 3 >= total * 2:
        target = min(len(CEFR_LEVELS) - 1, index + 1)
    else:
        target = max(0, index - 1)
    return CEFR_LEVELS[target]


def _difficulty_delta(difficulty: str, current_index: int) -> int:
    index = _level_index(normalize_cefr_level(difficulty))
    if index is None:
        return 0
    return max(-2, min(2, index - current_index))


def _level_index(level: str) -> int | None:
    try:
        return LEVEL_ORDER.index(level)
    except ValueError:
        return None


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


# ------------------------------------------------------------------ 服务
class ReadingQuizService:
    """出题 + 判分 + 讲评 + 调级。一个实例只在一次后台任务里使用。"""

    def __init__(
        self,
        client: TextCompleter,
        quiz_repo: QuizRepo,
        profile_repo: ProfileRepo,
        evidence_repo: LevelEvidenceRepo | None = None,
        review_repo: ReviewRepo | None = None,
        *,
        shuffle_options: bool = True,
        rng: random.Random | None = None,
        memory_provider: Callable[[], LearnerMemory] | None = None,
        real_text_repo: RealTextRepo | None = None,
    ) -> None:
        self._client = client
        self._quizzes = quiz_repo
        self._profiles = profile_repo
        self._evidence = evidence_repo
        self._reviews = review_repo
        self._shuffle_options = shuffle_options
        self._rng = rng
        self._memory_provider = memory_provider
        # M7.1 / M7.2：真实文章练习完成时回写供给历史（失败不阻断结果落库）。
        self._real_texts = real_text_repo

    # ------------------------------------------------------------ 出题 / 缓存
    def get_cached(self, article_id: int) -> ReadingQuiz | None:
        """读取已缓存的题目（不论是否已作答），不调用 AI。"""
        return self._quizzes.get_by_article(article_id)

    def is_practice_day(self, article: Article, quiz_id: int | None = None) -> bool:
        """今天是否已经完成过一次正式检测（本次只能练手）。

        真实文章（M7.1）恒为练习模式：难度是预估的，不能写等级证据。
        正式检测仍以证据表为准；没有注入证据仓库时退回按完成日期统计。
        """
        if (article.source or "") == "real":
            return True
        if self._evidence is not None:
            return self._evidence.count_on(article.article_date, exclude_quiz_id=quiz_id) > 0
        return self._quizzes.count_completed_on(article.article_date, exclude_id=quiz_id) > 0

    def seed_bundled_quiz(self, article: Article, text: RealText) -> ReadingQuiz:
        """把真实文章自带的 4 道预置题落库（M7.1，0 次 AI）。

        - 同一篇文章已有题目（含用户先手动出题的情况）直接返回，避免重复插入；
        - `stage=2`：界面不会触发第二轮出题，也不会等 AI 讲评；
        - `practice_mode=True`：提交时走本地判分、不调等级。
        """
        if article.id is None:
            raise AIError("文章还没有保存", "请先让今天的文章生成完成，再做阅读检测。")
        cached = self._quizzes.get_by_article(article.id)
        if cached is not None:
            return cached
        questions = tuple(text.questions)
        if not questions:
            raise AIError("这篇真实文章没有预置题目", "可以换一篇，或让 AI 生成一篇文章。")
        quiz = ReadingQuiz(
            article_id=article.id,
            article_date=article.article_date,
            level=normalize_cefr_level(article.level, text.level),
            questions=questions,
            explanations=tuple(question.explanation for question in questions),
            practice_mode=True,
            stage=2,
            created_at=now_iso(),
        )
        try:
            stored = self._quizzes.insert(quiz)
        except DuplicateError:
            existing = self._quizzes.get_by_article(article.id)
            if existing is None:
                raise
            stored = existing
        logger.info(
            "真实文章预置题目已入库：article=%s 题目数=%s", article.id, len(stored.questions)
        )
        return stored

    def get_or_create(self, article: Article, level: str) -> ReadingQuiz:
        """当天文章已有题目就直接返回；否则生成第一轮题目并落库。"""
        if article.id is None:
            raise AIError("文章还没有保存", "请先让今日文章生成完成，再做阅读检测。")

        cached = self._quizzes.get_by_article(article.id)
        if cached is not None:
            logger.info("命中阅读检测缓存：article=%s stage=%s", article.id, cached.stage)
            return cached

        questions = self.generate_stage_one(article, level)
        quiz = ReadingQuiz(
            article_id=article.id,
            article_date=article.article_date,
            level=normalize_cefr_level(level, article.level),
            questions=questions,
            stage=1,
            created_at=now_iso(),
        )
        try:
            stored = self._quizzes.insert(quiz)
        except DuplicateError:
            existing = self._quizzes.get_by_article(article.id)
            if existing is None:
                raise
            stored = existing
        logger.info("阅读检测第一轮已入库：article=%s 题目数=%s", article.id, len(stored.questions))
        return stored

    def generate_stage_one(self, article: Article, level: str) -> tuple[QuizQuestion, ...]:
        """生成第一轮 3 道当前等级题；校验/审核失败时带原因重试一次。"""
        weak_skills = self._weak_skills()
        last_reason = ""
        for attempt in range(2):
            messages = build_quiz_stage_one_messages(
                article,
                level,
                STAGE_ONE_CHOICE_COUNT,
                weak_skills=weak_skills,
                repair_note=last_reason if attempt else "",
            )
            logger.info(
                "阅读检测第一轮 prompt=%s level=%s attempt=%s weak=%s article=%s",
                PROMPT_VERSION,
                level,
                attempt + 1,
                weak_skills,
                article.title,
            )
            text = self._client.complete_json(
                messages,
                temperature=0.6 if attempt == 0 else 0.85,
                max_tokens=STAGE_ONE_MAX_TOKENS,
                task="阅读检测·第一轮出题",
            )
            try:
                questions = parse_stage_one_questions(text, level)
            except InvalidQuizError as exc:
                last_reason = str(exc)
                logger.warning("第一轮题目未通过校验（第 %s 次）：%s", attempt + 1, exc)
                continue
            reviewed = self._review_questions(article, questions)
            if len(reviewed) >= STAGE_ONE_CHOICE_COUNT:
                return self._shuffle_batch(reviewed[:STAGE_ONE_CHOICE_COUNT])
            last_reason = (
                f"上一次审核后只剩 {len(reviewed)} 道有效题；"
                "请避免答案不唯一、答案与文章矛盾或超出文章的题目"
            )
            logger.warning("第一轮题目审核后不足（第 %s 次）：%s", attempt + 1, last_reason)
        raise AIError(
            "AI 出的题目没有通过校验",
            f"最后一次原因：{last_reason}。可以重试，或换一个更稳定的模型。",
            retryable=True,
            kind="format",
        )

    def generate_stage_two(
        self,
        article: Article,
        level: str,
        target_level: str,
    ) -> tuple[QuizQuestion, ...]:
        """生成第二轮 2 道边界题 + 2 道英译中题；校验/审核失败时带原因重试一次。"""
        weak_skills = self._weak_skills()
        last_reason = ""
        for attempt in range(2):
            messages = build_quiz_stage_two_messages(
                article,
                level,
                target_level,
                weak_skills=weak_skills,
                repair_note=last_reason if attempt else "",
            )
            logger.info(
                "阅读检测第二轮 prompt=%s level=%s target=%s attempt=%s weak=%s article=%s",
                PROMPT_VERSION,
                level,
                target_level,
                attempt + 1,
                weak_skills,
                article.title,
            )
            text = self._client.complete_json(
                messages,
                temperature=0.6 if attempt == 0 else 0.85,
                max_tokens=STAGE_TWO_MAX_TOKENS,
                task="阅读检测·第二轮出题",
            )
            try:
                questions = parse_stage_two_questions(text, level, target_level)
            except InvalidQuizError as exc:
                last_reason = str(exc)
                logger.warning("第二轮题目未通过校验（第 %s 次）：%s", attempt + 1, exc)
                continue
            reviewed = self._review_questions(article, questions)
            choices = [question for question in reviewed if not question.is_short]
            productions = [question for question in reviewed if question.is_short]
            if (
                len(choices) >= STAGE_TWO_CHOICE_COUNT
                and len(productions) >= STAGE_TWO_PRODUCTION_COUNT
            ):
                batch = choices[:STAGE_TWO_CHOICE_COUNT] + productions[:STAGE_TWO_PRODUCTION_COUNT]
                return self._shuffle_batch(tuple(batch))
            last_reason = (
                f"上一次审核后选择题 {len(choices)} 道、产出题 {len(productions)} 道，数量不足；"
                "请确保选择题答案唯一、产出题可作答"
            )
            logger.warning("第二轮题目审核后不足（第 %s 次）：%s", attempt + 1, last_reason)
        raise AIError(
            "AI 出的第二轮题目没有通过校验",
            f"最后一次原因：{last_reason}。可以重试，或换一个更稳定的模型。",
            retryable=True,
            kind="format",
        )

    def _shuffle_batch(
        self,
        questions: tuple[QuizQuestion, ...],
    ) -> tuple[QuizQuestion, ...]:
        if not self._shuffle_options:
            return questions
        return tuple(shuffle_question_options(question, self._rng) for question in questions)

    def _weak_skills(self) -> tuple[str, ...]:
        """最近几次正式检测的低正确率技能 + 学习者记忆里的近期错因，喂给出题 prompt。

        样本太少（少于 WEAK_SKILL_MIN_SAMPLES）不参与，避免一次失误被当成长期弱点；
        M5.2 起把 `level_evidence.error_types` 的 Top2 也并进来——检测样本少时，
        记忆里的错因仍能提示模型该覆盖哪些技能。
        """
        weak: list[str] = []
        recent = self._quizzes.list_recent_completed_with_titles(limit=8)
        quizzes = [quiz for quiz, _ in recent if not quiz.practice_mode]
        if quizzes:
            stats = skill_stats(quizzes)
            weak = [
                stat.skill
                for stat in stats
                if stat.skill
                and stat.skill != "unknown"
                and stat.skill.lower() not in _PRODUCTION_SKILLS
                and stat.total >= WEAK_SKILL_MIN_SAMPLES
                and stat.accuracy < WEAK_SKILL_MAX_ACCURACY
            ]

        memory = load_memory(self._memory_provider)
        existing = {item.strip().lower() for item in weak}
        for item in memory.error_types:
            key = item.strip().lower()
            if key in _MEMORY_ERROR_SKILLS and key not in existing:
                weak.append(key)
                existing.add(key)
        return tuple(weak[:WEAK_SKILL_LIMIT])

    def _review_questions(
        self,
        article: Article,
        questions: tuple[QuizQuestion, ...],
    ) -> tuple[QuizQuestion, ...]:
        """题目审核 pass：返回剔除不合格题后的结果。

        审核本身失败（网络/JSON）时保留原题，不能因为审核模型抽风就让检测不可用。
        """
        if not questions:
            return questions
        messages = build_question_review_messages(article, questions)
        expected = tuple(
            None if question.is_short else question.answer_index for question in questions
        )
        try:
            text = self._client.complete_json(
                messages,
                temperature=0.1,
                max_tokens=QUESTION_REVIEW_MAX_TOKENS,
                task="阅读检测·题目审核",
            )
            invalid = parse_question_review(text, len(questions), expected)
        except (AIError, JsonParseError, InvalidQuizError) as exc:
            logger.warning("题目审核失败，保留原题：%s", exc)
            return questions
        if invalid:
            logger.info("题目审核剔除 %s 道：%s", len(invalid), invalid)
        return tuple(question for index, question in enumerate(questions) if index not in invalid)

    def advance_stage(
        self,
        article: Article,
        quiz: ReadingQuiz,
        answers: tuple[int, ...],
        *,
        choice_notes: Sequence[str] = (),
    ) -> ReadingQuiz:
        """写入第一轮作答，按正确率生成第二轮并追加到同一套题。

        `choice_notes` 与选择题一一对应，只有选了「不知道」的题会保留说明。
        """
        if quiz.id is None:
            raise AIError("题目还没有保存", "请先重新打开阅读检测。")
        if quiz.completed:
            return quiz
        if quiz.stage >= 2:
            return quiz

        normalized = _normalize_answers(answers, quiz)
        if any(index == -1 for index in normalized):
            raise AIError("还有选择题没有作答", "请把第一轮的每道选择题都选完。")
        notes = _normalize_choice_notes(choice_notes, quiz, normalized)

        correct = sum(
            1
            for question, chosen in zip(quiz.choice_questions, normalized, strict=False)
            if chosen == question.answer_index
        )
        target = choose_boundary_level(quiz.level, correct, quiz.choice_total)
        logger.info(
            "第一轮 %s/%s，边界等级 %s → %s",
            correct,
            quiz.choice_total,
            quiz.level,
            target,
        )

        # 先保存第一轮答案，再请求第二轮：出题失败/关窗后重进仍能回显已选项。
        self._quizzes.save_stage_answers(quiz.id, normalized, notes)
        questions = self.generate_stage_two(article, quiz.level, target)
        stored = self._quizzes.append_questions(quiz.id, questions, stage=2)
        logger.info("第二轮已追加：quiz=%s 总题数=%s", quiz.id, len(stored.questions))
        return stored

    # ------------------------------------------------------------ 判分 / 调级
    def submit(
        self,
        article: Article,
        quiz: ReadingQuiz,
        answers: tuple[int, ...],
        profile: Profile,
        *,
        short_answer_text: str = "",
        self_report: str = "ok",
        production_texts: tuple[str, ...] | list[str] | None = None,
        choice_notes: Sequence[str] = (),
    ) -> QuizOutcome:
        """本地判分 → 盲评 + AI 讲评 → 本地后验 → 保存画像与结果。

        AI 失败时不会修改画像；题目缓存保留，用户可以直接重试。
        """
        if quiz.id is None:
            raise AIError("题目还没有保存", "请先重新打开阅读检测。")
        if quiz.completed:
            raise AIError("今天的阅读检测已经完成了", "可以直接查看结果，明天读新文章再做。")
        if quiz.stage < 2:
            raise AIError("请先完成第一轮题目", "第一轮提交后才会生成边界题。")

        normalized = _normalize_answers(answers, quiz)
        if any(index == -1 for index in normalized):
            raise AIError("还有选择题没有作答", "请把每道选择题都选完再提交。")
        notes = _normalize_choice_notes(choice_notes, quiz, normalized)

        texts = _normalize_production_texts(quiz, production_texts, short_answer_text)
        choice_correct = sum(
            1
            for question, chosen in zip(quiz.choice_questions, normalized, strict=False)
            if chosen == question.answer_index
        )

        # M7.1：真实文章固定练习模式——本地判分、逐题讲评用预置解析，
        # 不调用 AI、不写等级证据、不改画像。
        if (article.source or "") == "real":
            return self._submit_real_practice(
                article,
                quiz,
                normalized,
                notes,
                profile,
                self_report,
                choice_correct,
            )

        # 练习模式的历史只用于展示，不进入讲评历史/难度统计，避免影响后续判断。
        recent = [
            (item_quiz, title)
            for item_quiz, title in self._quizzes.list_recent_completed_with_titles(limit=8)
            if not item_quiz.practice_mode
        ]
        history = history_entries(recent)
        completed = [item[0] for item in recent]
        practice_mode = self.is_practice_day(article, quiz.id)

        # 盲评和讲评互不依赖：正式检测时并行调用，练习模式直接跳过盲评
        # （练习结果不写等级证据，盲评没有任何消费者）。
        blind_level = ""
        if practice_mode:
            data = self._grade_with_repair(
                profile,
                article,
                quiz,
                normalized,
                choice_correct,
                self_report=self_report,
                production_texts=texts,
                choice_notes=notes,
                history=history,
                completed=completed,
            )
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                blind_future = pool.submit(
                    self._blind_level,
                    article,
                    quiz,
                    normalized,
                    choice_correct,
                    texts,
                    notes,
                )
                data = self._grade_with_repair(
                    profile,
                    article,
                    quiz,
                    normalized,
                    choice_correct,
                    self_report=self_report,
                    production_texts=texts,
                    choice_notes=notes,
                    history=history,
                    completed=completed,
                )
                try:
                    blind_level = blind_future.result()
                except Exception:  # noqa: BLE001 - 盲评失败不能影响讲评
                    logger.exception("盲评线程异常，本次不加入盲评信号")

        productions = _parse_productions(data, quiz, texts)
        production_scores = tuple(item.score for item in productions)
        production_scored = tuple(item.scored for item in productions)
        unscored_count = sum(1 for item in productions if not item.scored)
        short_score = production_scores[0] if production_scores else 0
        explanations = _parse_explanations(data, quiz)
        error_types = _parse_error_types(data, quiz, normalized)
        evidence = score_quiz_evidence(
            profile.cefr_level,
            quiz,
            normalized,
            production_scores=production_scores,
            production_scored=production_scored,
            self_report=self_report,
        )

        # 只有真实作答（选择题 + 产出题）会落库并参与历史回放；
        # 盲评与自评是“本次检测”的弱信号，只影响当前后验，不能随历史累积
        # （否则一次 0.5 的盲评会通过 0.75^age 回放变成 ~1.5 的权重）。
        stored_observations = list(
            observations_from_quiz(
                quiz,
                normalized,
                production_scores,
                production_scored=production_scored,
            )
        )
        current_observations = list(stored_observations)
        current_observations += list(
            observations_from_blind_level(
                blind_level,
                current_level=profile.cefr_level,
                weight=BLIND_OBSERVATION_WEIGHT,
            )
        )
        current_observations += list(observations_from_self_report(self_report, profile.cefr_level))
        observations = list(current_observations)
        if self._evidence is not None:
            observations += list(replay_evidence(self._evidence.list_recent(limit=5)))
        if self._reviews is not None:
            observations += list(review_band_observations(self._reviews.list_band_stats()))
        posterior = estimate_level(profile.cefr_level, observations)

        if practice_mode:
            recommended = profile.cefr_level
            decision = LevelDecision(
                profile.cefr_level,
                profile.pending_level,
                profile.pending_count,
                False,
                bool(profile.pending_level),
            )
        else:
            recommended = posterior.recommended_level
            decision = apply_level_policy(
                profile.cefr_level,
                recommended,
                profile.pending_level,
                profile.pending_count,
            )

        result = assessment_result_from_data(data, profile.cefr_level)
        result = replace(result, cefr=decision.applied_level)
        result = self._merge_profile_details(profile, result)
        score = choice_correct + sum(production_scores)
        if not result.comment:
            result = replace(
                result,
                comment=_level_comment(
                    profile.cefr_level,
                    decision,
                    recommended,
                    score,
                    quiz.total,
                    confidence=posterior.confidence,
                    practice_mode=practice_mode,
                    unscored=unscored_count,
                ),
            )

        timestamp = now_iso()
        reason = _level_reason(decision, posterior, practice_mode=practice_mode)
        if practice_mode:
            # 练习模式完全不写画像：等级、置信度、待定状态都保持原样。
            stored_profile = profile
        else:
            stored_profile = self._profiles.save(
                Profile(
                    cefr_level=result.cefr,
                    vocab_estimate=result.vocab_estimate,
                    strengths=result.strengths,
                    weaknesses=result.weaknesses,
                    comment=result.comment,
                    assessed_at=timestamp,
                    updated_at=timestamp,
                    pending_level=decision.pending_level,
                    pending_count=decision.pending_count,
                    level_confidence=posterior.confidence,
                    level_updated_reason=reason,
                )
            )
        stored_quiz = self._quizzes.save_result(
            quiz.id,
            answers=normalized,
            choice_notes=notes,
            explanations=explanations,
            error_types=error_types,
            short_answer_text=texts[0] if texts else "",
            short_answer_score=short_score,
            self_report=self_report,
            score=score,
            recommended_level=recommended,
            previous_level=profile.cefr_level,
            result_level=stored_profile.cefr_level,
            pending_level=decision.pending_level,
            pending_count=decision.pending_count,
            feedback=result.comment,
            evidence_ratio=evidence.ratio,
            productions=productions,
            level_confidence=posterior.confidence,
            posterior=posterior_to_tuple(posterior),
            practice_mode=practice_mode,
            completed_at=timestamp,
        )

        # 练习模式不写等级证据：它只用于当场练手，不能影响下一次正式检测的后验。
        if self._evidence is not None and not practice_mode:
            self._evidence.insert(
                LevelEvidence(
                    source="quiz",
                    observed_at=timestamp,
                    evidence_date=article.article_date,
                    article_level=quiz.level,
                    quiz_id=quiz.id,
                    choice_correct=choice_correct,
                    choice_total=quiz.choice_total,
                    short_score=sum(production_scores),
                    short_total=quiz.scored_production_total,
                    weighted_points=evidence.points,
                    evidence_ratio=evidence.ratio,
                    error_types=error_types,
                    observations=tuple(stored_observations),
                    posterior=posterior_to_tuple(posterior),
                    confidence=posterior.confidence,
                )
            )

        logger.info(
            "阅读检测完成：选择 %s/%s，产出 %s/%s，总分 %s/%s，等级 %s → %s"
            "（后验 %s %.2f，建议 %s，待定 %s/%s，练习=%s）",
            choice_correct,
            quiz.choice_total,
            sum(production_scores),
            quiz.short_total,
            score,
            quiz.total,
            profile.cefr_level,
            stored_profile.cefr_level,
            posterior.map_level,
            posterior.confidence,
            recommended,
            decision.pending_level or "-",
            decision.pending_count,
            practice_mode,
        )
        return QuizOutcome(
            quiz=stored_quiz,
            profile=stored_profile,
            result=result,
            previous_level=profile.cefr_level,
            recommended_level=recommended,
            pending_level=decision.pending_level,
            pending_count=decision.pending_count,
            evidence=evidence,
            posterior=posterior,
            confidence=posterior.confidence,
            practice_mode=practice_mode,
            blind_level=blind_level,
        )

    def _submit_real_practice(
        self,
        article: Article,
        quiz: ReadingQuiz,
        normalized: tuple[int, ...],
        notes: tuple[str, ...],
        profile: Profile,
        self_report: str,
        choice_correct: int,
    ) -> QuizOutcome:
        """真实文章练习：0 次 AI 的本地判分 + 供给反馈回写（M7.1 / M7.2）。"""
        if quiz.id is None:
            raise AIError("题目还没有保存", "请先重新打开阅读检测。")
        questions = quiz.choice_questions
        explanations = tuple(
            quiz.explanations[index]
            if index < len(quiz.explanations) and quiz.explanations[index]
            else question.explanation
            for index, question in enumerate(questions)
        )
        total = len(questions)
        score = int(choice_correct)
        feedback = "真实文章练习：本地判分，不调整等级"
        timestamp = now_iso()

        stored = self._quizzes.save_result(
            quiz.id,
            answers=normalized,
            choice_notes=notes,
            explanations=explanations,
            score=score,
            previous_level=profile.cefr_level,
            result_level=profile.cefr_level,
            feedback=feedback,
            self_report=self_report,
            recommended_level=profile.cefr_level,
            pending_level=profile.pending_level,
            pending_count=profile.pending_count,
            practice_mode=True,
            completed_at=timestamp,
        )

        # 完成反馈驱动下一篇的间隔与难度；失败只记日志，不能把答题结果搞丢。
        if self._real_texts is not None and article.source_ref:
            ratio = score / total if total else 0.0
            try:
                record_real_text_completion(
                    self._real_texts,
                    article.source_ref,
                    score=ratio,
                    self_report=self_report,
                    completed_at=timestamp,
                )
            except Exception:  # noqa: BLE001 - 反馈是增强信息
                logger.warning("回写真实文章完成反馈失败：%s", article.source_ref, exc_info=True)

        result = AssessmentResult(
            cefr=profile.cefr_level,
            vocab_estimate=profile.vocab_estimate,
            strengths=profile.strengths,
            weaknesses=profile.weaknesses,
            comment=feedback,
        )
        logger.info(
            "真实文章练习完成：article=%s 选择 %s/%s，自评=%s",
            article.id,
            score,
            total,
            self_report,
        )
        return QuizOutcome(
            quiz=stored,
            profile=profile,
            result=result,
            previous_level=profile.cefr_level,
            recommended_level=profile.cefr_level,
            pending_level=profile.pending_level,
            pending_count=profile.pending_count,
            practice_mode=True,
        )

    def _grade_with_repair(
        self,
        profile: Profile,
        article: Article,
        quiz: ReadingQuiz,
        answers: tuple[int, ...],
        choice_correct: int,
        *,
        self_report: str,
        production_texts: list[str],
        choice_notes: Sequence[str],
        history: tuple[QuizHistoryEntry, ...],
        completed: list[ReadingQuiz],
    ) -> dict[str, object]:
        """讲评调用；JSON 或产出题分数缺失时带原因重试一次。

        第二次仍缺分数时不再阻塞用户：缺失的产出题会标记为“未评分”，
        不进入等级证据，避免把模型格式问题算成学生答错。
        """
        last_note = ""
        for attempt in range(2):
            messages = build_quiz_grading_messages(
                profile,
                article,
                quiz,
                answers,
                choice_correct,
                self_report=self_report,
                production_texts=production_texts,
                choice_notes=choice_notes,
                history=history,
                difficulty_stats=difficulty_stats(completed),
                skill_stats=skill_stats(completed),
                repair_note=last_note,
            )
            logger.info(
                "阅读检测讲评 prompt=%s article=%s choice=%s/%s level=%s attempt=%s",
                PROMPT_VERSION,
                article.title,
                choice_correct,
                quiz.choice_total,
                profile.cefr_level,
                attempt + 1,
            )
            text = self._client.complete_json(
                messages,
                temperature=0.2,
                max_tokens=GRADING_MAX_TOKENS,
                task="阅读检测·讲评",
            )
            try:
                data = parse_json_object(text)
            except JsonParseError as exc:
                if attempt == 0:
                    last_note = "输出不是合法 JSON；请只输出一个 JSON 对象，不要解释或代码围栏。"
                    logger.warning("阅读检测讲评 JSON 解析失败，按原因重试：%s", exc)
                    continue
                logger.warning("阅读检测讲评 JSON 解析失败：%s", exc)
                raise AIError(
                    "AI 讲评结果无法解析",
                    "模型没有按 JSON 格式返回，可以重试一次，题目会保留。",
                    retryable=True,
                    kind="format",
                ) from exc

            missing = _missing_production_scores(data, quiz, production_texts)
            if not missing:
                return data
            if attempt == 0:
                last_note = (
                    f"上一次输出缺少第 {', '.join(str(index + 1) for index in missing)} 道"
                    f"产出题的分数；production_scores 必须恰好 {quiz.short_total} 条，"
                    "每条为 0 或 1。"
                )
                logger.warning("讲评缺少产出题分数，按原因重试：%s", missing)
                continue
            logger.warning("讲评仍缺少产出题分数，标记为未评分：%s", missing)
            return data
        raise AIError(
            "AI 讲评失败",
            "请稍后重试，题目和作答都会保留。",
            retryable=True,
            kind="format",
        )

    def _blind_level(
        self,
        article: Article,
        quiz: ReadingQuiz,
        answers: tuple[int, ...],
        choice_correct: int,
        production_texts: list[str],
        choice_notes: Sequence[str] = (),
    ) -> str:
        """盲评调用；失败时返回空串，不影响讲评与本地后验。"""
        messages = build_blind_level_messages(
            article, quiz, answers, choice_correct, production_texts, choice_notes
        )
        try:
            text = self._client.complete_json(
                messages,
                temperature=0.2,
                max_tokens=BLIND_MAX_TOKENS,
                task="阅读检测·盲评",
            )
            data = parse_json_object(text)
        except (AIError, JsonParseError) as exc:
            logger.warning("盲评失败，本次不加入盲评信号：%s", exc)
            return ""
        level = normalize_cefr_level(data.get("cefr"))
        if level:
            logger.info("盲评建议等级：%s", level)
        return level

    @staticmethod
    def _merge_profile_details(profile: Profile, result: AssessmentResult) -> AssessmentResult:
        """AI 少给字段时保留旧画像，别让一次答题把词汇量/优势清空。"""
        return AssessmentResult(
            cefr=result.cefr,
            vocab_estimate=result.vocab_estimate or profile.vocab_estimate,
            strengths=result.strengths or profile.strengths,
            weaknesses=result.weaknesses or profile.weaknesses,
            comment=result.comment,
        )


def history_entries(
    recent: list[tuple[ReadingQuiz, str]],
) -> tuple[QuizHistoryEntry, ...]:
    entries: list[QuizHistoryEntry] = []
    for quiz, title in recent:
        scored_productions = [item for item in quiz.production_answers if item.scored]
        production_score = sum(item.score for item in scored_productions)
        production_total = len(scored_productions)
        entries.append(
            QuizHistoryEntry(
                date=quiz.article_date,
                title=title or f"#{quiz.article_id}",
                level=quiz.level,
                score=quiz.choice_correct + production_score,
                total=quiz.choice_total + production_total,
                choice_correct=quiz.choice_correct,
                choice_total=quiz.choice_total,
                production_score=production_score,
                production_total=production_total,
                difficulties=tuple(
                    normalize_cefr_level(question.difficulty, quiz.level)
                    for question in quiz.questions
                ),
                error_types=quiz.error_types,
            )
        )
    return tuple(entries)


def _level_comment(
    previous: str,
    decision: LevelDecision,
    recommended: str,
    score: int,
    total: int,
    *,
    confidence: float = 0.0,
    practice_mode: bool = False,
    unscored: int = 0,
) -> str:
    if practice_mode:
        text = (
            f"检测得分 {score}/{total}；今天已经完成过一次正式检测，"
            f"本次只练手，等级保持 {decision.applied_level}。"
        )
    elif decision.waiting:
        text = (
            f"检测得分 {score}/{total}，本地后验建议升到 {recommended}"
            f"（置信度 {confidence:.2f}）；这是第 "
            f"{decision.pending_count}/{UPGRADE_EVIDENCE_REQUIRED} 次向上证据，"
            f"等级暂时保持 {decision.applied_level}。"
        )
    elif decision.changed:
        text = (
            f"检测得分 {score}/{total}，等级从 {previous} 调整为 "
            f"{decision.applied_level}（置信度 {confidence:.2f}）。"
        )
    else:
        text = (
            f"检测得分 {score}/{total}，等级保持 {decision.applied_level}"
            f"（置信度 {confidence:.2f}）。"
        )
    if unscored:
        text += f" 有 {unscored} 道产出题 AI 未给分，未计入等级证据。"
    return text


def _level_reason(
    decision: LevelDecision,
    posterior: LevelPosterior,
    *,
    practice_mode: bool,
) -> str:
    if practice_mode:
        return "今日已完成一次正式检测，本次练习不调整等级。"
    if decision.changed:
        return f"阅读检测后验：{decision.applied_level} （置信度 {posterior.confidence:.2f}）"
    if decision.waiting:
        return (
            f"后验建议升到 {decision.pending_level}，"
            f"等待连续证据（{decision.pending_count}/{UPGRADE_EVIDENCE_REQUIRED}）"
        )
    return f"阅读检测后验保持 {decision.applied_level}（置信度 {posterior.confidence:.2f}）"


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


__all__ = [
    "EvidenceSummary",
    "InvalidQuizError",
    "LevelDecision",
    "QuizOutcome",
    "ReadingQuizService",
    "UNKNOWN_ANSWER",
    "UNKNOWN_LABEL",
    "apply_level_policy",
    "choose_boundary_level",
    "decide_recommended_level",
    "history_entries",
    "parse_question_review",
    "parse_quiz_questions",
    "parse_stage_one_questions",
    "parse_stage_two_questions",
    "score_quiz_evidence",
    "shuffle_question_options",
]
