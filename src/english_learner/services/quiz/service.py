"""ReadingQuizService：阅读检测的出题、判分、讲评与调级编排。"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import TypeVar

from ...models.entities import (
    Article,
    AssessmentResult,
    EvidenceObservation,
    LevelEvidence,
    Profile,
    QuizProduction,
    QuizQuestion,
    ReadingQuiz,
    RealText,
    normalize_cefr_level,
    now_iso,
)
from ...storage.db import DuplicateError
from ...storage.repositories import (
    LevelEvidenceRepo,
    ProfileRepo,
    QuizRepo,
    RealTextRepo,
    ReviewRepo,
)
from ..ai_client import AIError
from ..ai_protocol import TextCompleter
from ..assessment_service import assessment_result_from_data
from ..json_utils import JsonParseError, parse_json_object
from ..learner_memory import LearnerMemory, load_memory
from ..level_estimator import (
    LevelPosterior,
    estimate_level,
    observations_from_blind_level,
    observations_from_quiz,
    observations_from_self_report,
    posterior_to_tuple,
    replay_evidence,
    review_band_observations,
)
from ..prompts import (
    PROMPT_VERSION,
    QUIZ_ANGLE_HINTS,
    QUIZ_BOUNDARY_TYPE_KEYS,
    QUIZ_QUESTION_TYPES,
    QUIZ_STAGE_ONE_BUCKETS,
    QuizHistoryEntry,
    QuizQuestionType,
    build_blind_level_messages,
    build_question_review_messages,
    build_quiz_grading_messages,
    build_quiz_stage_one_messages,
    build_quiz_stage_two_messages,
)
from ..quiz_calibration import difficulty_stats, skill_stats
from ..real_text_feedback import record_completion as record_real_text_completion
from .constants import (
    _MEMORY_ERROR_SKILLS,
    _PRODUCTION_SKILLS,
    AVOID_STEM_CHARS,
    AVOID_STEM_LIMIT,
    AVOID_STEM_QUIZ_LIMIT,
    BLIND_MAX_TOKENS,
    BLIND_OBSERVATION_WEIGHT,
    GRADING_MAX_TOKENS,
    QUESTION_REVIEW_MAX_TOKENS,
    STAGE_ONE_CHOICE_COUNT,
    STAGE_ONE_MAX_TOKENS,
    STAGE_TWO_CHOICE_COUNT,
    STAGE_TWO_MAX_TOKENS,
    STAGE_TWO_PRODUCTION_COUNT,
    WEAK_SKILL_LIMIT,
    WEAK_SKILL_MAX_ACCURACY,
    WEAK_SKILL_MIN_SAMPLES,
)
from .models import EvidenceSummary, InvalidQuizError, LevelDecision, QuizOutcome
from .parsing import (
    _missing_production_scores,
    _normalize_answers,
    _normalize_choice_notes,
    _normalize_production_texts,
    _parse_error_types,
    _parse_explanations,
    _parse_productions,
    parse_question_review,
    parse_stage_one_questions,
    parse_stage_two_questions,
    shuffle_question_options,
)
from .scoring import (
    _level_comment,
    _level_reason,
    apply_level_policy,
    choose_boundary_level,
    history_entries,
    score_quiz_evidence,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True)
class _GradedSubmission:
    """一次提交在“AI 讲评 + 本地解析”之后的中间结果。"""

    data: dict[str, object]
    practice_mode: bool
    blind_level: str
    productions: tuple[QuizProduction, ...]
    production_scores: tuple[int, ...]
    production_scored: tuple[bool, ...]
    unscored_count: int
    short_score: int
    explanations: tuple[str, ...]
    error_types: tuple[str, ...]
    evidence: EvidenceSummary
    stored_observations: tuple[EvidenceObservation, ...]


@dataclass(frozen=True)
class _LevelResolution:
    """本地后验 + 等级策略的结果，以及据此生成的讲评文案。"""

    posterior: LevelPosterior
    decision: LevelDecision
    recommended: str
    result: AssessmentResult
    score: int
    timestamp: str
    reason: str


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

    def save_self_report(self, quiz_id: int | None, self_report: str) -> None:
        """首次检测开场先记录阅读自评（还没作答也能保存，重开检测不丢）。

        `submit` 落库时会用第二轮页面上最终确认的值再写一次。
        """
        if quiz_id is None:
            return
        report = (self_report or "").strip()
        if not report:
            return
        self._quizzes.save_self_report(int(quiz_id), report)

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
        # 题型 / 角度只抽一次：重试时保持同一份出题计划，让 repair_note 针对
        # 同一组题型修正，而不是换一套题重新赌运气。
        question_types = self._pick_stage_one_types()
        angle = self._pick_angle()
        avoid_stems = self._avoid_stems(article.id)
        last_reason = ""
        for attempt in range(2):
            messages = build_quiz_stage_one_messages(
                article,
                level,
                STAGE_ONE_CHOICE_COUNT,
                weak_skills=weak_skills,
                repair_note=last_reason if attempt else "",
                question_types=question_types,
                angle=angle,
                avoid_stems=avoid_stems,
            )
            logger.info(
                "阅读检测第一轮 prompt=%s level=%s attempt=%s weak=%s "
                "types=%s angle=%s avoid=%s article=%s",
                PROMPT_VERSION,
                level,
                attempt + 1,
                weak_skills,
                [item.key for item in question_types],
                angle,
                len(avoid_stems),
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
        *,
        previous: Sequence[QuizQuestion] = (),
    ) -> tuple[QuizQuestion, ...]:
        """生成第二轮 2 道边界题 + 2 道英译中题；校验/审核失败时带原因重试一次。

        `previous` 是第一轮刚出的题：把题干并入避让列表，避免第二轮把同一道
        题换个说法再问一遍。
        """
        weak_skills = self._weak_skills()
        question_types = self._pick_boundary_types(STAGE_TWO_CHOICE_COUNT)
        angle = self._pick_angle()
        # 第一轮题干优先：同一篇文章里重复问法的观感最差。
        previous_stems = tuple(
            (question.prompt or "").strip()[:AVOID_STEM_CHARS]
            for question in previous
            if (question.prompt or "").strip()
        )
        avoid_stems = tuple(dict.fromkeys(previous_stems + self._avoid_stems(article.id)))[
            :AVOID_STEM_LIMIT
        ]
        last_reason = ""
        for attempt in range(2):
            messages = build_quiz_stage_two_messages(
                article,
                level,
                target_level,
                weak_skills=weak_skills,
                repair_note=last_reason if attempt else "",
                question_types=question_types,
                angle=angle,
                avoid_stems=avoid_stems,
            )
            logger.info(
                "阅读检测第二轮 prompt=%s level=%s target=%s attempt=%s weak=%s "
                "types=%s angle=%s avoid=%s article=%s",
                PROMPT_VERSION,
                level,
                target_level,
                attempt + 1,
                weak_skills,
                [item.key for item in question_types],
                angle,
                len(avoid_stems),
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

    def _choice(self, items: Sequence[_T]) -> _T:
        """抽样统一走注入的 rng（测试可复现）；未注入时退回模块级 random。"""
        if self._rng is not None:
            return self._rng.choice(items)
        return random.choice(items)

    def _pick_stage_one_types(self) -> tuple[QuizQuestionType, ...]:
        """第一轮：每个桶各抽一个，保证全局 / 细节 / 推断都覆盖且跨天变化。"""
        chosen: list[QuizQuestionType] = []
        for bucket in QUIZ_STAGE_ONE_BUCKETS:
            candidates = [item for item in QUIZ_QUESTION_TYPES if item.bucket == bucket]
            if candidates:
                chosen.append(self._choice(candidates))
        return tuple(chosen)

    def _pick_boundary_types(self, count: int) -> tuple[QuizQuestionType, ...]:
        """第二轮：从边界题型里抽两个不同类型，尽量分属不同桶。

        第一轮的题型 key 没有落库，做不到“题型完全不重复”；第一轮题干会
        通过 `avoid_stems` 进入 prompt，避免同一道题再问一遍。
        """
        pool = [item for item in QUIZ_QUESTION_TYPES if item.key in QUIZ_BOUNDARY_TYPE_KEYS]
        chosen: list[QuizQuestionType] = []
        buckets: set[str] = set()
        while len(chosen) < count:
            candidates = [
                item for item in pool if item not in chosen and item.bucket not in buckets
            ]
            if not candidates:
                candidates = [item for item in pool if item not in chosen]
            if not candidates:
                break
            item = self._choice(candidates)
            chosen.append(item)
            buckets.add(item.bucket)
        return tuple(chosen)

    def _pick_angle(self) -> str:
        """随机选一个设问侧重，让题干措辞本身产生差异。"""
        return self._choice(QUIZ_ANGLE_HINTS)

    def _avoid_stems(self, article_id: int | None) -> tuple[str, ...]:
        """最近几套题的英文题干，喂回出题 prompt 防止反复用同样的问法。"""
        stems: list[str] = []
        for quiz in self._quizzes.list_recent_with_questions(
            AVOID_STEM_QUIZ_LIMIT,
            exclude_article_id=article_id,
        ):
            for question in quiz.choice_questions:
                stem = (question.prompt or "").strip()[:AVOID_STEM_CHARS]
                # 先截断再去重：两条长题干可能前 100 字符相同，用原串比较会漏掉。
                if stem and stem not in stems:
                    stems.append(stem)
        return tuple(stems[:AVOID_STEM_LIMIT])

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
        questions = self.generate_stage_two(
            article,
            quiz.level,
            target,
            previous=quiz.choice_questions,
        )
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

        graded = self._grade_submission(
            article,
            quiz,
            normalized,
            choice_correct,
            profile=profile,
            self_report=self_report,
            texts=texts,
            notes=notes,
        )
        resolution = self._resolve_level(
            profile,
            quiz,
            graded,
            choice_correct,
            self_report=self_report,
        )
        stored_profile, stored_quiz = self._persist_submission(
            article,
            quiz,
            profile,
            normalized,
            notes,
            texts,
            graded,
            resolution,
            choice_correct,
            self_report=self_report,
        )

        logger.info(
            "阅读检测完成：选择 %s/%s，产出 %s/%s，总分 %s/%s，等级 %s → %s"
            "（后验 %s %.2f，建议 %s，待定 %s/%s，练习=%s）",
            choice_correct,
            quiz.choice_total,
            sum(graded.production_scores),
            quiz.short_total,
            resolution.score,
            quiz.total,
            profile.cefr_level,
            stored_profile.cefr_level,
            resolution.posterior.map_level,
            resolution.posterior.confidence,
            resolution.recommended,
            resolution.decision.pending_level or "-",
            resolution.decision.pending_count,
            graded.practice_mode,
        )
        return QuizOutcome(
            quiz=stored_quiz,
            profile=stored_profile,
            result=resolution.result,
            previous_level=profile.cefr_level,
            recommended_level=resolution.recommended,
            pending_level=resolution.decision.pending_level,
            pending_count=resolution.decision.pending_count,
            evidence=graded.evidence,
            posterior=resolution.posterior,
            confidence=resolution.posterior.confidence,
            practice_mode=graded.practice_mode,
            blind_level=graded.blind_level,
        )

    def _grade_submission(
        self,
        article: Article,
        quiz: ReadingQuiz,
        normalized: tuple[int, ...],
        choice_correct: int,
        *,
        profile: Profile,
        self_report: str,
        texts: list[str],
        notes: tuple[str, ...],
    ) -> _GradedSubmission:
        """并行盲评 + 讲评，并把 AI 输出解析成结构化中间结果。"""
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
        stored_observations = observations_from_quiz(
            quiz,
            normalized,
            production_scores,
            production_scored=production_scored,
        )
        return _GradedSubmission(
            data=data,
            practice_mode=practice_mode,
            blind_level=blind_level,
            productions=productions,
            production_scores=production_scores,
            production_scored=production_scored,
            unscored_count=unscored_count,
            short_score=short_score,
            explanations=explanations,
            error_types=error_types,
            evidence=evidence,
            stored_observations=stored_observations,
        )

    def _resolve_level(
        self,
        profile: Profile,
        quiz: ReadingQuiz,
        graded: _GradedSubmission,
        choice_correct: int,
        *,
        self_report: str,
    ) -> _LevelResolution:
        """把本次证据、盲评、自评与历史回放合成后验，并决定等级。"""
        current_observations = list(graded.stored_observations)
        current_observations += list(
            observations_from_blind_level(
                graded.blind_level,
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

        if graded.practice_mode:
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

        result = assessment_result_from_data(graded.data, profile.cefr_level)
        result = replace(result, cefr=decision.applied_level)
        result = self._merge_profile_details(profile, result)
        score = choice_correct + sum(graded.production_scores)
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
                    practice_mode=graded.practice_mode,
                    unscored=graded.unscored_count,
                ),
            )

        timestamp = now_iso()
        reason = _level_reason(decision, posterior, practice_mode=graded.practice_mode)
        return _LevelResolution(
            posterior=posterior,
            decision=decision,
            recommended=recommended,
            result=result,
            score=score,
            timestamp=timestamp,
            reason=reason,
        )

    def _persist_submission(
        self,
        article: Article,
        quiz: ReadingQuiz,
        profile: Profile,
        normalized: tuple[int, ...],
        notes: tuple[str, ...],
        texts: list[str],
        graded: _GradedSubmission,
        resolution: _LevelResolution,
        choice_correct: int,
        *,
        self_report: str,
    ) -> tuple[Profile, ReadingQuiz]:
        """落库：画像（练习模式不写）、检测结果与等级证据。"""
        if graded.practice_mode:
            # 练习模式完全不写画像：等级、置信度、待定状态都保持原样。
            stored_profile = profile
        else:
            stored_profile = self._profiles.save(
                Profile(
                    cefr_level=resolution.result.cefr,
                    vocab_estimate=resolution.result.vocab_estimate,
                    strengths=resolution.result.strengths,
                    weaknesses=resolution.result.weaknesses,
                    comment=resolution.result.comment,
                    assessed_at=resolution.timestamp,
                    updated_at=resolution.timestamp,
                    pending_level=resolution.decision.pending_level,
                    pending_count=resolution.decision.pending_count,
                    level_confidence=resolution.posterior.confidence,
                    level_updated_reason=resolution.reason,
                )
            )
        stored_quiz = self._quizzes.save_result(
            quiz.id,
            answers=normalized,
            choice_notes=notes,
            explanations=graded.explanations,
            error_types=graded.error_types,
            short_answer_text=texts[0] if texts else "",
            short_answer_score=graded.short_score,
            self_report=self_report,
            score=resolution.score,
            recommended_level=resolution.recommended,
            previous_level=profile.cefr_level,
            result_level=stored_profile.cefr_level,
            pending_level=resolution.decision.pending_level,
            pending_count=resolution.decision.pending_count,
            feedback=resolution.result.comment,
            evidence_ratio=graded.evidence.ratio,
            productions=graded.productions,
            level_confidence=resolution.posterior.confidence,
            posterior=posterior_to_tuple(resolution.posterior),
            practice_mode=graded.practice_mode,
            completed_at=resolution.timestamp,
        )

        # 练习模式不写等级证据：它只用于当场练手，不能影响下一次正式检测的后验。
        if self._evidence is not None and not graded.practice_mode:
            self._evidence.insert(
                LevelEvidence(
                    source="quiz",
                    observed_at=resolution.timestamp,
                    evidence_date=article.article_date,
                    article_level=quiz.level,
                    quiz_id=quiz.id,
                    choice_correct=choice_correct,
                    choice_total=quiz.choice_total,
                    short_score=sum(graded.production_scores),
                    short_total=quiz.scored_production_total,
                    weighted_points=graded.evidence.points,
                    evidence_ratio=graded.evidence.ratio,
                    error_types=graded.error_types,
                    observations=graded.stored_observations,
                    posterior=posterior_to_tuple(resolution.posterior),
                    confidence=resolution.posterior.confidence,
                )
            )
        return stored_profile, stored_quiz

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
