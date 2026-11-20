"""AI 客户端错误翻译与连接测试的单元测试（不访问真实网络）。"""

from __future__ import annotations

import uuid
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


def fake_response(
    content: str | None,
    *,
    finish_reason: str | None = "stop",
) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


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


def test_per_call_timeout_overrides_client_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """后台预取用 35s 超时：请求层要真正带上，不能默默沿用业务默认的 300s。"""
    completions = FakeCompletions(response=fake_response("hello"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    AIClient(make_config()).complete([{"role": "user", "content": "hi"}], timeout=30.0)

    assert completions.last_kwargs["timeout"] == 30.0


def test_truncated_output_has_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(response=fake_response('{"title": "cut', finish_reason="length"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    with pytest.raises(AIError, match="截断"):
        AIClient(make_config()).complete([{"role": "user", "content": "hi"}])


class SequenceCompletions:
    """按顺序返回响应或异常，用于验证客户端层重试。"""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls = 0
        self.kwargs_list: list[dict] = []

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs_list.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_transient_errors_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    completions = SequenceCompletions(
        openai.APITimeoutError(request=request),
        fake_response("ok"),
    )
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))
    monkeypatch.setattr(ai_module.time, "sleep", lambda _seconds: None)

    text = AIClient(make_config()).complete([{"role": "user", "content": "hi"}])

    assert text == "ok"
    assert completions.calls == 2


def test_long_read_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """业务调用读超时 300s：超时说明服务端真慢，不再自动重试拖成十几分钟。"""
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    completions = SequenceCompletions(
        openai.APITimeoutError(request=request),
        fake_response("ok"),
    )
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))
    monkeypatch.setattr(ai_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(AIError, match="超时"):
        AIClient(make_config(), timeout=ai_module.BUSINESS_TIMEOUT).complete(
            [{"role": "user", "content": "hi"}]
        )

    assert completions.calls == 1


def _capture_built_clients(monkeypatch: pytest.MonkeyPatch, completions) -> list[FakeClient]:
    """把每次构造的假客户端收集起来，方便断言构造参数。"""
    built: list[FakeClient] = []

    def build(**kwargs):
        client = FakeClient(completions)
        client.init_kwargs = kwargs
        built.append(client)
        return client

    monkeypatch.setattr(ai_module, "OpenAI", build)
    return built


def test_opencode_base_url_adds_session_header(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AIConfig(
        base_url="https://opencode.ai/inference/go/openai/v1",
        api_key="oc_sk-test",
        model="deepseek-v4.1-flash",
    )
    completions = FakeCompletions(response=fake_response("OK"))
    built = _capture_built_clients(monkeypatch, completions)

    client = AIClient(config)
    client.test_connection()

    headers = built[0].init_kwargs["default_headers"]
    assert headers["x-opencode-session"] == client._session_id
    # 会话 id 必须是合法 UUID；不同客户端实例互相独立。
    assert uuid.UUID(headers["x-opencode-session"])

    AIClient(config).test_connection()
    assert built[1].init_kwargs["default_headers"]["x-opencode-session"] != client._session_id


def test_other_provider_has_no_session_header(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = FakeCompletions(response=fake_response("OK"))
    built = _capture_built_clients(monkeypatch, completions)

    AIClient(make_config()).test_connection()

    assert built[0].init_kwargs["default_headers"] is None


def test_opencode_detection_matches_only_its_own_domain() -> None:
    assert ai_module._needs_opencode_session("https://opencode.ai/inference/go/openai/v1")
    assert ai_module._needs_opencode_session("https://api.opencode.ai/v1")
    assert not ai_module._needs_opencode_session("https://not-opencode.ai/v1")
    assert not ai_module._needs_opencode_session("https://api.deepseek.com/v1")


def test_json_mode_unsupported_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    config = AIConfig(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="no-json-model",
    )
    request = httpx2.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx2.Response(
        400,
        request=request,
        json={"error": {"message": "response_format is not supported"}},
    )
    error = openai.BadRequestError(
        "response_format is not supported",
        response=response,
        body={"error": {"message": "response_format is not supported"}},
    )
    completions = SequenceCompletions(error, fake_response("{}"), fake_response("{}"))
    monkeypatch.setattr(ai_module, "OpenAI", FakeClient.factory(completions))

    first = AIClient(config).complete_json([{"role": "user", "content": "hi"}])
    second = AIClient(config).complete_json([{"role": "user", "content": "hi"}])

    assert first == "{}"
    assert second == "{}"
    assert completions.calls == 3
    assert "response_format" in completions.kwargs_list[0]
    assert "response_format" not in completions.kwargs_list[1]
    assert "response_format" not in completions.kwargs_list[2]
