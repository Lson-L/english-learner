"""阅读检测：兼容门面。

实现已按层拆分到 `english_learner.services.quiz` 包：
- `quiz.models`：结果对象与异常；
- `quiz.parsing`：AI 输出解析与本地归一化；
- `quiz.scoring`：本地证据与等级策略；
- `quiz.constants`：阈值与提示词常量；
- `quiz.service`：`ReadingQuizService` 编排。

本模块只做 re-export，保持既有 import 路径不变。
"""

from __future__ import annotations

from .assessment_bank import UNKNOWN_ANSWER, UNKNOWN_LABEL
from .quiz.constants import (
    AVOID_STEM_CHARS,
    AVOID_STEM_LIMIT,
    AVOID_STEM_QUIZ_LIMIT,
    BLIND_MAX_TOKENS,
    BLIND_OBSERVATION_WEIGHT,
    EVIDENCE_DOWN_THRESHOLD,
    EVIDENCE_STRONG_THRESHOLD,
    EVIDENCE_UP_THRESHOLD,
    GRADING_MAX_TOKENS,
    LEVEL_ORDER,
    MAX_CHOICE_NOTE_CHARS,
    MAX_CHOICE_QUESTIONS,
    MAX_QUIZ_OPTIONS,
    MIN_CHOICE_QUESTIONS,
    MIN_QUIZ_OPTIONS,
    QUESTION_REVIEW_MAX_TOKENS,
    STAGE_ONE_CHOICE_COUNT,
    STAGE_ONE_MAX_TOKENS,
    STAGE_TWO_CHOICE_COUNT,
    STAGE_TWO_MAX_TOKENS,
    STAGE_TWO_PRODUCTION_COUNT,
    UPGRADE_EVIDENCE_REQUIRED,
    WEAK_SKILL_LIMIT,
    WEAK_SKILL_MAX_ACCURACY,
    WEAK_SKILL_MIN_SAMPLES,
)
from .quiz.models import EvidenceSummary, InvalidQuizError, LevelDecision, QuizOutcome
from .quiz.parsing import (
    parse_question_review,
    parse_quiz_questions,
    parse_stage_one_questions,
    parse_stage_two_questions,
    shuffle_question_options,
)
from .quiz.scoring import (
    apply_level_policy,
    choose_boundary_level,
    decide_recommended_level,
    history_entries,
    score_quiz_evidence,
)
from .quiz.service import ReadingQuizService

__all__ = [
    "AVOID_STEM_CHARS",
    "AVOID_STEM_LIMIT",
    "AVOID_STEM_QUIZ_LIMIT",
    "BLIND_MAX_TOKENS",
    "BLIND_OBSERVATION_WEIGHT",
    "EVIDENCE_DOWN_THRESHOLD",
    "EVIDENCE_STRONG_THRESHOLD",
    "EVIDENCE_UP_THRESHOLD",
    "GRADING_MAX_TOKENS",
    "LEVEL_ORDER",
    "MAX_CHOICE_NOTE_CHARS",
    "MAX_CHOICE_QUESTIONS",
    "MAX_QUIZ_OPTIONS",
    "MIN_CHOICE_QUESTIONS",
    "MIN_QUIZ_OPTIONS",
    "QUESTION_REVIEW_MAX_TOKENS",
    "STAGE_ONE_CHOICE_COUNT",
    "STAGE_ONE_MAX_TOKENS",
    "STAGE_TWO_CHOICE_COUNT",
    "STAGE_TWO_MAX_TOKENS",
    "STAGE_TWO_PRODUCTION_COUNT",
    "WEAK_SKILL_LIMIT",
    "WEAK_SKILL_MAX_ACCURACY",
    "WEAK_SKILL_MIN_SAMPLES",
    "UNKNOWN_ANSWER",
    "UNKNOWN_LABEL",
    "UPGRADE_EVIDENCE_REQUIRED",
    "EvidenceSummary",
    "InvalidQuizError",
    "LevelDecision",
    "QuizOutcome",
    "ReadingQuizService",
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
