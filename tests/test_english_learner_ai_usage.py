"""AI 调用记录：仓库查询、客户端埋点与 AppContext 注入。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest

from english_learner.models.entities import AICall
from english_learner.services import ai_client as ai_module
from english_learner.services.ai_client import AIClient, AIError
from english_learner.services.app_context import AppContext
from english_learner.services.config_store import AIConfig, ConfigStore
from english_learner.storage.db import SCHEMA_VERSION, Database


def make_config() -> AIConfig:
    return AIConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )


def fake_response(
    content: str | None = "OK",
    *,
    finish_reason: str | None = "stop",
    usage: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=(
            SimpleNamespace(prompt_tokens=30, completion_tokens=20, total_tokens=50)
            if usage
            else None
        ),
    )


class FakeCompletions:
    def __init__(self, *effects: object) -> None:
        self.effects = list(effects)

    def create(self, **kwargs):
        item = self.effects.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)

    @classmethod
    def factory(cls, completions: FakeCompletions):
        return lambda **kwargs: cls(completions)


def http_error(status: int, message: str) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx2.Response(status, request=request, json={"error": {"message": message}})
    body = {"error": {"message": message}}
    if status == 401:
        return openai.AuthenticationError(message, response=response, body=body)
    if status == 429:
        return openai.RateLimitError(message, response=response, body=body)
    return openai.BadRequestError(message, response=response, body=body)


class ListRecorder:
    def __init__(self) -> None:
        self.calls: list[AICall] = []

    def insert(self, call: AICall) -> AICall:
        self.calls.append(call)
        return call


class BrokenRecorder:
    def insert(self, call: AICall) -> AICall:
        raise RuntimeError("disk full")


# --------------------------------------------------------------- 仓库
def test_ai_call_repo_summary_filters_and_clear(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.db")
    repo = db.ai_calls

    repo.insert(
        AICall(
            called_at="2026-11-19T10:00:00",
            task="查词",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=500,
        )
    )
    repo.insert(
        AICall(
            called_at="2026-11-19T11:00:00",
            task="查词",
            ok=False,
            error_kind="timeout",
            error_message="请求超时",
            latency_ms=30000,
        )
    )
    repo.insert(
        AICall(
            called_at="2026-11-18T09:00:00",
            task="每日文章",
            total_tokens=200,
            latency_ms=1000,
        )
    )

    summary = repo.summary()
    assert summary.calls == 3
    assert summary.failed_calls == 1
    assert summary.ok_calls == 2
    # 失败记录没有 usage，SUM 会忽略 NULL，而不是把 150+200 算错。
    assert summary.total_tokens == 350
    assert summary.prompt_tokens == 100
    assert summary.completion_tokens == 50
    assert summary.avg_latency_ms == (500 + 30000 + 1000) // 3

    today = repo.summary(since="2026-11-19")
    assert today.calls == 2
    assert today.total_tokens == 150

    failed = repo.list_recent(task="查词", only_failed=True)
    assert len(failed) == 1
    assert failed[0].error_kind == "timeout"
    assert failed[0].attempt == 1

    by_task = {item.task: item for item in repo.by_task()}
    assert by_task["查词"].calls == 2
    assert by_task["查词"].failed_calls == 1
    assert by_task["每日文章"].total_tokens == 200

    assert set(repo.task_options()) == {"查词", "每日文章"}
    assert repo.count() == 3
    assert len(repo.list_all()) == 3

    assert repo.delete_all() == 3
    assert repo.count() == 0
    db.close()


def test_ai_call_repo_insert_fills_missing_time(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.db")
    stored = db.ai_calls.insert(AICall(called_at="", task="查词"))
    assert stored.id is not None
    assert stored.called_at
    db.close()


def test_v6_database_gains_ai_calls_table(tmp_path: Path) -> None:
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE profile (id INTEGER PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()

    db = Database(path)

    assert db.query_one("SELECT COUNT(*) AS n FROM ai_calls")["n"] == 0
    assert int(db.query_one("PRAGMA user_version")[0]) == SCHEMA_VERSION
    db.close()


# ----------------------------------------------------------- 客户端埋点
def test_client_records_success_with_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = ListRecorder()
    completions = FakeCompletions(fake_response("hello"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    text = AIClient(make_config(), recorder=recorder).complete_json(
        [{"role": "user", "content": "hi"}], task="查词"
    )

    assert text == "hello"
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.task == "查词"
    assert call.model == "test-model"
    assert call.base_url == "https://api.example.com/v1"
    assert call.json_mode is True
    assert call.attempt == 1
    assert call.ok is True
    assert (call.prompt_tokens, call.completion_tokens, call.total_tokens) == (30, 20, 50)
    assert call.latency_ms >= 0


def test_client_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = ListRecorder()
    # 用不可重试的 401，确保一次业务调用只产生一条记录。
    completions = FakeCompletions(http_error(401, "invalid api key"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    with pytest.raises(AIError):
        AIClient(make_config(), recorder=recorder).complete_json(
            [{"role": "user", "content": "hi"}], task="查词"
        )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.ok is False
    assert call.error_kind == "auth"
    assert "API Key" in call.error_message
    assert call.total_tokens is None


def test_client_records_each_retry_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = ListRecorder()
    completions = FakeCompletions(http_error(429, "slow down"), fake_response("ok"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))
    monkeypatch.setattr(ai_module.time, "sleep", lambda _seconds: None)

    text = AIClient(make_config(), recorder=recorder).complete(
        [{"role": "user", "content": "hi"}], task="每周复盘"
    )

    assert text == "ok"
    assert [call.attempt for call in recorder.calls] == [1, 2]
    assert [call.ok for call in recorder.calls] == [False, True]
    assert recorder.calls[0].error_kind == "rate_limit"
    # 第二次成功才有 usage；第一次失败时没有响应对象。
    assert recorder.calls[0].total_tokens is None
    assert recorder.calls[1].total_tokens == 50


def test_client_records_truncated_response_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = ListRecorder()
    completions = FakeCompletions(fake_response(None, finish_reason="length"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    with pytest.raises(AIError, match="截断"):
        AIClient(make_config(), recorder=recorder).complete(
            [{"role": "user", "content": "x"}], task="查词"
        )

    call = recorder.calls[0]
    assert call.ok is False
    assert call.error_kind == "format"
    # 被截断的请求仍然消耗了 token，必须计入统计。
    assert call.total_tokens == 50


def test_client_skips_usage_when_gateway_omits_it(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = ListRecorder()
    completions = FakeCompletions(fake_response("hello", usage=False))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    AIClient(make_config(), recorder=recorder).complete(
        [{"role": "user", "content": "hi"}], task="查词"
    )

    call = recorder.calls[0]
    assert call.total_tokens is None
    assert call.ok is True


def test_broken_recorder_does_not_break_ai_call(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(fake_response("hello"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    text = AIClient(make_config(), recorder=BrokenRecorder()).complete(
        [{"role": "user", "content": "hi"}]
    )

    assert text == "hello"


def test_connection_test_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = ListRecorder()
    completions = FakeCompletions(fake_response("OK"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    AIClient(make_config(), recorder=recorder).test_connection()

    assert len(recorder.calls) == 1
    assert recorder.calls[0].task == "连接测试"
    assert recorder.calls[0].ok is True
    assert recorder.calls[0].json_mode is False


# ------------------------------------------------------------ 整体装配
def test_app_context_injects_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(tmp_path / "data.db")
    store = ConfigStore(tmp_path / "config.json")
    context = AppContext(make_config(), store, db)
    completions = FakeCompletions(fake_response("hello"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    context.client().complete_json([{"role": "user", "content": "hi"}], task="查词")

    rows = db.ai_calls.list_all()
    assert len(rows) == 1
    assert rows[0].task == "查词"
    assert rows[0].total_tokens == 50
    context.close()
    db.close()


def test_app_context_uses_business_timeout(tmp_path: Path) -> None:
    """业务调用放宽读超时：thinking 模型生成文章经常超过 30s。"""
    db = Database(tmp_path / "data.db")
    store = ConfigStore(tmp_path / "config.json")
    context = AppContext(make_config(), store, db)

    client = context.client()

    assert client.timeout is ai_module.BUSINESS_TIMEOUT
    # 具体秒数可能随模型 / 网关调优（120s → 300s）；这里只保证“确实放宽了”。
    assert client.timeout.read >= 120.0
    # 连接 / 连接池仍要快速失败，断网时不能干等两分钟。
    assert client.timeout.connect == 10.0
    context.close()
    db.close()
