"""拼写回忆判分与题面生成的纯函数测试（不依赖 PySide6 / 网络）。"""

from __future__ import annotations

from english_learner.models.entities import Word
from english_learner.services.recall import (
    MASK,
    STATUS_CORRECT,
    STATUS_NEAR,
    STATUS_WRONG,
    accepted_forms,
    build_spelling_prompt,
    check_spelling,
    edit_distance,
    mask_occurrences,
    normalize_spelling,
)
from english_learner.services.srs import RATING_FORGOT, RATING_KNOWN, RATING_VAGUE


def make_word(**overrides) -> Word:
    values = {
        "lemma": "preserve",
        "display_form": "preserve",
        "phonetic": "/prɪˈzɜːv/",
        "pos": "v.",
        "meaning_cn": "保护；保存",
        "meaning_en": "to keep something safe",
        "example_en": "We should preserve the forest.",
        "example_cn": "我们应该保护森林。",
        "context_sentence": "The law helps preserve the old town.",
        "in_vocabulary": True,
        "status": "new",
        "ease": 2.5,
        "interval_days": 0,
        "repetitions": 0,
        "due_date": "2026-11-19",
        "created_at": "2026-11-19T08:00:00",
        "updated_at": "2026-11-19T08:00:00",
    }
    values.update(overrides)
    return Word(**values)


# ------------------------------------------------------------------ 归一化
def test_normalize_ignores_case_space_and_surrounding_punctuation() -> None:
    assert normalize_spelling("  PreServe. ") == "preserve"
    assert normalize_spelling("look   UP") == "look up"
    assert normalize_spelling("don’t") == "don't"


def test_accepted_forms_include_lemma_and_display_form() -> None:
    word = make_word(lemma="go", display_form="went")
    assert accepted_forms(word) == ("go", "went")

    # 大小写不同但归一化后相同的词形只保留一个。
    assert accepted_forms(make_word(lemma="Go", display_form="go")) == ("Go",)


# ------------------------------------------------------------------ 判分
def test_correct_answer_accepts_lemma_and_display_form() -> None:
    word = make_word(lemma="go", display_form="went", meaning_cn="去")

    for answer in ("go", "went", " GO ", "Went."):
        result = check_spelling(word, answer)
        assert result.status == STATUS_CORRECT
        assert result.suggested_rating == RATING_KNOWN
        assert result.distance == 0
        assert result.expected == "went"
        assert result.lemma == "go"


def test_near_miss_is_one_edit_away() -> None:
    result = check_spelling(make_word(), "preserv")

    assert result.status == STATUS_NEAR
    assert result.suggested_rating == RATING_VAGUE
    assert result.distance == 1


def test_short_words_do_not_get_near_miss() -> None:
    """1~2 个字母的词容错 1 个字母太宽松，只按对错处理。"""
    word = make_word(lemma="go", display_form="go", meaning_cn="去")

    assert check_spelling(word, "to").status == STATUS_WRONG
    assert check_spelling(word, "go").status == STATUS_CORRECT


def test_wrong_and_empty_answers_suggest_forgot() -> None:
    word = make_word()

    assert check_spelling(word, "protect").status == STATUS_WRONG
    assert check_spelling(word, "protect").suggested_rating == RATING_FORGOT

    empty = check_spelling(word, "   ")
    assert empty.status == STATUS_WRONG
    assert empty.suggested_rating == RATING_FORGOT


def test_edit_distance_basic() -> None:
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("alpha", "alpha") == 0
    assert edit_distance("", "abc") == 3


# ------------------------------------------------------------------ 题面
def test_mask_occurrences_is_case_insensitive_and_respects_word_boundary() -> None:
    masked, count = mask_occurrences("Preserve it. preserve it.", ("preserve",))
    assert count == 2
    assert masked == f"{MASK} it. {MASK} it."

    # go 不能把 good / going 也挖掉。
    masked, count = mask_occurrences("We go to a good game.", ("go",))
    assert count == 1
    assert masked == f"We {MASK} to a good game."


def test_build_prompt_masks_example_and_meaning() -> None:
    prompt = build_spelling_prompt(make_word())

    assert prompt is not None
    assert prompt.meaning == "保护；保存"
    assert prompt.masked_sentence == f"We should {MASK} the forest."
    assert prompt.pos == "v."


def test_build_prompt_masks_word_inside_meaning() -> None:
    """释义里如果带了英文原词，也要挖掉，避免直接泄题。"""
    prompt = build_spelling_prompt(make_word(meaning_cn="preserve 的意思"))

    assert prompt is not None
    assert "preserve" not in prompt.meaning
    assert MASK in prompt.meaning


def test_build_prompt_falls_back_to_context_sentence() -> None:
    prompt = build_spelling_prompt(
        make_word(example_en="Nothing to see here.", context_sentence="We preserve it.")
    )

    assert prompt is not None
    assert prompt.masked_sentence == f"We {MASK} it."


def test_build_prompt_hides_sentence_that_cannot_be_masked() -> None:
    prompt = build_spelling_prompt(
        make_word(example_en="Nothing to see here.", context_sentence="")
    )

    assert prompt is not None
    assert prompt.masked_sentence == ""


def test_build_prompt_uses_masked_meaning_en_when_cn_is_empty() -> None:
    prompt = build_spelling_prompt(make_word(meaning_cn="", meaning_en="to preserve something"))

    assert prompt is not None
    assert prompt.meaning == f"to {MASK} something"


def test_build_prompt_returns_none_for_phrase_or_missing_meaning() -> None:
    phrase = make_word(lemma="look up", display_form="look up", meaning_cn="查阅")
    assert build_spelling_prompt(phrase) is None

    no_meaning = make_word(meaning_cn="", meaning_en="")
    assert build_spelling_prompt(no_meaning) is None
