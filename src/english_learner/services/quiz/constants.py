"""阅读检测的阈值、额度与常量。"""

from __future__ import annotations

from ...models.entities import CEFR_LEVELS
from ..prompts import QUIZ_PRODUCTION_COUNT, QUIZ_STAGE_ONE_COUNT, QUIZ_STAGE_TWO_CHOICE_COUNT

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

# 最近几套题的题干避让：只取最近 4 套、最多 8 条，避免 prompt 被稀释。
AVOID_STEM_QUIZ_LIMIT = 4
AVOID_STEM_LIMIT = 8
AVOID_STEM_CHARS = 100


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
