"""复习卡片自动编排（review_plan）单测：纯函数，不依赖数据库与界面。"""

from __future__ import annotations

from english_learner.models.entities import Word, today_str
from english_learner.services.review_plan import (
    CARD_DUAL,
    CARD_RECOGNITION,
    CARD_SPELLING,
    MAX_CONSECUTIVE_SAME_FRONT,
    build_session_plan,
    plan_card,
)
from english_learner.services.srs import (
    RATING_KNOWN,
    RATING_VAGUE,
    VERDICT_CORRECT,
    VERDICT_NEAR,
    VERDICT_WRONG,
)


def make_word(lemma: str = "alpha", **overrides) -> Word:
    values = {
        "lemma": lemma,
        "display_form": lemma,
        "meaning_cn": "第一个字母",
        "example_en": f"This is {lemma}.",
        "context_sentence": f"A sentence with {lemma} in it.",
        "in_vocabulary": True,
        "due_date": today_str(),
    }
    values.update(overrides)
    return Word(**values)


def test_first_review_uses_recognition() -> None:
    assert plan_card(make_word(repetitions=0)) == CARD_RECOGNITION


def test_forgotten_word_restarts_with_recognition() -> None:
    """评“忘记”后 repetitions 归零，即使上次评分是 1 也先认义。"""
    word = make_word(repetitions=0)
    assert plan_card(word, last_rating=1, last_verdict=VERDICT_WRONG) == CARD_RECOGNITION


def test_phrase_without_spelling_prompt_falls_back() -> None:
    word = make_word("look up", repetitions=5, meaning_cn="查阅", example_en="Look it up.")
    assert plan_card(word) == CARD_RECOGNITION


def test_word_missing_meaning_falls_back() -> None:
    word = make_word(repetitions=3, meaning_cn="", meaning_en="", example_en="This is.")
    assert plan_card(word) == CARD_RECOGNITION


def test_second_review_uses_dual_card() -> None:
    assert plan_card(make_word(repetitions=1)) == CARD_DUAL


def test_mature_word_uses_spelling() -> None:
    assert plan_card(make_word(repetitions=2)) == CARD_SPELLING


def test_last_vague_rating_forces_dual() -> None:
    """即使 repetitions 已经很高，上次评“模糊”也要先认一遍再默写。"""
    word = make_word(repetitions=5)
    assert plan_card(word, last_rating=RATING_VAGUE) == CARD_DUAL


def test_last_near_verdict_forces_dual() -> None:
    word = make_word(repetitions=5)
    assert plan_card(word, last_rating=RATING_KNOWN, last_verdict=VERDICT_NEAR) == CARD_DUAL


def test_last_wrong_verdict_forces_dual() -> None:
    word = make_word(repetitions=5)
    assert plan_card(word, last_rating=RATING_KNOWN, last_verdict=VERDICT_WRONG) == CARD_DUAL


def test_last_correct_verdict_keeps_spelling() -> None:
    word = make_word(repetitions=5)
    assert plan_card(word, last_rating=RATING_KNOWN, last_verdict=VERDICT_CORRECT) == CARD_SPELLING


def test_plan_uses_history_by_word_id() -> None:
    mature = make_word(repetitions=5, id=7)
    other = make_word("beta", repetitions=5, id=8)

    cards = build_session_plan([mature, other], {7: (RATING_KNOWN, VERDICT_WRONG)})

    assert [card.mode for card in cards] == [CARD_DUAL, CARD_SPELLING]


def test_plan_ignores_history_for_unknown_word() -> None:
    word = make_word(repetitions=5, id=9)
    cards = build_session_plan([word], {1: (1, VERDICT_WRONG)})
    assert cards[0].mode == CARD_SPELLING


def test_empty_plan() -> None:
    assert build_session_plan([]) == []


def _front(mode: str) -> str:
    return CARD_SPELLING if mode == CARD_SPELLING else CARD_RECOGNITION


def test_interleave_limits_same_front_runs() -> None:
    """先排 4 张拼写、再排 4 张看词；穿插后同类连续不超过上限。"""
    spelling = [make_word(f"spell{index}", repetitions=2) for index in range(4)]
    recognition = [make_word(f"new{index}", repetitions=0) for index in range(4)]

    cards = build_session_plan(spelling + recognition)
    fronts = [_front(card.mode) for card in cards]

    assert len(cards) == 8
    for start in range(len(fronts) - MAX_CONSECUTIVE_SAME_FRONT):
        window = fronts[start : start + MAX_CONSECUTIVE_SAME_FRONT + 1]
        assert len(set(window)) > 1, f"连续同类卡面超过 {MAX_CONSECUTIVE_SAME_FRONT}：{fronts}"


def test_interleave_keeps_order_when_all_same_type() -> None:
    words = [make_word(f"word{index}", repetitions=0) for index in range(5)]

    cards = build_session_plan(words)

    assert [card.word.lemma for card in cards] == [word.lemma for word in words]


def test_interleave_does_not_drop_or_duplicate_cards() -> None:
    words = [make_word(f"word{index}", repetitions=index % 3) for index in range(9)]

    cards = build_session_plan(words)

    assert len(cards) == len(words)
    assert sorted(card.word.lemma for card in cards) == sorted(word.lemma for word in words)


def test_mask_only_meaning_falls_back_to_recognition() -> None:
    """释义正好是目标词、挖空后只剩 ____ 时，没有可用线索，自动回退看词。"""
    word = make_word(
        "alpha",
        repetitions=2,
        meaning_cn="alpha",
        meaning_en="",
        example_en="This is alpha.",
    )
    assert plan_card(word) == CARD_RECOGNITION


def test_meaning_with_chinese_hint_still_allows_spelling() -> None:
    word = make_word(
        "alpha",
        repetitions=2,
        meaning_cn="alpha 第一个字母",
        meaning_en="",
        example_en="This is alpha.",
    )
    assert plan_card(word) == CARD_SPELLING


def test_mask_only_meaning_with_punctuation_falls_back() -> None:
    word = make_word(
        "alpha",
        repetitions=2,
        meaning_cn="____ / ____",
        meaning_en="",
        example_en="This is alpha.",
    )
    assert plan_card(word) == CARD_RECOGNITION


def test_dual_stage_also_falls_back_when_meaning_is_mask_only() -> None:
    """repetitions==1 本该排双阶段，但题面无提示时仍回退看词。"""
    word = make_word(
        "alpha",
        repetitions=1,
        meaning_cn="alpha",
        meaning_en="",
        example_en="This is alpha.",
    )
    assert plan_card(word) == CARD_RECOGNITION


def test_numeric_hint_still_allows_spelling() -> None:
    word = make_word(
        "alpha",
        repetitions=2,
        meaning_cn="第 1 个字母",
        meaning_en="",
        example_en="This is alpha.",
    )
    assert plan_card(word) == CARD_SPELLING


def test_interleave_spreads_five_spelling_and_two_recognition() -> None:
    spelling = [make_word(f"spell{index}", repetitions=2) for index in range(5)]
    recognition = [make_word(f"new{index}", repetitions=0) for index in range(2)]

    cards = build_session_plan(spelling + recognition)

    assert [_front(card.mode) for card in cards] == [
        CARD_SPELLING,
        CARD_SPELLING,
        CARD_RECOGNITION,
        CARD_SPELLING,
        CARD_SPELLING,
        CARD_RECOGNITION,
        CARD_SPELLING,
    ]
