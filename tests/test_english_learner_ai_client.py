"""AI 客户端错误翻译与连接测试的单元测试（不访问真实网络）。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx2
import openai
import pytest

from english_learner.services import ai_client as ai_module
from english_learner.services.ai_client import AIClient, AIError
from english_learner.services.config_store import AIConfig


def make_config() -> AIConfig:
    return AIConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="test-model",
    )


def fake_response(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeCompletions:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions_api = completions
        self.chat = SimpleNamespace(completions=completions)
        self.init_kwargs: dict = {}

    @classmethod
    def factory(cls, completions: FakeCompletions):
        def build(**kwargs):
            client = cls(completions)
            client.init_kwargs = kwargs
            return client

        return build


def http_error(status: int, message: str) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx2.Response(status, request=request, json={"error": {"message": message}})
    body = {"error": {"message": message}}
    if status == 401:
        return openai.AuthenticationError(message, response=response, body=body)
    if status == 404:
        return openai.NotFoundError(message, response=response, body=body)
    if status == 429:
        return openai.RateLimitError(message, response=response, body=body)
    return openai.BadRequestError(message, response=response, body=body)


def test_test_connection_success(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(response=fake_response("OK"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    result = AIClient(make_config()).test_connection()

    assert result.reply == "OK"
    assert result.model == "test-model"
    assert result.latency_ms >= 0
    assert completions.last_kwargs["model"] == "test-model"
    assert completions.last_kwargs["messages"][0]["role"] == "user"


def test_auth_error_is_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(error=http_error(401, "invalid api key"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    with pytest.raises(AIError) as exc_info:
        AIClient(make_config()).test_connection()

    message = exc_info.value.display_message()
    assert "401" in message
    assert "API Key" in message


def test_not_found_suggests_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(error=http_error(404, "model not found"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    with pytest.raises(AIError) as exc_info:
        AIClient(make_config()).test_connection()

    assert "/v1" in exc_info.value.display_message()


def test_status_error_includes_server_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(error=http_error(400, "temperature must be <= 2"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    with pytest.raises(AIError) as exc_info:
        AIClient(make_config()).test_connection()

    assert "400" in exc_info.value.display_message()
    assert "temperature must be <= 2" in exc_info.value.display_message()


def test_empty_content_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(response=fake_response(None))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    with pytest.raises(AIError, match="空内容"):
        AIClient(make_config()).test_connection()


def test_connection_error_is_translated() -> None:
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    error = openai.APIConnectionError(request=request)

    translated = ai_module.translate_error(error)

    assert "无法连接" in translated.display_message()


def test_error_kinds_classify_offline_and_retryable() -> None:
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")

    network = ai_module.translate_error(openai.APIConnectionError(request=request))
    assert network.kind == "network"
    assert network.retryable is True
    assert network.is_offline is True

    timeout = ai_module.translate_error(openai.APITimeoutError(request=request))
    assert timeout.kind == "timeout"
    assert timeout.retryable is True
    assert timeout.is_offline is True

    auth = ai_module.translate_error(http_error(401, "bad key"))
    assert auth.kind == "auth"
    assert auth.retryable is False
    assert auth.is_offline is False

    not_found = ai_module.translate_error(http_error(404, "missing"))
    assert not_found.kind == "not_found"
    assert not_found.retryable is False

    rate_limit = ai_module.translate_error(http_error(429, "slow down"))
    assert rate_limit.kind == "rate_limit"
    assert rate_limit.retryable is True

    bad_request = ai_module.translate_error(http_error(400, "bad params"))
    assert bad_request.kind == "request"
    assert bad_request.retryable is False


def test_server_error_is_retryable() -> None:
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx2.Response(503, request=request, json={"error": {"message": "busy"}})
    error = openai.APIStatusError("busy", response=response, body=None)

    translated = ai_module.translate_error(error)

    assert translated.kind == "server"
    assert translated.retryable is True
    assert "503" in translated.display_message()


def test_complete_uses_configured_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(response=fake_response("hello"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    text = AIClient(make_config()).complete([{"role": "user", "content": "hi"}])

    assert text == "hello"
    assert completions.last_kwargs["temperature"] == 0.7
