"""摸底测评的评分与落库。

流程（对应 HISTORY.md M1-3）：
1. 本地先算客观题得分（选择题不需要 AI）；
2. 把题目、标准答案、用户答案、翻译一起交给 AI，要求只返回评分 JSON；
3. 解析并容错，CEFR 不合法时回退到这套题的 level_hint；
4. 结果写 profile 表；
5. AI 失败时界面可以手动选级，`save_manual()` 保证流程不卡死。

注意：本模块是同步阻塞的，只能在 TaskWorker 里调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..models.entities import (
    CEFR_LEVELS,
    AssessmentResult,
    Profile,
    now_iso,
)
from ..storage.repositories import ProfileRepo
from .ai_client import AIError
from .ai_protocol import TextCompleter
from .assessment_bank import AssessmentSet, Question
from .json_utils import JsonParseError, as_int, as_str_list, as_text, parse_json_object
from .prompts import PROMPT_VERSION, build_assessment_messages

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnswerSheet:
    """用户的作答。

    - `comprehension` / `vocabulary`：每道题选中的选项下标，-1 表示未作答，
      `assessment_bank.UNKNOWN_ANSWER`（-2）表示用户主动选了“不知道”；
    - `*_notes`：与题目一一对应的补充说明（用户写的“这题哪里看不懂”），
      空串表示没写。它们只作为 AI 评分的参考，不参与本地计分；
    - `translation`：翻译题译文，空串表示未作答。
    """

    comprehension: tuple[int, ...] = ()
    vocabulary: tuple[int, ...] = ()
    translation: str = ""
    comprehension_notes: tuple[str, ...] = ()
    vocabulary_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectiveScore:
    """客观题得分（本地计算，不经 AI）。"""

    comprehension_correct: int
    comprehension_total: int
    vocabulary_correct: int
    vocabulary_total: int

    @property
    def total_correct(self) -> int:
        return self.comprehension_correct + self.vocabulary_correct

    @property
    def total_questions(self) -> int:
        return self.comprehension_total + self.vocabulary_total

    @property
    def percent(self) -> float:
        if self.total_questions == 0:
            return 0.0
        return self.total_correct * 100.0 / self.total_questions

    def summary(self) -> str:
        return (
            f"阅读 {self.comprehension_correct}/{self.comprehension_total}，"
            f"词汇 {self.vocabulary_correct}/{self.vocabulary_total}，"
            f"合计 {self.total_correct}/{self.total_questions}"
        )


def _count_correct(questions: tuple[Question, ...], answers: tuple[int, ...]) -> int:
    correct = 0
    for index, question in enumerate(questions):
        if index < len(answers) and answers[index] == question.answer_index:
            correct += 1
    return correct


def score_answers(assessment_set: AssessmentSet, sheet: AnswerSheet) -> ObjectiveScore:
    """只做本地计分，容忍少答 / 多答。"""
    return ObjectiveScore(
        comprehension_correct=_count_correct(assessment_set.comprehension, sheet.comprehension),
        comprehension_total=len(assessment_set.comprehension),
        vocabulary_correct=_count_correct(assessment_set.vocabulary, sheet.vocabulary),
        vocabulary_total=len(assessment_set.vocabulary),
    )


def assessment_result_from_data(data: dict[str, object], fallback_level: str) -> AssessmentResult:
    """把 AI 的评级 JSON 转成 AssessmentResult，非法等级回退到 fallback_level。

    测评评分和“阅读反馈调级”共用这套字段容错，避免两份解析逻辑漂移。
    """
    raw = as_text(data.get("cefr"), fallback_level).upper()
    # 兼容 "B1+" / "B2/C1" 这类写法；A1 归到本应用支持的最低档 A2。
    cefr = raw[:2] if raw[:2] in CEFR_LEVELS else raw
    if cefr == "A1":
        cefr = "A2"
    if cefr not in CEFR_LEVELS:
        logger.warning("AI 返回了非法等级 %r，回退到 %s", raw, fallback_level)
        cefr = fallback_level
    vocab = max(0, min(as_int(data.get("vocab_estimate"), 0), 30000))
    return AssessmentResult(
        cefr=cefr,
        vocab_estimate=vocab,
        strengths=as_str_list(data.get("strengths"))[:5],
        weaknesses=as_str_list(data.get("weaknesses"))[:5],
        comment=as_text(data.get("comment")),
    )


class AssessmentService:
    """评分 + 保存 profile。"""

    def __init__(self, client: TextCompleter, profile_repo: ProfileRepo) -> None:
        self._client = client
        self._profiles = profile_repo

    # ------------------------------------------------------------------ 评分
    def grade(self, assessment_set: AssessmentSet, sheet: AnswerSheet) -> AssessmentResult:
        """调用 AI 评分；解析失败统一抛 AIError，让界面走手动选级。"""
        score = score_answers(assessment_set, sheet)
        messages = build_assessment_messages(assessment_set, sheet, score)
        logger.info(
            "测评评分开始 prompt=%s set=%s score=%s",
            PROMPT_VERSION,
            assessment_set.key,
            score.summary(),
        )
        text = self._client.complete_json(
            messages, temperature=0.2, max_tokens=1200, task="水平测评"
        )
        try:
            data = parse_json_object(text)
        except JsonParseError as exc:
            logger.warning("测评评分 JSON 解析失败：%s", exc)
            raise AIError(
                "AI 评分结果无法解析",
                "模型可能没有按 JSON 格式返回。可以重试，或直接手动选择等级。",
            ) from exc

        return self._to_result(data, assessment_set)

    @staticmethod
    def _to_result(data: dict[str, object], assessment_set: AssessmentSet) -> AssessmentResult:
        return assessment_result_from_data(data, assessment_set.level_hint)

    # ------------------------------------------------------------------ 落库
    def save(self, result: AssessmentResult) -> Profile:
        """把评分结果写入 profile（单行表，覆盖旧值）。"""
        cefr = result.cefr.upper()
        if cefr not in CEFR_LEVELS:
            raise ValueError(f"非法 CEFR 等级：{result.cefr}")
        timestamp = now_iso()
        profile = Profile(
            cefr_level=cefr,
            vocab_estimate=int(result.vocab_estimate),
            strengths=tuple(result.strengths),
            weaknesses=tuple(result.weaknesses),
            comment=result.comment,
            assessed_at=timestamp,
            updated_at=timestamp,
        )
        stored = self._profiles.save(profile)
        logger.info("测评结果已保存：%s，估算词汇量 %s", stored.cefr_level, stored.vocab_estimate)
        return stored

    def submit(self, assessment_set: AssessmentSet, sheet: AnswerSheet) -> Profile:
        """评分 + 落库，供后台任务一次性调用。"""
        return self.save(self.grade(assessment_set, sheet))

    def save_manual(self, cefr: str, comment: str = "手动选择等级") -> Profile:
        """AI 评分失败时的兜底：用户自己选一个等级，流程继续。"""
        return self.save(AssessmentResult(cefr=cefr, comment=comment))
