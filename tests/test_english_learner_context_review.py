"""M7.4：语境识别题生成与复习卡编排规则。"""

from __future__ import annotations

from english_learner.models.entities import Word, now_iso, today_str
from english_learner.services.context_review import build_context_question
from english_learner.services.review_plan import (
    CARD_CONTEXT,
    CARD_RECOGNITION,
    build_session_plan,
)


def make_word(
    word_id: int,
    lemma: str,
    *,
    pos: str = "n.",
    band: str = "B1",
    context: str = "",
    example: str = "",
    repetitions: int = 0,
    meaning_cn: str = "",
) -> Word:
    return Word(
        id=word_id,
        lemma=lemma,
        display_form=lemma,
        pos=pos,
        cefr_band=band,
        meaning_cn=meaning_cn or f"{lemma} 的意思",
        example_en=example,
        context_sentence=context,
        in_vocabulary=True,
        repetitions=repetitions,
        due_date=today_str(),
        created_at=now_iso(),
        updated_at=now_iso(),
    )


def test_build_context_question_masks_sentence_and_builds_options() -> None:
    word = make_word(1, "cat", context="The cat sat on the warm mat.")
    pool = [
        word,
        make_word(2, "dog"),
        make_word(3, "fox"),
        make_word(4, "owl"),
    ]

    question = build_context_question(word, pool, seed=7)

    assert question is not None
    assert "____" in question.masked_sentence
    assert "cat" not in question.masked_sentence.lower()
    assert len(question.options) == 4
    assert question.options[question.answer_index] == "cat"
    assert question.word_id == 1
    assert question.answer == "cat"


def test_build_context_question_is_deterministic_for_same_seed() -> None:
    word = make_word(1, "cat", context="The cat sat on the warm mat.")
    pool = [word] + [make_word(index, name) for index, name in enumerate(("dog", "fox", "owl"), 2)]

    first = build_context_question(word, pool, seed=42)
    second = build_context_question(word, pool, seed=42)

    assert first is not None and second is not None
    assert first.options == second.options
    assert first.answer_index == second.answer_index


def test_build_context_question_excludes_words_already_in_sentence() -> None:
    word = make_word(1, "cat", context="The cat sat on the warm mat.")
    pool = [
        word,
        make_word(2, "mat"),
        make_word(3, "dog"),
        make_word(4, "fox"),
        make_word(5, "owl"),
    ]

    question = build_context_question(word, pool, seed=1)

    assert question is not None
    assert "mat" not in question.options


def test_build_context_question_returns_none_without_enough_distractors() -> None:
    word = make_word(1, "cat", context="The cat sat on the warm mat.")
    pool = [word, make_word(2, "dog"), make_word(3, "fox")]

    assert build_context_question(word, pool, seed=1) is None


def test_build_context_question_falls_back_to_example_sentence() -> None:
    word = make_word(1, "cat", example="A cat is sleeping.")
    pool = [word] + [make_word(index, name) for index, name in enumerate(("dog", "fox", "owl"), 2)]

    question = build_context_question(word, pool, seed=3)

    assert question is not None
    assert "sleeping" in question.masked_sentence


def test_build_context_question_skips_phrases_and_missing_sentences() -> None:
    phrase = make_word(1, "look up", context="Look up the word in a dictionary.")
    pool = [phrase] + [
        make_word(index, name) for index, name in enumerate(("dog", "fox", "owl"), 2)
    ]
    assert build_context_question(phrase, pool, seed=1) is None

    no_sentence = make_word(1, "cat")
    pool = [no_sentence] + [
        make_word(index, name) for index, name in enumerate(("dog", "fox", "owl"), 2)
    ]
    assert build_context_question(no_sentence, pool, seed=1) is None


def test_session_plan_uses_context_for_new_words() -> None:
    words = [make_word(1, "cat", context="The cat sat on the warm mat.")]
    pool = words + [make_word(index, name) for index, name in enumerate(("dog", "fox", "owl"), 2)]

    cards = build_session_plan(words, context_pool=pool, context_seed=5)

    assert cards[0].mode == CARD_CONTEXT
    assert cards[0].context is not None


def test_session_plan_keeps_old_behavior_without_pool() -> None:
    words = [make_word(1, "cat", context="The cat sat on the warm mat.")]

    cards = build_session_plan(words, context_pool=None)

    assert cards[0].mode == CARD_RECOGNITION
    assert cards[0].context is None


def test_session_plan_does_not_use_context_after_first_successful_review() -> None:
    words = [make_word(1, "cat", context="The cat sat on the warm mat.", repetitions=1)]
    pool = words + [make_word(index, name) for index, name in enumerate(("dog", "fox", "owl"), 2)]

    cards = build_session_plan(words, context_pool=pool, context_seed=5)

    assert cards[0].mode != CARD_CONTEXT


def test_session_plan_allows_context_when_spelling_is_disabled() -> None:
    words = [make_word(1, "cat", context="The cat sat on the warm mat.")]
    pool = words + [make_word(index, name) for index, name in enumerate(("dog", "fox", "owl"), 2)]

    cards = build_session_plan(words, spelling_enabled=False, context_pool=pool, context_seed=5)

    assert cards[0].mode == CARD_CONTEXT
