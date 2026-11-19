"""OpenAI 兼容 API 的客户端封装。

设计目标：
- 一套代码支持 DeepSeek / Kimi / 通义 / 智谱 / 硅基流动 / OpenRouter / Ollama；
- 把第三方 SDK 的异常翻译成用户看得懂的中文提示；
- 只做“发请求 + 解析响应”，不含任何业务提示词（业务在 assessor/article 等模块）。

Java 对照：
- 本文件 ≈ 用 RestTemplate 封装的 Feign Client；
- AIError ≈ 自定义 ServiceException，带用户提示（hint）。

重要：所有调用都是同步阻塞的，**必须**放到 gui.workers.TaskWorker 里执行，
绝不能直接在 UI 线程调用，否则窗口会卡死。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from .config_store import AIConfig

logger = logging.getLogger(__name__)


class AIError(RuntimeError):
    """AI 调用失败。message 给用户看，hint 给排查建议。

    `kind` 用于界面/上层做粗分类（auth / network / timeout / rate_limit /
    not_found / server / request / config / format / unknown）；
    `retryable=True` 表示“稍后再试可能成功”，例如断网、超时、429、5xx。
    """

    def __init__(
        self,
        message: str,
        hint: str = "",
        *,
        retryable: bool = False,
        kind: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.kind = kind

    @property
    def is_offline(self) -> bool:
        """是否为“网络不可达”类错误（离线时复习/生词本仍应可用）。"""
        return self.kind in {"network", "timeout"}

    def display_message(self) -> str:
        if self.hint:
            return f"{self.message}\n{self.hint}"
        return self.message


@dataclass(frozen=True)
class ConnectionResult:
    """连接测试成功后的信息，用于界面展示。"""

    reply: str
    model: str
    latency_ms: int


class AIClient:
    """一个配置对应一个客户端实例。"""

    def __init__(self, config: AIConfig, timeout: float = 30.0) -> None:
        self.config = config.normalized()
        self.timeout = timeout

    def _build_client(self) -> OpenAI:
        """创建 SDK 客户端。

        关闭 SDK 自带重试（max_retries=0）：连接测试要快速失败，
        业务层重试策略由我们自己控制，避免用户等太久。
        """
        return OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.timeout,
            max_retries=0,
        )

    def test_connection(self) -> ConnectionResult:
        """发一次最小请求验证 base_url / key / model 是否可用。

        不用 /models 接口做检测，因为不少兼容网关不实现它；
        直接发一次极短的 chat 请求最接近真实使用场景。
        """
        errors = self.config.validate()
        if errors:
            raise AIError("配置不完整：" + "；".join(errors), kind="config")

        client = self._build_client()
        started_at = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {
                        "role": "user",
                        "content": "This is a connection test. Reply with the single word: OK",
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 - 统一翻译成 AIError
            logger.warning("连接测试失败: %s", exc)
            raise translate_error(exc) from exc

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        content = _extract_content(response)
        if not content:
            raise AIError(
                "服务返回了空内容",
                "模型可能把 token 全用于思考，或该模型不支持 chat 接口。"
                "请换一个模型 id，或检查 base_url 是否为 OpenAI 兼容地址。",
                kind="format",
            )
        return ConnectionResult(reply=content, model=self.config.model, latency_ms=latency_ms)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        """通用补全接口，供 M1 的测评/文章/查词复用。

        json_mode=True 时要求服务端返回 JSON 对象（OpenAI 的 response_format）。
        注意：部分兼容网关不支持该参数，业务层需要自己做兜底解析。
        """
        client = self._build_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI 调用失败: %s", exc)
            raise translate_error(exc) from exc
        return _extract_content(response)

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """要求服务端返回 JSON；不支持 response_format 时降级重试一次。

        部分兼容网关（尤其自建代理）会对 response_format 返回 400。
        降级后仍依赖业务层的 `json_utils.parse_json_object` 做本地兜底解析。
        """
        try:
            return self.complete(
                messages,
                temperature=temperature,
                json_mode=True,
                max_tokens=max_tokens,
            )
        except AIError as exc:
            if not _json_mode_unsupported(exc):
                raise
            logger.info("服务端疑似不支持 response_format，改用普通模式重试")
            return self.complete(messages, temperature=temperature, max_tokens=max_tokens)


def _json_mode_unsupported(exc: AIError) -> bool:
    """根据错误文案粗略判断“只是不支持 JSON 模式”，而不是鉴权/网络问题。"""
    text = f"{exc.message} {exc.hint}".lower()
    return any(hint in text for hint in ("response_format", "response format", "json_object"))


def _extract_content(response: Any) -> str:
    """兼容不同 SDK 返回结构，取出第一条回复的文本。"""
    try:
        choices = response.choices
        if not choices:
            return ""
        message = choices[0].message
        return (message.content or "").strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def translate_error(exc: Exception) -> AIError:
    """把 SDK 异常翻译成中文可操作提示。

    判断顺序很重要：AuthenticationError / NotFoundError 都是
    APIStatusError 的子类，必须先匹配更具体的类型。
    """
    if isinstance(exc, AuthenticationError):
        return AIError(
            "API Key 无效或没有权限（HTTP 401）",
            "请检查 Key 是否复制完整、是否已过期；部分服务商还需要先在控制台开通模型。",
            kind="auth",
        )
    if isinstance(exc, PermissionDeniedError):
        return AIError(
            "没有访问该模型的权限（HTTP 403）",
            "请在服务商控制台确认账号已开通该模型，或检查 Key 的权限范围。",
            kind="auth",
        )
    if isinstance(exc, NotFoundError):
        return AIError(
            "模型或接口地址不存在（HTTP 404）",
            "请检查模型 id 拼写；base_url 通常需要以 /v1 结尾"
            "（例如 https://api.deepseek.com/v1）。",
            kind="not_found",
        )
    if isinstance(exc, RateLimitError):
        return AIError(
            "请求过于频繁或额度不足（HTTP 429）",
            "请稍后再试，或检查账户余额与限流设置。",
            retryable=True,
            kind="rate_limit",
        )
    if isinstance(exc, APITimeoutError):
        return AIError(
            "请求超时",
            "请检查网络，或换一个更近的服务地址后重试。",
            retryable=True,
            kind="timeout",
        )
    if isinstance(exc, APIConnectionError):
        return AIError(
            "无法连接到服务地址",
            "请检查 base_url 是否正确、本机网络/代理是否可用；本地 Ollama 需先启动服务。",
            retryable=True,
            kind="network",
        )
    if isinstance(exc, APIStatusError):
        detail = _extract_error_detail(exc)
        suffix = f"：{detail}" if detail else ""
        server_side = exc.status_code >= 500 or exc.status_code == 408
        return AIError(
            f"服务端返回 HTTP {exc.status_code}{suffix}",
            "请根据状态码检查配置；如果状态码是 400，通常是模型 id 或参数不被支持。",
            retryable=server_side,
            kind="server" if server_side else "request",
        )
    return AIError(f"调用失败：{exc}")


def _extract_error_detail(exc: APIStatusError) -> str:
    """尽量从错误响应里抠出服务端给的原因，截断避免刷屏。"""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if message:
                return str(message)[:300]
        if body.get("message"):
            return str(body["message"])[:300]
    text = str(exc)
    return text[:300]
