"""划词查词与阅读器 UI 的测试（offscreen，无网络）。"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QSplitter  # noqa: E402

from english_learner.gui.pages.reader import ReaderPage  # noqa: E402
from english_learner.gui.regenerate_dialog import RegenerateDialog  # noqa: E402
from english_learner.gui.widgets import ArticleView, WordPanel  # noqa: E402
from english_learner.gui.workers import TaskWorker  # noqa: E402
from english_learner.models.entities import (  # noqa: E402
    Article,
    Highlight,
    Profile,
    QuizQuestion,
    ReadingQuiz,
    Word,
    now_iso,
    today_str,
)
from english_learner.services.ai_client import AIError  # noqa: E402
from english_learner.services.app_context import AppContext  # noqa: E402
from english_learner.services.config_store import AIConfig, ConfigStore  # noqa: E402
from english_learner.services.dictionary_service import (  # noqa: E402
    DictionaryService,
    extract_sentence,
    normalize_lemma,
)
from english_learner.services.learner_memory import LearnerMemory  # noqa: E402
from english_learner.services.prompts import build_dictionary_messages  # noqa: E402
from english_learner.storage.db import Database  # noqa: E402

_APP: QApplication | None = None


def get_app() -> QApplication:
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class FakeCompleter:
    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages, *, temperature=None, max_tokens=None, task="") -> str:
        self.calls += 1
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "data.db")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def disable_reader_prefetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """本文件的阅读器测试不联网：统一停用打开文章后的后台预取。

    预取本身由 test_english_learner_prefetch.py 用假 service 单独验证。
    """
    monkeypatch.setattr(ReaderPage, "_start_prefetch", lambda self, article: None)


def make_article(**overrides) -> Article:
    values = {
        "article_date": today_str(),
        "title": "Small Habits",
        "content": "Small habits are powerful. Resilient people keep going.",
        "level": "B1",
        "word_count": 8,
        "created_at": now_iso(),
    }
    values.update(overrides)
    return Article(**values)


def make_word(**overrides) -> Word:
    values = {
        "lemma": "habits",
        "display_form": "habits",
        "meaning_cn": "习惯",
        "in_vocabulary": True,
        "status": "new",
        "due_date": today_str(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    values.update(overrides)
    return Word(**values)


# ---------------------------------------------------------------- 纯函数
def test_normalize_lemma() -> None:
    assert normalize_lemma("  Hello, ") == "hello"
    assert normalize_lemma("(resilient)") == "resilient"
    assert normalize_lemma("don't") == "don't"
    assert normalize_lemma("well-known") == "well-known"
    assert normalize_lemma("123") == ""
    assert normalize_lemma("中文") == ""
    assert normalize_lemma("") == ""
    assert normalize_lemma("hello world") == ""


def test_extract_sentence() -> None:
    text = "Anna waited. Resilient people recover fast! Last one."
    start = text.index("Resilient")
    assert extract_sentence(text, start, start + 9) == "Resilient people recover fast!"

    start = text.index("Anna")
    assert extract_sentence(text, start, start + 4) == "Anna waited."

    multiline = "First line here\nSecond line here"
    assert extract_sentence(multiline, 6, 10) == "First line here"


def test_extract_sentence_truncates_long_text() -> None:
    text = "word " * 100 + "target" + " more" * 100
    start = text.index("target")
    sentence = extract_sentence(text, start, start + 6, max_len=120)
    assert len(sentence) <= 120
    assert "target" in sentence


# ---------------------------------------------------------------- 查词服务
def test_lookup_hits_cache_without_ai(db: Database) -> None:
    stored = db.words.upsert(make_word())
    fake = FakeCompleter()  # 没有预设回复，被调用就会抛 IndexError
    service = DictionaryService(fake, db.words, db.stats)

    result = service.lookup("Habits,", "Small habits are powerful.")

    assert result.cached is True
    assert result.word.id == stored.id
    assert fake.calls == 0


def test_lookup_new_word_calls_ai_and_stores(db: Database) -> None:
    payload = {
        "lemma": "resilient",
        "display_form": "resilient",
        "phonetic": "/rɪˈzɪliənt/",
        "pos": "adj.",
        "meaning_cn": "有韧性的",
        "meaning_en": "able to recover quickly",
        "example_en": "Children are resilient.",
        "example_cn": "孩子很有韧性。",
        "mnemonic": "re-silient：像弹簧一样重新弹起来",
    }
    fake = FakeCompleter(json.dumps(payload, ensure_ascii=False))
    service = DictionaryService(fake, db.words, db.stats)

    result = service.lookup("Resilient", "Resilient people keep going.", None, "B1")

    assert fake.calls == 1
    assert result.cached is False
    assert result.word.meaning_cn == "有韧性的"
    assert result.word.mnemonic.startswith("re-silient")
    assert result.word.id is not None
    stored = db.words.get_by_lemma("resilient")
    assert stored is not None
    assert stored.due_date == today_str()
    assert stored.context_sentence == "Resilient people keep going."


def test_lookup_injects_learner_memory(db: Database) -> None:
    """M5.2：查词 prompt 带上学习者记忆，但空记忆时保持原 prompt。"""
    payload = {
        "lemma": "resilient",
        "meaning_cn": "有韧性的",
        "cefr_band": "B2",
    }
    fake = FakeCompleter(json.dumps(payload, ensure_ascii=False))
    memory = LearnerMemory(level="B1", error_types=("vocabulary",))
    service = DictionaryService(
        fake,
        db.words,
        db.stats,
        memory_provider=lambda: memory,
    )

    service.lookup("Resilient", "Resilient people keep going.", None, "B1")

    prompt = fake.messages[0][-1]["content"]
    assert "【学习者记忆】" in prompt
    assert "近 7 天主要错因：语境词义" in prompt
    # 查词任务不注入积压 / 上周计划。
    assert "到期未复习" not in prompt
    assert "上周计划重点" not in prompt


def test_dictionary_prompt_only_mentions_base_form_when_it_differs() -> None:
    """词形与原形相同时，不许 AI 再写“原形就是 X”（释义卡片会重复）。"""
    prompt = build_dictionary_messages("damage", "The earthquake caused damage.", "B1")[-1][
        "content"
    ]

    assert "display_form 与 lemma 不同时" in prompt
    assert "两者相同（仅大小写不同也算相同）时" in prompt
    assert "不要出现“原形”字样" in prompt
    assert "原形就是 X" in prompt
    # 旧措辞会诱导模型每次都解释词形关系，不能回来。
    assert "说明它与原形的关系" not in prompt


def test_lookup_stores_cefr_band_and_tolerates_invalid(db: Database) -> None:
    payload = {
        "lemma": "resilient",
        "meaning_cn": "有韧性的",
        "cefr_band": "b2",
    }
    service = DictionaryService(
        FakeCompleter(json.dumps(payload, ensure_ascii=False)), db.words, db.stats
    )

    result = service.lookup("Resilient", "Resilient people keep going.", None, "B1")
    assert result.word.cefr_band == "B2"

    payload["lemma"] = "stubborn"
    payload["cefr_band"] = "Z9"
    service = DictionaryService(
        FakeCompleter(json.dumps(payload, ensure_ascii=False)), db.words, db.stats
    )
    invalid = service.lookup("Stubborn", "Stubborn people keep going.", None, "B1")
    assert invalid.word.cefr_band == ""


def test_lookup_uses_base_form_and_caches_inflection(db: Database) -> None:
    """went 应存成 go；再次查 went 直接命中词形缓存，不再调用 AI。"""
    payload = {
        "lemma": "go",
        "display_form": "went",
        "phonetic": "/ɡoʊ/",
        "pos": "v.",
        "meaning_cn": "去；走（go 的过去式）",
        "meaning_en": "to move or travel to a place (past tense of go)",
        "example_en": "She went home after work.",
        "example_cn": "她下班后回家了。",
        "mnemonic": "go-went-gone：想象一个人出门又走远",
    }
    fake = FakeCompleter(json.dumps(payload, ensure_ascii=False))
    service = DictionaryService(fake, db.words, db.stats)

    first = service.lookup("Went", "She went home after work.", None, "B1")

    assert first.cached is False
    assert first.word.lemma == "go"
    assert first.word.display_form == "Went"
    assert first.word.mnemonic.startswith("go-went-gone")

    second = service.lookup("went", "She went home after work.", None, "B1")
    assert second.cached is True
    assert fake.calls == 1
    assert second.word.id == first.word.id
    # 查词只写缓存，不自动加入生词本
    assert first.word.in_vocabulary is False
    assert db.words.count() == 0
    assert db.words.count(include_cache=True) == 1
    assert {form.form for form in db.words.list_all_forms()} == {"went", "go"}


def test_lookup_flags_context_mismatch_and_can_refresh(db: Database) -> None:
    """缓存释义来自另一句时应提示，并可跳过缓存按本句重查。"""
    first_payload = json.dumps(
        {
            "lemma": "bank",
            "meaning_cn": "河岸",
            "meaning_en": "the land beside a river",
        },
        ensure_ascii=False,
    )
    second_payload = json.dumps(
        {
            "lemma": "bank",
            "meaning_cn": "银行",
            "meaning_en": "a financial institution",
        },
        ensure_ascii=False,
    )
    fake = FakeCompleter(first_payload, second_payload)
    service = DictionaryService(fake, db.words, db.stats)

    first = service.lookup("bank", "We sat on the river bank.", None, "B1")
    assert first.cached is False
    assert first.context_mismatch is False

    cached = service.lookup("bank", "She works at a bank downtown.", None, "B1")
    assert cached.cached is True
    assert cached.word.meaning_cn == "河岸"
    assert cached.context_mismatch is True
    assert fake.calls == 1

    refreshed = service.lookup(
        "bank",
        "She works at a bank downtown.",
        None,
        "B1",
        refresh=True,
    )
    assert fake.calls == 2
    assert refreshed.cached is False
    assert refreshed.word.meaning_cn == "银行"
    assert refreshed.word.context_sentence == "She works at a bank downtown."
    assert refreshed.context_mismatch is False


def test_generate_mnemonic_for_existing_word(db: Database) -> None:
    word = db.words.upsert(make_word(lemma="resilient"))
    assert word.id is not None

    fake = FakeCompleter(json.dumps({"mnemonic": "re + silent：重新安静下来就有韧性"}))
    service = DictionaryService(fake, db.words, db.stats)

    updated = service.generate_mnemonic(word, "B1")

    assert fake.calls == 1
    assert updated.mnemonic == "re + silent：重新安静下来就有韧性"
    stored = db.words.get_by_id(word.id)
    assert stored is not None and stored.mnemonic == updated.mnemonic


def test_generate_mnemonic_requires_meaningful_reply(db: Database) -> None:
    word = db.words.upsert(make_word(lemma="resilient"))
    service = DictionaryService(FakeCompleter(json.dumps({})), db.words, db.stats)

    with pytest.raises(AIError):
        service.generate_mnemonic(word, "B1")


def test_lookup_rejects_non_word(db: Database) -> None:
    service = DictionaryService(FakeCompleter(), db.words, db.stats)
    with pytest.raises(AIError):
        service.lookup("12345")


def test_add_to_vocabulary_is_idempotent(db: Database) -> None:
    article = db.articles.insert(make_article())
    # 模拟“双击查词后只进缓存”的状态，再显式加入生词本
    word = db.words.upsert(make_word(in_vocabulary=False))
    assert article.id is not None and word.id is not None
    assert db.words.count() == 0
    service = DictionaryService(FakeCompleter(), db.words, db.stats)

    first = service.add_to_vocabulary(
        word,
        article_id=article.id,
        start_offset=0,
        end_offset=5,
        sentence="Small habits are powerful.",
    )
    second = service.add_to_vocabulary(
        word,
        article_id=article.id,
        start_offset=0,
        end_offset=5,
    )

    assert first is True
    assert second is False
    assert len(db.words.list_highlights(article.id)) == 1


def test_add_to_vocabulary_schedules_first_review_tomorrow(db: Database) -> None:
    """查词缓存可能放了很多天；第一次入本时把 due_date 改成明天，避免一入本即逾期。"""
    article = db.articles.insert(make_article())
    word = db.words.upsert(make_word(in_vocabulary=False, due_date="2020-01-01"))
    assert article.id is not None and word.id is not None
    service = DictionaryService(FakeCompleter(), db.words, db.stats)

    service.add_to_vocabulary(
        word,
        article_id=article.id,
        start_offset=0,
        end_offset=5,
    )

    stored = db.words.get_by_id(word.id)
    assert stored is not None
    assert stored.in_vocabulary is True
    assert stored.due_date == (date.today() + timedelta(days=1)).isoformat()


def test_re_adding_does_not_reset_existing_due_date(db: Database) -> None:
    """已经入本的词再次添加（例如换个位置高亮）不能重置 SRS 排期。"""
    article = db.articles.insert(make_article())
    word = db.words.upsert(make_word(in_vocabulary=True, due_date="2099-01-01"))
    assert article.id is not None and word.id is not None
    service = DictionaryService(FakeCompleter(), db.words, db.stats)

    service.add_to_vocabulary(
        word,
        article_id=article.id,
        start_offset=0,
        end_offset=5,
    )

    stored = db.words.get_by_id(word.id)
    assert stored is not None
    assert stored.due_date == "2099-01-01"
    assert db.stats.get(today_str()).words_added == 1
    stored = db.words.get_by_id(word.id)
    assert stored is not None and stored.in_vocabulary is True
    assert db.words.count() == 1


# ------------------------------------------------------------------ 阅读器
def test_reader_page_uses_floating_word_card(db: Database) -> None:
    """阅读器不再有右侧固定栏，释义卡是浮在页面上的子控件。"""
    get_app()
    db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)
    page.resize(1000, 700)
    page.on_activated()

    assert page.findChildren(QSplitter) == []
    assert page._panel.parent() is page
    assert page._panel.isHidden() is True

    word = db.words.upsert(make_word())
    page._panel.show_definition(word, "Small habits are powerful.")
    # 模拟双击时记录的单词矩形，验证卡片定位/夹取逻辑。
    page._view._last_word_rect = QRect(600, 600, 80, 20)
    page._show_word_card()

    assert page._panel.isHidden() is False
    assert page._panel.width() == 380
    assert page._panel.x() >= 0 and page._panel.y() >= 0
    assert page._panel.x() + page._panel.width() <= page.width()
    assert page._panel.y() + page._panel.height() <= page.height()

    # 点击正文 / 关闭按钮 / Esc 路径都会收起卡片
    page._view.background_clicked.emit()
    assert page._panel.isHidden() is True

    page._show_word_card()
    page._panel._close_button.click()
    assert page._panel.isHidden() is True

    # 收起后迟到的查词结果不应让卡片再次弹出
    page._pending = ("late", 0, 4, "")
    page._show_word_card()
    page._hide_word_card()
    page._on_lookup_ready(object())
    assert page._pending is None
    assert page._panel.isHidden() is True


def test_closing_word_card_releases_running_lookup(db: Database) -> None:
    """查词进行中关闭卡片，要立刻释放 _worker 槽位，否则下一次双击被吞掉。"""
    get_app()
    db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)
    page.on_activated()

    started = threading.Event()

    def slow_lookup() -> None:
        started.set()
        time.sleep(0.2)

    worker = TaskWorker(slow_lookup)
    worker.start()
    assert started.wait(1)

    # 模拟“AI 还没返回”的状态：槽位被占用、卡片处于加载中。
    page._worker = worker
    page._busy_phase = "lookup"
    page._pending = ("word", 0, 4, "")
    page._panel.show_loading("word")
    page._show_word_card()

    page._hide_word_card()

    assert page._worker is None
    assert page._busy_phase == ""
    assert page._pending is None
    assert page._panel.isHidden() is True
    worker.wait(2000)


def test_article_view_renders_highlights(db: Database) -> None:
    get_app()
    view = ArticleView()
    article = make_article()
    view.set_article(article)
    assert view.toPlainText() == article.content

    view.set_highlights([Highlight(article_id=1, word_id=1, start_offset=0, end_offset=5)])
    assert view.highlight_count() == 1

    view.set_highlights([])
    assert view.highlight_count() == 0


def test_reader_page_loads_cached_article_without_worker(db: Database) -> None:
    get_app()
    article = db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()

    assert page._worker is None
    assert page._view.toPlainText() == article.content
    assert "Small Habits" in page._title_label.text()

    stored = db.articles.get_by_date(today_str())
    assert stored is not None and stored.read_at is not None
    assert db.stats.get(today_str()).article_read == 1


def test_reader_auto_generates_first_visit(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """第一次进阅读页（当天没有文章）仍然自动生成。"""
    get_app()
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)
    calls: list[dict] = []
    monkeypatch.setattr(page, "_start_generation", lambda **kwargs: calls.append(kwargs))

    page.refresh()

    assert calls == [{"regenerate": False}]
    assert page._generation_failed is False


def test_reader_does_not_auto_generate_again_after_failure(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生成失败后切页回来不再自动重发，必须由用户点“重试生成”。

    超时一次要等好几分钟，自动重发既耗 token 又会让用户以为界面卡住。
    """
    get_app()
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)

    page._on_article_failed("请求超时")

    assert page._generation_failed is True
    assert page._generate_button.isHidden() is False
    assert page._generate_button.text() == "重试生成"

    calls: list[dict] = []
    monkeypatch.setattr(page, "_start_generation", lambda **kwargs: calls.append(kwargs))
    page.refresh()

    assert calls == []
    assert page._generate_button.isHidden() is False
    assert page._generate_button.text() == "重试生成"

    # 用户主动点重试才真正发起生成，并清掉失败标记。
    page._on_generate_clicked()

    assert calls == [{"regenerate": False}]
    assert page._generation_failed is False


def test_reader_feedback_button_needs_profile_and_article(db: Database) -> None:
    get_app()
    db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()
    assert page._feedback_button.isEnabled() is False

    db.profiles.save(Profile(cefr_level="B1"))
    page.refresh()
    assert page._feedback_button.isEnabled() is True


def test_reader_back_translation_button_needs_only_article(db: Database) -> None:
    """回译练习只依赖当天文章，不需要先完成测评。"""
    get_app()
    db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()

    assert page._back_translation_button.isEnabled() is True
    assert page._back_translation_button.text() == "回译练习"


def test_reader_quiz_button_reflects_completion(db: Database) -> None:
    get_app()
    article = db.articles.insert(make_article())
    db.profiles.save(Profile(cefr_level="B1"))
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()
    assert page._quiz_button.isEnabled() is True
    assert page._quiz_button.text() == "阅读完成"

    assert article.id is not None
    quiz = db.quizzes.insert(
        ReadingQuiz(
            article_id=article.id,
            article_date=today_str(),
            level="B1",
            questions=(
                QuizQuestion(prompt="Q?", options=("a", "b", "c"), answer_index=0),
                QuizQuestion(prompt="Q2?", options=("a", "b", "c"), answer_index=1),
                QuizQuestion(prompt="Q3?", options=("a", "b", "c"), answer_index=2),
            ),
            created_at=now_iso(),
        )
    )
    assert quiz.id is not None
    db.quizzes.save_result(
        quiz.id,
        answers=(0, 1, 2),
        explanations=("", "", ""),
        score=3,
        previous_level="B1",
        result_level="B2",
        feedback="全对，升一档。",
    )

    page.refresh()
    assert page._quiz_button.text() == "查看答题结果"
    assert page._quiz_button.isEnabled() is True


def test_regenerate_dialog_returns_input_or_empty() -> None:
    get_app()
    dialog = RegenerateDialog()
    assert dialog.request_text() == ""

    dialog._request_edit.setPlainText("  写一篇关于深海生物的文章  ")
    assert dialog.request_text() == "写一篇关于深海生物的文章"


def test_word_panel_shows_original_form_and_mnemonic() -> None:
    get_app()
    panel = WordPanel()
    word = Word(
        lemma="go",
        display_form="went",
        meaning_cn="去；走（go 的过去式）",
        mnemonic="go-went-gone：出门又走远",
        created_at=now_iso(),
        updated_at=now_iso(),
    )

    panel.show_definition(word, "She went home after work.")

    assert panel._title.text() == "went"
    assert panel._lemma.text() == "原形：go"
    assert panel._lemma.isHidden() is False
    assert "go-went-gone" in panel._mnemonic.text()
    assert panel._mnemonic.isHidden() is False


def test_reader_cancel_generation_frees_ui_immediately(db: Database) -> None:
    """点取消后旧请求转入后台，界面立刻恢复并露出「重试生成」。"""
    get_app()
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)

    started = threading.Event()
    release = threading.Event()

    def task() -> str:
        started.set()
        release.wait(3)
        return "done"

    worker = TaskWorker(task)
    worker.start()
    assert started.wait(2.0) is True

    page._worker = worker
    page._busy_phase = "generate"
    page._set_busy(True)
    assert page._cancel_button.isHidden() is False

    page._on_cancel_clicked()

    assert page._worker is None
    assert worker.is_cancelled() is True
    assert page._generation_failed is True
    assert page._generate_button.isHidden() is False
    assert page._generate_button.text() == "重试生成"
    assert page._cancel_button.isHidden() is True
    assert "已取消生成" in page._status.text()

    release.set()
    assert worker.wait(3000) is True


def test_reader_shutdown_returns_worker_that_ignores_cancel(db: Database) -> None:
    """关闭页面时先等宽限期；任务卡住时把 worker 交回主窗口兜底等待。"""
    get_app()
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)

    started = threading.Event()
    release = threading.Event()

    def task() -> str:
        started.set()
        release.wait(3)
        return "done"

    worker = TaskWorker(task)
    worker.start()
    assert started.wait(2.0) is True

    page._worker = worker
    stuck = page.shutdown(timeout_ms=100)

    assert stuck is worker
    assert worker.isRunning() is True

    release.set()
    assert worker.wait(3000) is True


def test_reader_page_without_profile_does_not_generate(db: Database) -> None:
    get_app()
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )

    page = ReaderPage(context)
    page.on_activated()

    assert page._worker is None
    assert not page._generate_button.isHidden()
    assert page._quiz_button.isEnabled() is False
    assert "测评" in page._status.text()


# ------------------------------------------------- M5.3 可读性指标展示
def test_reader_shows_readability_metrics(db: Database) -> None:
    get_app()
    db.articles.insert(
        make_article(
            metrics={
                "word_total": 100,
                "unknown_count": 5,
                "unknown_ratio": 0.05,
                "avg_sentence_len": 12.0,
                "max_sentence_len": 20,
                "target_total": 2,
                "target_found": 2,
                "wordlist_size": 3000,
            }
        )
    )
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)
    page.on_activated()

    meta = page._meta_label.text()
    assert "生词 5.0%" in meta
    assert "目标词 2 个" in meta
    assert "偏难" not in meta


def test_reader_marks_hard_article(db: Database) -> None:
    get_app()
    db.articles.insert(
        make_article(
            metrics={
                "word_total": 100,
                "unknown_count": 30,
                "unknown_ratio": 0.3,
                "avg_sentence_len": 12.0,
                "max_sentence_len": 20,
                "target_total": 0,
                "target_found": 0,
                "wordlist_size": 3000,
            }
        )
    )
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)
    page.on_activated()

    assert "偏难" in page._meta_label.text()


def test_reader_old_article_without_metrics_still_renders(db: Database) -> None:
    get_app()
    db.articles.insert(make_article())
    context = AppContext(
        AIConfig(base_url="https://api.example.com/v1", api_key="sk-test", model="test"),
        ConfigStore(Path("unused.json")),
        db,
    )
    page = ReaderPage(context)
    page.on_activated()

    meta = page._meta_label.text()
    assert "生词" not in meta
    assert "偏难" not in meta
