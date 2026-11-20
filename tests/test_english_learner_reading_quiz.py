"""阅读检测测试：自适应两阶段、本地后验、盲评、产出题与练习模式。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from english_learner.models.entities import (
    Article,
    Profile,
    QuizQuestion,
    ReadingQuiz,
    now_iso,
)
from english_learner.services.ai_client import AIError
from english_learner.services.learner_memory import LearnerMemory
from english_learner.services.prompts import build_quiz_grading_messages
from english_learner.services.quiz_calibration import (
    difficulty_stats,
    question_outcomes,
    skill_stats,
)
from english_learner.services.reading_quiz_service import (
    UNKNOWN_ANSWER,
    EvidenceSummary,
    InvalidQuizError,
    QuizOutcome,
    ReadingQuizService,
    apply_level_policy,
    choose_boundary_level,
    decide_recommended_level,
    parse_question_review,
    parse_quiz_questions,
    parse_stage_one_questions,
    parse_stage_two_questions,
    score_quiz_evidence,
    shuffle_question_options,
)
from english_learner.storage.db import Database

TODAY = "2026-11-19"
ARTICLE_TITLE = "The Quiet Power of Small Habits"
ARTICLE_CONTENT = (
    "Small habits are easy to start and easy to repeat. "
    "They add up over time and quietly change our lives."
)


# ------------------------------------------------------------------ 桩模型
class RoutingCompleter:
    """按 prompt 内容路由到不同预设回复，支持队列与异常。"""

    def __init__(
        self,
        *,
        stage_one: object = None,
        stage_two: object = None,
        review: object = None,
        blind: object = None,
        grading: object = None,
    ) -> None:
        self.replies = {
            "stage_one": stage_one if stage_one is not None else stage_one_payload(),
            "stage_two": stage_two if stage_two is not None else stage_two_payload(),
            "review": review if review is not None else review_payload(),
            "blind": blind if blind is not None else blind_payload("B1"),
            "grading": grading if grading is not None else grading_payload(),
        }
        self.calls: list[str] = []
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task="") -> str:
        content = messages[-1]["content"]
        self.messages.append(messages)
        if "审核下面这组阅读题" in content:
            kind = "review"
        elif "不会看到学习者的当前等级" in content:
            kind = "blind"
        elif "边界探测题" in content:
            kind = "stage_two"
        elif "逐题用中文讲评" in content:
            kind = "grading"
        else:
            kind = "stage_one"
        self.calls.append(kind)
        reply = self.replies[kind]
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return str(reply)


def question_payload(
    count: int,
    *,
    difficulty: str,
    answer_index: int = 1,
    start: int = 1,
) -> list[dict[str, object]]:
    return [
        {
            "kind": "choice",
            "prompt": f"Question {index} about the passage?",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "answer_index": answer_index,
            "skill": "detail" if index % 2 else "inference",
            "difficulty": difficulty,
            "explanation": f"第 {index} 题的解析",
        }
        for index in range(start, start + count)
    ]


def stage_one_payload(count: int = 3, difficulty: str = "B1") -> str:
    return json.dumps(
        {"questions": question_payload(count, difficulty=difficulty)},
        ensure_ascii=False,
    )


def stage_two_payload(
    target: str = "B2",
    *,
    difficulty: str = "B2",
) -> str:
    questions = question_payload(2, difficulty=difficulty, start=4)
    questions.append(
        {
            "kind": "short",
            "prompt": (
                'Translate the following sentence into Chinese: "Small habits add up over time."'
            ),
            "skill": "translation",
            "difficulty": difficulty,
            "reference_answer": "小习惯会随着时间不断累积。",
            "explanation": "评分要点：add up 译为累积。",
        }
    )
    questions.append(
        {
            "kind": "short",
            "prompt": (
                "Translate the following sentence into Chinese: "
                '"They change our lives in ways we rarely notice."'
            ),
            "skill": "translation",
            "difficulty": target,
            "reference_answer": "它们以我们很少察觉的方式改变生活。",
            "explanation": "评分要点：in ways 表示方式；rarely 是很少。",
        }
    )
    return json.dumps({"questions": questions}, ensure_ascii=False)


def review_payload(invalid: tuple[int, ...] = ()) -> str:
    return json.dumps({"invalid_indices": list(invalid)}, ensure_ascii=False)


def blind_payload(cefr: str = "B1") -> str:
    return json.dumps({"cefr": cefr, "reason": "根据作答判断"}, ensure_ascii=False)


def grading_payload(
    *,
    production_scores: tuple[int, int] = (1, 1),
    error_types: list[str] | None = None,
    explanations: list[str] | None = None,
    **overrides,
) -> str:
    data = {
        "vocab_estimate": 4200,
        "strengths": ["细节理解"],
        "weaknesses": ["推断题"],
        "comment": "整体理解不错。",
        "explanations": explanations or [f"讲评第 {index} 题" for index in range(1, 8)],
        "error_types": error_types or ["none"] * 7,
        "production_scores": list(production_scores),
        "production_feedbacks": ["译文准确。", "关键词译对。"],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


@pytest.fixture
def profile(db: Database) -> Profile:
    return db.profiles.save(
        Profile(
            cefr_level="B1",
            vocab_estimate=3800,
            strengths=("阅读理解",),
            weaknesses=("学术词汇",),
            assessed_at="2026-11-01T10:00:00",
        )
    )


def make_article(db: Database, day: str = TODAY, level: str = "B1") -> Article:
    return db.articles.insert(
        Article(
            article_date=day,
            title=ARTICLE_TITLE,
            content=ARTICLE_CONTENT,
            level=level,
            topic="psychology",
            word_count=18,
            created_at=now_iso(),
        )
    )


def make_service(
    db: Database,
    fake: RoutingCompleter,
    *,
    shuffle_options: bool = False,
    memory_provider=None,
) -> ReadingQuizService:
    # 默认关闭选项打乱，测试里可以稳定地用固定下标作答；
    # 打乱逻辑有独立的纯函数测试与开启后的映射测试。
    return ReadingQuizService(
        fake,
        db.quizzes,
        db.profiles,
        db.evidence,
        db.reviews,
        shuffle_options=shuffle_options,
        memory_provider=memory_provider,
    )


def complete_quiz(
    service: ReadingQuizService,
    article: Article,
    *,
    level: str = "B1",
    stage_one_answers: tuple[int, int, int] = (1, 1, 1),
    all_answers: tuple[int, int, int, int, int] = (1, 1, 1, 1, 1),
    production_texts: tuple[str, str] = ("小习惯会不断累积。", "小习惯会改变生活。"),
):
    quiz = service.get_or_create(article, level)
    quiz = service.advance_stage(article, quiz, stage_one_answers)
    return service, quiz, all_answers, production_texts


def make_quiz(
    level: str = "B1",
    difficulties: tuple[str, ...] = ("A2", "B1", "B1", "B2", "C1"),
    answer_index: int = 1,
) -> ReadingQuiz:
    questions = [
        QuizQuestion(
            prompt=f"Question {index}?",
            options=("A", "B", "C", "D"),
            answer_index=answer_index,
            skill="detail",
            difficulty=difficulty,
        )
        for index, difficulty in enumerate(difficulties, start=1)
    ]
    questions.append(
        QuizQuestion(
            prompt="Summarize the passage.",
            skill="writing",
            difficulty=level,
            kind="short",
            reference_answer="Small habits add up.",
        )
    )
    return ReadingQuiz(
        id=1,
        article_id=1,
        article_date=TODAY,
        level=level,
        questions=tuple(questions),
        created_at=now_iso(),
    )


# ------------------------------------------------------------------ 解析
def test_parse_stage_one_requires_three_choices() -> None:
    questions = parse_stage_one_questions(stage_one_payload(), "B1")
    assert len(questions) == 3
    assert all(not question.is_short for question in questions)

    with pytest.raises(InvalidQuizError, match="3 道"):
        parse_stage_one_questions(stage_one_payload(count=2), "B1")


def test_parse_stage_two_requires_two_choices_and_two_translations() -> None:
    questions = parse_stage_two_questions(stage_two_payload("B2"), "B1", "B2")

    assert len(questions) == 4
    assert sum(1 for question in questions if question.is_short) == 2
    assert questions[0].difficulty == "B2"
    productions = [question for question in questions if question.is_short]
    assert {question.skill for question in productions} == {"translation"}
    assert all(question.is_translation for question in productions)

    only_choices = json.dumps(
        {"questions": question_payload(2, difficulty="B2")}, ensure_ascii=False
    )
    with pytest.raises(InvalidQuizError, match="英译中"):
        parse_stage_two_questions(only_choices, "B1", "B2")


def test_parse_stage_two_rejects_english_writing_production() -> None:
    """模型退回英文短答时判不合格，触发重试，避免用户重新练写作。"""
    payload = json.loads(stage_two_payload("B2"))
    payload["questions"][3] = {
        "kind": "short",
        "prompt": "In 1-2 sentences, summarize the main idea.",
        "skill": "writing",
        "difficulty": "B2",
        "reference_answer": "Small habits add up.",
        "explanation": "评分要点：内容 / 语法 / 词汇。",
    }

    with pytest.raises(InvalidQuizError, match="英译中"):
        parse_stage_two_questions(json.dumps(payload, ensure_ascii=False), "B1", "B2")


def test_parse_stage_two_forces_translation_skill() -> None:
    """模型漏标 skill 或写成 rewrite 时，只要题面要求译成中文就按英译中处理。"""
    payload = json.loads(stage_two_payload("B2"))
    payload["questions"][3]["kind"] = "rewrite"
    payload["questions"][3].pop("skill")

    questions = parse_stage_two_questions(json.dumps(payload, ensure_ascii=False), "B1", "B2")

    productions = [question for question in questions if question.is_short]
    assert len(productions) == 2
    assert all(question.skill == "translation" for question in productions)


def test_is_translation_requires_chinese_direction() -> None:
    chinese = QuizQuestion(
        prompt='Translate into Chinese: "Small habits add up."',
        skill="translation",
        kind="short",
    )
    assert chinese.is_translation is True

    # 旧缓存的“翻译成英文”不能被误判成英译中。
    english = QuizQuestion(
        prompt="Translate into English: 小习惯会不断累积。",
        skill="translation",
        kind="short",
    )
    assert english.is_translation is False

    # 不是 translation 技能的产出题（旧短答/改写）也不算。
    writing = QuizQuestion(
        prompt='Translate into Chinese: "Small habits add up."',
        skill="writing",
        kind="short",
    )
    assert writing.is_translation is False


def test_parse_question_review_filters_bad_indices() -> None:
    assert parse_question_review('{"invalid_indices": [0, 2, 99, "x"]}', 4) == (0, 2)
    assert parse_question_review('{"invalid": []}', 4) == ()
    with pytest.raises(InvalidQuizError):
        parse_question_review("不是 JSON", 4)


def test_parse_question_review_detects_answer_key_mismatch() -> None:
    """审核模型独立作答与标注答案不一致时，该题也应判为不合格。"""
    payload = json.dumps(
        {"answers": [0, 2, None, None], "invalid_indices": []},
        ensure_ascii=False,
    )
    assert parse_question_review(payload, 4, (1, 2, None, None)) == (0,)

    combined = json.dumps(
        {"answers": [1, 2, None, None], "invalid_indices": [3]},
        ensure_ascii=False,
    )
    assert parse_question_review(combined, 4, (1, 2, None, None)) == (3,)


def test_stage_difficulty_labels_are_forced_locally() -> None:
    """模型自报难度不能直接进 Rasch：第一轮强制当前等级，第二轮强制边界等级。"""
    first_round = parse_stage_one_questions(stage_one_payload(difficulty="C1"), "B1")
    assert all(question.difficulty == "B1" for question in first_round)

    second_round = parse_stage_two_questions(
        stage_two_payload("A2", difficulty="C1"),
        "B1",
        "A2",
    )
    choices = [question for question in second_round if not question.is_short]
    productions = [question for question in second_round if question.is_short]
    assert all(question.difficulty == "A2" for question in choices)
    # 产出题保留模型难度，但收敛到 A2~B1；C1 会被压到 B1。
    assert all(question.difficulty in {"A2", "B1"} for question in productions)
    assert productions[0].difficulty == "B1"


def test_parse_legacy_quiz_still_works() -> None:
    payload = json.dumps(
        {
            "questions": question_payload(5, difficulty="B1")
            + [
                {
                    "kind": "short",
                    "prompt": "Summarize it.",
                    "skill": "writing",
                    "difficulty": "B1",
                    "reference_answer": "A reference.",
                }
            ]
        },
        ensure_ascii=False,
    )
    questions = parse_quiz_questions(payload, "B1")
    assert len(questions) == 6
    assert questions[-1].is_short is True


# ------------------------------------------------------------------ 证据与策略
def test_evidence_rewards_hard_correct_and_punishes_easy_wrong() -> None:
    quiz = make_quiz(difficulties=("A2", "B1", "B2", "C1", "B1"))

    strong = score_quiz_evidence("B1", quiz, (1, 1, 1, 1, 1), short_answer_score=1)
    weak = score_quiz_evidence("B1", quiz, (0, 1, 1, 0, 1), short_answer_score=0)

    assert strong.ratio > 0
    assert strong.direction == "up"
    assert weak.ratio < strong.ratio
    assert weak.direction in {"keep", "down"}


def test_single_wrong_question_weights_by_difficulty() -> None:
    quiz = make_quiz(difficulties=("A2", "B1", "B2", "C1", "B1"))

    easy_wrong = score_quiz_evidence("B1", quiz, (0, 1, 1, 1, 1), short_answer_score=1)
    same_wrong = score_quiz_evidence("B1", quiz, (1, 0, 1, 1, 1), short_answer_score=1)
    hard_wrong = score_quiz_evidence("B1", quiz, (1, 1, 1, 0, 1), short_answer_score=1)

    assert easy_wrong.details[0] == pytest.approx(-1.5)
    assert same_wrong.details[1] == pytest.approx(-1.0)
    assert hard_wrong.details[3] == pytest.approx(0.0)
    assert easy_wrong.details[0] < same_wrong.details[1] < hard_wrong.details[3]


def test_decide_recommended_level_legacy_rules() -> None:
    up = score_quiz_evidence("B1", make_quiz(), (1, 1, 1, 1, 1), short_answer_score=1)
    down = score_quiz_evidence("B1", make_quiz(), (0, 0, 0, 0, 0), short_answer_score=0)
    neutral = score_quiz_evidence(
        "B1", make_quiz(difficulties=("B1", "B1", "B1", "B1", "B1")), (1, 0, 1, 0, 1)
    )

    assert decide_recommended_level("B1", "B1", up) == "B2"
    assert decide_recommended_level("B1", "C1", neutral) == "B2"
    assert decide_recommended_level("B1", "B1", down) == "A2"
    moderate_down = EvidenceSummary(ratio=-0.4, direction="down", points=-2.4, max_points=6)
    assert decide_recommended_level("B1", "C1", moderate_down) == "B1"


def test_apply_level_policy_requires_two_up_signals() -> None:
    first = apply_level_policy("B1", "B2", "", 0)
    assert first.applied_level == "B1"
    assert first.pending_level == "B2"
    assert first.pending_count == 1
    assert first.waiting is True

    second = apply_level_policy("B1", "B2", first.pending_level, first.pending_count)
    assert second.applied_level == "B2"
    assert second.pending_level == ""
    assert second.changed is True

    downgrade = apply_level_policy("B1", "A2", "B2", 1)
    assert downgrade.applied_level == "A2"
    assert downgrade.changed is True


def test_shuffle_options_remaps_answer_index() -> None:
    class ReverseRng:
        @staticmethod
        def shuffle(items: list[int]) -> None:
            items.reverse()

    question = QuizQuestion(
        prompt="Q?",
        options=("A", "B", "C", "D"),
        answer_index=1,
    )
    shuffled = shuffle_question_options(question, ReverseRng())

    assert shuffled.options == ("D", "C", "B", "A")
    assert shuffled.answer_index == 2
    assert shuffled.options[shuffled.answer_index] == "B"

    production = QuizQuestion(prompt="Write.", kind="short")
    assert shuffle_question_options(production) == production


def test_service_can_shuffle_options_without_changing_correct_text(
    db: Database,
) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake, shuffle_options=True)

    quiz = service.get_or_create(article, "B1")

    for question in quiz.choice_questions:
        assert question.options[question.answer_index] == "Option B"
        assert set(question.options) == {"Option A", "Option B", "Option C", "Option D"}


def test_boundary_level_uses_stage_one_accuracy() -> None:
    assert choose_boundary_level("B1", 3, 3) == "B2"
    assert choose_boundary_level("B1", 2, 3) == "B2"
    assert choose_boundary_level("B1", 1, 3) == "A2"
    assert choose_boundary_level("B1", 0, 3) == "A2"
    assert choose_boundary_level("C1", 3, 3) == "C1"
    assert choose_boundary_level("A2", 0, 3) == "A2"


# ------------------------------------------------------------------ 出题 / 缓存
def test_get_or_create_generates_stage_one_and_reviews(db: Database) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)

    first = service.get_or_create(article, "B1")
    second = service.get_or_create(article, "B1")

    assert fake.calls == ["stage_one", "review"]
    assert first.id is not None and second.id == first.id
    assert first.stage == 1
    assert first.choice_total == 3
    assert first.short_total == 0
    assert first.completed is False


def test_get_or_create_retries_when_stage_one_invalid(db: Database) -> None:
    article = make_article(db)
    fake = RoutingCompleter(stage_one=["垃圾", stage_one_payload()])
    service = make_service(db, fake)

    quiz = service.get_or_create(article, "B1")

    assert quiz.choice_total == 3
    assert fake.calls == ["stage_one", "stage_one", "review"]


def test_question_review_can_drop_and_regenerate(db: Database) -> None:
    article = make_article(db)
    # 第一次审核把 3 道题全判无效，第二次审核通过。
    fake = RoutingCompleter(
        stage_one=[stage_one_payload(), stage_one_payload()],
        review=[review_payload((0, 1, 2)), review_payload()],
    )
    service = make_service(db, fake)

    quiz = service.get_or_create(article, "B1")

    assert quiz.choice_total == 3
    assert fake.calls.count("stage_one") == 2


def test_get_or_create_requires_saved_article(db: Database) -> None:
    article = Article(
        article_date=TODAY,
        title="Unsaved",
        content=ARTICLE_CONTENT,
        level="B1",
        word_count=18,
    )
    with pytest.raises(AIError, match="保存"):
        make_service(db, RoutingCompleter()).get_or_create(article, "B1")


def test_advance_stage_appends_boundary_questions(db: Database) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)

    quiz = service.get_or_create(article, "B1")
    advanced = service.advance_stage(article, quiz, (1, 1, 1))

    assert advanced.stage == 2
    assert advanced.choice_total == 5
    assert advanced.short_total == 2
    assert advanced.answers == (1, 1, 1)
    assert "stage_two" in fake.calls
    assert advanced.questions[-2].skill == "translation"
    assert advanced.questions[-1].skill == "translation"
    assert all(question.is_translation for question in advanced.production_questions)

    again = service.advance_stage(article, advanced, (1, 1, 1))
    assert again.questions == advanced.questions


def test_stage_two_missing_difficulty_falls_back_to_boundary_level() -> None:
    payload = json.loads(stage_two_payload("B2"))
    for entry in payload["questions"]:
        entry.pop("difficulty", None)

    questions = parse_stage_two_questions(json.dumps(payload, ensure_ascii=False), "B1", "B2")

    assert all(question.difficulty == "B2" for question in questions)


def test_advance_stage_saves_answers_before_generation(db: Database) -> None:
    article = make_article(db)
    # 第二轮两次都返回垃圾，generate_stage_two 最终抛 AIError。
    fake = RoutingCompleter(stage_two=["垃圾", "垃圾"])
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")

    with pytest.raises(AIError, match="第二轮"):
        service.advance_stage(article, quiz, (1, 0, 1))

    stored = db.quizzes.get_by_article(article.id)
    assert stored is not None
    assert stored.stage == 1
    assert stored.answers == (1, 0, 1)


def test_advance_stage_requires_all_answers(db: Database) -> None:
    article = make_article(db)
    service = make_service(db, RoutingCompleter())
    quiz = service.get_or_create(article, "B1")

    with pytest.raises(AIError, match="选择题"):
        service.advance_stage(article, quiz, (1, -1, 1))


def test_stage_two_targets_lower_level_after_poor_stage_one(db: Database) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)

    quiz = service.get_or_create(article, "B1")
    service.advance_stage(article, quiz, (0, 0, 1))

    stage_two_prompt = next(
        messages[-1]["content"]
        for messages in fake.messages
        if "边界探测题" in messages[-1]["content"]
    )
    assert "A2" in stage_two_prompt


def test_stage_two_prompt_asks_for_chinese_translation(db: Database) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)

    quiz = service.get_or_create(article, "B1")
    service.advance_stage(article, quiz, (1, 1, 1))

    prompt = next(
        messages[-1]["content"]
        for messages in fake.messages
        if "边界探测题" in messages[-1]["content"]
    )
    assert "英译中" in prompt
    assert "翻译成中文" in prompt
    assert "不要要求学习者写英文" in prompt
    assert "reference_answer 必须给中文参考翻译" in prompt


def test_grading_prompt_uses_comprehension_rubric_for_translation(
    db: Database, profile: Profile
) -> None:
    article = make_article(db)
    quiz = make_quiz()
    translation = QuizQuestion(
        prompt='Translate the following sentence into Chinese: "Small habits add up."',
        skill="translation",
        difficulty="B1",
        kind="short",
        reference_answer="小习惯会不断累积。",
    )
    quiz = replace(quiz, questions=quiz.questions[:-1] + (translation,))

    messages = build_quiz_grading_messages(
        profile,
        article,
        quiz,
        (1, 1, 1, 1, 1),
        5,
        production_texts=("小习惯会不断累积。",),
    )
    prompt = messages[-1]["content"]

    assert "英译中" in prompt
    assert "理解准确 / 中文通顺" in prompt
    assert "不因中文文采" in prompt
    assert "参考翻译：小习惯会不断累积。" in prompt
    assert "学习者译文：小习惯会不断累积。" in prompt


# ------------------------------------------------------------------ 提交
def test_submit_records_posterior_evidence_and_productions(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", "Small habits change lives."),
        self_report="ok",
    )

    assert isinstance(outcome, QuizOutcome)
    assert outcome.score == 7
    assert outcome.total == 7
    assert outcome.quiz.production_score == 2
    assert outcome.quiz.stage == 2
    assert outcome.quiz.completed is True
    assert outcome.quiz.level_confidence > 0
    assert len(outcome.quiz.posterior) == 4
    assert outcome.posterior is not None
    assert outcome.confidence >= 0.6
    # 第一次向上证据只记录待定
    assert outcome.previous_level == "B1"
    assert outcome.profile.cefr_level == "B1"
    assert outcome.recommended_level == "B2"
    assert outcome.pending_level == "B2"
    assert outcome.pending_count == 1

    stored_profile = db.profiles.get()
    assert stored_profile is not None
    assert stored_profile.level_confidence > 0
    assert "后验" in stored_profile.level_updated_reason
    assert db.evidence.count() == 1
    evidence = db.evidence.list_recent(1)[0]
    assert evidence.source == "quiz"
    assert evidence.quiz_id == quiz.id
    assert evidence.confidence > 0
    # 盲评 B1 与当前等级相同，不产生观测：5 选择 + 2 产出。
    assert len(evidence.observations) == 7
    assert evidence.evidence_date == TODAY

    stored_quiz = db.quizzes.get_by_article(article.id)
    assert stored_quiz is not None
    assert stored_quiz.productions[0].text == "Habits add up."
    assert stored_quiz.productions[1].score == 1


def test_two_up_signals_promote_level(db: Database, profile: Profile) -> None:
    fake = RoutingCompleter()
    service = make_service(db, fake)
    first_article = make_article(db, "2026-11-19")
    second_article = make_article(db, "2026-11-20")

    _, first_quiz, answers, texts = complete_quiz(service, first_article)
    first = service.submit(first_article, first_quiz, answers, profile, production_texts=texts)
    assert first.profile.cefr_level == "B1"
    assert first.pending_count == 1

    _, second_quiz, answers, texts = complete_quiz(service, second_article)
    second = service.submit(
        second_article, second_quiz, answers, first.profile, production_texts=texts
    )

    assert second.profile.cefr_level == "B2"
    assert second.level_changed is True
    assert second.pending_level == ""
    assert second.quiz.result_level == "B2"

    stored = db.profiles.get()
    assert stored is not None and stored.cefr_level == "B2"


def test_low_score_downgrades_immediately(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter(
        stage_two=stage_two_payload("A2", difficulty="A2"),
        grading=grading_payload(production_scores=(0, 0)),
    )
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (0, 0, 0))

    outcome = service.submit(
        article,
        quiz,
        (0, 0, 0, 0, 0),
        profile,
        production_texts=("", ""),
    )

    assert outcome.score == 0
    assert outcome.recommended_level == "A2"
    assert outcome.profile.cefr_level == "A2"
    assert outcome.level_changed is True
    assert outcome.pending_level == ""


def test_same_day_second_quiz_is_practice_mode(db: Database, profile: Profile) -> None:
    fake = RoutingCompleter()
    service = make_service(db, fake)

    first_article = make_article(db, "2026-11-19")
    _, first_quiz, answers, texts = complete_quiz(service, first_article)
    first = service.submit(first_article, first_quiz, answers, profile, production_texts=texts)
    assert first.pending_level == "B2"

    # 同一天换一篇再来一次：只练手，不累计证据、不改等级。
    second_article = db.articles.replace_by_date(
        Article(
            article_date="2026-11-19",
            title="Another Article",
            content=ARTICLE_CONTENT,
            level="B1",
            word_count=18,
            created_at=now_iso(),
        )
    )
    _, second_quiz, answers, texts = complete_quiz(service, second_article)
    second = service.submit(
        second_article,
        second_quiz,
        answers,
        first.profile,
        production_texts=texts,
    )

    assert second.practice_mode is True
    assert second.profile.cefr_level == "B1"
    assert second.quiz.practice_mode is True
    assert second.profile.pending_level == "B2"
    assert second.profile.pending_count == 1
    assert second.quiz.result_level == "B1"
    # 练习模式不写等级证据，后续正式检测不会被它影响
    assert db.evidence.count() == 1


def test_stage_one_cannot_be_submitted_directly(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")

    with pytest.raises(AIError, match="第一轮"):
        service.submit(article, quiz, (1, 1, 1), profile, production_texts=("a", "b"))


def test_empty_production_forces_zero(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter(grading=grading_payload(production_scores=(1, 1)))
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", ""),
    )

    assert outcome.quiz.productions[0].score == 1
    assert outcome.quiz.productions[1].score == 0
    assert outcome.score == 6


def test_missing_production_scores_are_retried_then_marked_unscored(
    db: Database, profile: Profile
) -> None:
    """AI 漏给产出题分数时先按原因重试；仍缺失则不当作答错。"""
    payload = json.loads(grading_payload())
    payload.pop("production_scores")
    payload.pop("production_feedbacks", None)
    payload.pop("comment", None)
    missing = json.dumps(payload, ensure_ascii=False)
    fake = RoutingCompleter(grading=[missing, missing])
    service = make_service(db, fake)
    article = make_article(db)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", "Small habits change lives."),
    )

    assert fake.calls.count("grading") == 2  # 第一次缺失，按原因重试一次
    assert outcome.quiz.unscored_production_count == 2
    assert all(not item.scored for item in outcome.quiz.productions)
    assert outcome.quiz.production_score == 0
    assert outcome.score == 5  # 只算 5 道选择题

    evidence = db.evidence.list_recent(1)[0]
    assert len(evidence.observations) == 5
    assert all(item.source != "production" for item in evidence.observations)
    assert "未给分" in outcome.result.comment


def test_weak_skills_are_computed_from_recent_quizzes(db: Database) -> None:
    article = make_article(db)
    questions = tuple(
        QuizQuestion(
            prompt=f"Inference {index}?",
            options=("A", "B", "C", "D"),
            answer_index=1,
            skill="inference",
            difficulty="B1",
        )
        for index in range(2)
    )
    db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=TODAY,
            level="B1",
            questions=questions,
            answers=(0, 0),
            completed_at=now_iso(),
        )
    )
    service = make_service(db, RoutingCompleter())

    assert service._weak_skills() == ("inference",)


def test_weak_skills_ignore_production_only_skills(db: Database) -> None:
    """英译中/写作是产出题技能，选择题无法覆盖，不能喂进选择题的出题提示。"""
    article = make_article(db)
    questions = (
        QuizQuestion(
            prompt="Inference 1?",
            options=("A", "B", "C", "D"),
            answer_index=1,
            skill="inference",
            difficulty="B1",
        ),
        QuizQuestion(
            prompt="Inference 2?",
            options=("A", "B", "C", "D"),
            answer_index=1,
            skill="inference",
            difficulty="B1",
        ),
        QuizQuestion(
            prompt='Translate into Chinese: "Small habits add up."',
            skill="translation",
            difficulty="B1",
            kind="short",
            reference_answer="小习惯会不断累积。",
        ),
    )
    db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=TODAY,
            level="B1",
            questions=questions,
            answers=(0, 0),
            completed_at=now_iso(),
        )
    )
    service = make_service(db, RoutingCompleter())

    assert service._weak_skills() == ("inference",)


def test_weak_skills_merge_learner_memory_error_types(db: Database) -> None:
    """检测样本很少时，学习者记忆里的近期错因也要并入出题薄弱技能。"""
    memory = LearnerMemory(
        level="B1",
        error_types=("inference", "careless", "main_idea"),
    )
    service = make_service(db, RoutingCompleter(), memory_provider=lambda: memory)

    # careless 不是选择题技能，被过滤；其余按记忆顺序并入。
    assert service._weak_skills() == ("inference", "main_idea")


def test_error_types_are_aligned_and_correct_is_none(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter(
        grading=grading_payload(
            error_types=[
                "vocabulary",
                "vocabulary",
                "inference",
                "detail",
                "careless",
                "translation",
                "translation",
            ]
        )
    )
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (1, 0, 1, 1, 1),
        profile,
        production_texts=("小习惯会不断累积。", "小习惯会改变生活。"),
    )

    assert len(outcome.quiz.error_types) == 7
    assert outcome.quiz.error_types[0] == "none"
    assert outcome.quiz.error_types[1] == "vocabulary"
    assert outcome.quiz.error_types[2] == "none"
    assert outcome.quiz.error_types[-1] == "translation"


def test_bad_grading_json_keeps_profile_and_allows_retry(db: Database, profile: Profile) -> None:
    article = make_article(db)
    # 第一次提交会先自动按原因重试一次；两次都坏才失败，第三次（用户手动重试）成功。
    fake = RoutingCompleter(grading=["不是 JSON", "还是 JSON", grading_payload()])
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    with pytest.raises(AIError, match="讲评"):
        service.submit(
            article,
            quiz,
            (1, 1, 1, 1, 1),
            profile,
            production_texts=("Habits add up.", "Small habits change lives."),
        )

    stored = db.profiles.get()
    assert stored is not None and stored.cefr_level == "B1"
    stored_quiz = db.quizzes.get_by_article(article.id)
    assert stored_quiz is not None and stored_quiz.completed is False

    retried = service.submit(
        article,
        stored_quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", "Small habits change lives."),
    )
    assert retried.quiz.completed is True


def test_blind_suggestion_affects_current_but_not_stored_history(
    db: Database, profile: Profile
) -> None:
    article = make_article(db)
    fake = RoutingCompleter(blind=blind_payload("B2"))
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", "Small habits change lives."),
    )

    assert outcome.blind_level == "B2"
    evidence = db.evidence.list_recent(1)[0]
    # 盲评只影响本次后验，不写入证据表，避免通过历史回放累积权重。
    assert all(item.source != "blind" for item in evidence.observations)
    assert len(evidence.observations) == 7  # 5 选择 + 2 产出


def test_blind_failure_does_not_block_local_posterior(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter(blind=AIError("网关超时", "稍后重试"))
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", "Small habits change lives."),
    )

    assert outcome.blind_level == ""
    assert outcome.quiz.completed is True
    assert outcome.posterior is not None


def test_completed_quiz_cannot_be_resubmitted(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))
    completed = service.submit(
        article,
        quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", "Small habits change lives."),
    ).quiz

    with pytest.raises(AIError, match="已经完成"):
        service.submit(
            article,
            completed,
            (1, 1, 1, 1, 1),
            profile,
            production_texts=("Habits add up.", "Small habits change lives."),
        )


def test_missing_fields_keep_old_profile_details(db: Database, profile: Profile) -> None:
    article = make_article(db)
    fake = RoutingCompleter(grading=json.dumps({"comment": "保持"}))
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (1, 1, 1, 1, 1),
        profile,
        production_texts=("Habits add up.", "Small habits change lives."),
    )

    assert outcome.profile.vocab_estimate == 3800
    assert outcome.profile.strengths == ("阅读理解",)
    assert outcome.profile.weaknesses == ("学术词汇",)
    assert len(outcome.quiz.explanations) == 7


def test_legacy_single_stage_quiz_can_still_submit(db: Database, profile: Profile) -> None:
    """升级前的“5 选择 + 1 短答”旧题（stage 默认 2）仍可正常提交。"""
    article = make_article(db)
    legacy = db.quizzes.insert(replace(make_quiz(level="B1"), id=None, article_id=article.id))
    assert legacy.stage == 2
    assert legacy.choice_total == 5
    assert legacy.short_total == 1

    fake = RoutingCompleter(
        grading=json.dumps(
            {
                "vocab_estimate": 4000,
                "strengths": ["细节理解"],
                "weaknesses": ["推断题"],
                "comment": "",
                "explanations": ["讲评"] * 6,
                "error_types": ["none"] * 6,
                "production_scores": [1],
                "production_feedbacks": ["短答达意。"],
            },
            ensure_ascii=False,
        )
    )
    service = make_service(db, fake)

    outcome = service.submit(
        article,
        legacy,
        (1, 1, 1, 1, 1),
        profile,
        short_answer_text="Habits add up.",
    )

    assert outcome.quiz.completed is True
    assert outcome.quiz.total == 6
    assert outcome.quiz.short_total == 1
    assert outcome.quiz.production_score == 1
    assert outcome.quiz.productions[0].text == "Habits add up."


def test_history_prompt_contains_previous_quiz(db: Database, profile: Profile) -> None:
    fake = RoutingCompleter()
    service = make_service(db, fake)
    first_article = make_article(db, "2026-11-18")
    _, first_quiz, answers, texts = complete_quiz(service, first_article)
    service.submit(first_article, first_quiz, answers, profile, production_texts=texts)

    second_article = make_article(db, "2026-11-19")
    _, second_quiz, answers, texts = complete_quiz(service, second_article)
    service.submit(
        second_article,
        second_quiz,
        answers,
        db.profiles.get(),
        production_texts=texts,
    )

    grading_prompts = [
        messages[-1]["content"]
        for messages in fake.messages
        if "逐题用中文讲评" in messages[-1]["content"]
    ]
    assert grading_prompts
    assert "最近检测历史" in grading_prompts[-1]
    assert ARTICLE_TITLE in grading_prompts[-1]


# ------------------------------------------------------------------ 本地统计
def test_quiz_calibration_groups_by_difficulty_and_skill() -> None:
    completed = ReadingQuiz(
        article_id=1,
        article_date=TODAY,
        level="B1",
        questions=make_quiz(difficulties=("A2", "B1", "B2", "C1", "B1")).questions,
        answers=(1, 0, 1, 0, 1),
        completed_at=now_iso(),
    )

    outcomes = question_outcomes(completed)
    assert len(outcomes) == 6
    assert outcomes[0].correct is True
    assert outcomes[1].correct is False

    by_difficulty = {stat.level: stat for stat in difficulty_stats([completed])}
    assert by_difficulty["A2"].total == 1
    assert by_difficulty["C1"].correct == 0
    assert sum(stat.total for stat in by_difficulty.values()) == 6

    by_skill = {stat.skill: stat for stat in skill_stats([completed])}
    assert by_skill["writing"].total == 1


# ------------------------------------------------- 选择题「不知道 + 补充说明」
def test_advance_stage_accepts_unknown_answer_and_saves_note(db: Database) -> None:
    article = make_article(db)
    service = make_service(db, RoutingCompleter())
    quiz = service.get_or_create(article, "B1")

    advanced = service.advance_stage(
        article,
        quiz,
        (UNKNOWN_ANSWER, 1, 0),
        choice_notes=("这个单词不认识", "不该保留", ""),
    )

    # -2 是有效作答，按答错计分；说明只保留在选「不知道」的题上。
    assert advanced.answers == (UNKNOWN_ANSWER, 1, 0)
    assert advanced.choice_notes[:3] == ("这个单词不认识", "", "")

    stored = db.quizzes.get_by_article(article.id)
    assert stored is not None
    assert stored.choice_notes[:3] == ("这个单词不认识", "", "")


def test_submit_accepts_unknown_answer_and_passes_note_to_ai(
    db: Database, profile: Profile
) -> None:
    article = make_article(db)
    fake = RoutingCompleter()
    service = make_service(db, fake)
    quiz = service.get_or_create(article, "B1")
    quiz = service.advance_stage(article, quiz, (1, 1, 1))

    outcome = service.submit(
        article,
        quiz,
        (UNKNOWN_ANSWER, 1, 1, 1, 1),
        profile,
        production_texts=("小习惯会不断累积。", "小习惯会改变生活。"),
        choice_notes=("这个单词不认识", "不该保留", "", "", ""),
    )

    assert outcome.quiz.answers[:5] == (UNKNOWN_ANSWER, 1, 1, 1, 1)
    assert outcome.quiz.choice_notes[:5] == ("这个单词不认识", "", "", "", "")
    stored = db.quizzes.get_by_article(article.id)
    assert stored is not None and stored.choice_notes[0] == "这个单词不认识"

    grading = next(
        messages[-1]["content"]
        for messages in fake.messages
        if "逐题用中文讲评" in messages[-1]["content"]
    )
    assert "不知道（主动表示不会）" in grading
    assert "补充说明：这个单词不认识" in grading

    blind = next(
        messages[-1]["content"]
        for messages in fake.messages
        if "不会看到学习者的当前等级" in messages[-1]["content"]
    )
    assert "补充说明：这个单词不认识" in blind
