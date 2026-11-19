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
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

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

from ..models.entities import AICall, now_iso
from .config_store import AIConfig

logger = logging.getLogger(__name__)

# 瞬时错误重试间隔（秒）：429 / 超时 / 5xx 最多再试两次。
_RETRY_DELAYS: tuple[float, ...] = (0.6, 1.4)

# 某些兼容网关不支持 response_format={"type": "json_object"}。
# 成功探测一次后按 (base_url, model) 记住，避免每次查词都先失败一次再降级。
_JSON_MODE_UNSUPPORTED: set[tuple[str, str]] = set()
_JSON_MODE_LOCK = threading.Lock()

# opencode 网关（https://opencode.ai/inference/go/...）的 OpenAI 兼容接口要求
# 每个客户端带上 x-opencode-session 头，否则一律返回 400 MissingSessionID。
# 其它服务商不需要这个头，按 base_url 的域名判断，避免污染通用配置。
_OPENCODE_HOSTS: tuple[str, ...] = ("opencode.ai",)
_OPENCODE_SESSION_HEADER = "x-opencode-session"


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


class AICallRecorder(Protocol):
    """调用记录写入接口：AppContext 注入仓库，单测可传一个列表收集器。

    刻意不依赖 storage 层：AIClient 只需要“把这一行记下来”。
    记录失败绝不能影响 AI 调用本身，AIClient 内部会吞掉异常。
    """

    def insert(self, call: AICall) -> AICall: ...


class AIClient:
    """一个配置对应一个客户端实例。

    同一个实例会复用底层 OpenAI/httpx 连接池：一次阅读检测有出题、审核、
    盲评、讲评等多轮请求，复用连接能省掉多次 TCP/TLS 握手。
    """

    def __init__(
        self,
        config: AIConfig,
        timeout: float = 30.0,
        recorder: AICallRecorder | None = None,
    ) -> None:
        self.config = config.normalized()
        self.timeout = timeout
        self._recorder = recorder
        self._client: OpenAI | None = None
        self._client_lock = threading.Lock()
        # 网关会话 id：同一实例复用，一次会话的多轮请求能被网关归到同一条链路。
        self._session_id = str(uuid.uuid4())

    def _build_client(self) -> OpenAI:
        """创建 SDK 客户端。

        关闭 SDK 自带重试（max_retries=0）：连接测试要快速失败，
        业务层重试策略由我们自己控制，避免用户等太久。
        """
        default_headers: dict[str, str] = {}
        if _needs_opencode_session(self.config.base_url):
            default_headers[_OPENCODE_SESSION_HEADER] = self._session_id
        return OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.timeout,
            max_retries=0,
            default_headers=default_headers or None,
        )

    def _get_client(self) -> OpenAI:
        """懒加载并复用底层客户端（多线程下只创建一次）。"""
        client = self._client
        if client is not None:
            return client
        with self._client_lock:
            if self._client is None:
                self._client = self._build_client()
            return self._client

    def close(self) -> None:
        """关闭底层连接池；重复调用安全。"""
        with self._client_lock:
            client = self._client
            self._client = None
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - 关闭失败不应影响退出流程
                logger.debug("关闭 AI 客户端时出错", exc_info=True)

    def __enter__(self) -> AIClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def test_connection(self, *, task: str = "连接测试") -> ConnectionResult:
        """发一次最小请求验证 base_url / key / model 是否可用。

        不用 /models 接口做检测，因为不少兼容网关不实现它；
        直接发一次极短的 chat 请求最接近真实使用场景。
        """
        errors = self.config.validate()
        if errors:
            raise AIError("配置不完整：" + "；".join(errors), kind="config")

        client = self._get_client()
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
                # 给 thinking 模型留出额度，避免推理把输出额度吃光后返回空内容。
                max_tokens=1024,
            )
        except Exception as exc:  # noqa: BLE001 - 统一翻译成 AIError
            logger.warning("连接测试失败: %s", exc)
            error = translate_error(exc)
            self._record_call(
                task=task,
                json_mode=False,
                attempt=1,
                latency_ms=_elapsed_ms(started_at),
                ok=False,
                error=error,
            )
            raise error from exc

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        _log_usage(response, latency_ms, json_mode=False)
        try:
            _raise_if_truncated(response)
            content = _extract_content(response)
            if not content:
                raise AIError(
                    "服务返回了空内容",
                    "模型可能把 token 全用于思考，或该模型不支持 chat 接口。"
                    "请换一个模型 id，或检查 base_url 是否为 OpenAI 兼容地址。",
                    kind="format",
                )
        except AIError as exc:
            self._record_call(
                task=task,
                json_mode=False,
                attempt=1,
                latency_ms=latency_ms,
                ok=False,
                response=response,
                error=exc,
            )
            raise
        self._record_call(
            task=task,
            json_mode=False,
            attempt=1,
            latency_ms=latency_ms,
            ok=True,
            response=response,
        )
        return ConnectionResult(reply=content, model=self.config.model, latency_ms=latency_ms)

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        task: str = "",
    ) -> str:
        """通用补全接口，供测评/文章/查词复用。

        json_mode=True 时要求服务端返回 JSON 对象（OpenAI 的 response_format）。
        注意：部分兼容网关不支持该参数，业务层需要自己做兜底解析。
        `task` 只用于调用记录（例如“查词”“每周复盘”），不影响请求内容。

        对 429 / 超时 / 5xx 这类瞬时错误会做 1~2 次退避重试；格式错误和
        鉴权错误不会自动重试。
        """
        return self._complete_with_retry(
            messages,
            temperature=temperature,
            json_mode=json_mode,
            max_tokens=max_tokens,
            task=task,
        )

    def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task: str = "",
    ) -> str:
        """要求服务端返回 JSON；不支持 response_format 时降级并记住能力。

        部分兼容网关（尤其自建代理）会对 response_format 返回 400。
        降级后仍依赖业务层的 `json_utils.parse_json_object` 做本地兜底解析。
        同一 (base_url, model) 只要探测到一次不支持，后续请求直接走普通模式，
        不再白白多发一次失败请求。
        """
        key = (self.config.base_url, self.config.model)
        if _json_mode_is_unsupported(key):
            return self._complete_with_retry(
                messages,
                temperature=temperature,
                json_mode=False,
                max_tokens=max_tokens,
                task=task,
            )
        try:
            return self._complete_with_retry(
                messages,
                temperature=temperature,
                json_mode=True,
                max_tokens=max_tokens,
                task=task,
            )
        except AIError as exc:
            if not _json_mode_unsupported(exc):
                raise
            _mark_json_mode_unsupported(key)
            logger.info("服务端不支持 response_format，后续改用普通模式：%s", key)
            return self._complete_with_retry(
                messages,
                temperature=temperature,
                json_mode=False,
                max_tokens=max_tokens,
                task=task,
            )

    # ------------------------------------------------------------------ 内部
    def _complete_with_retry(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        json_mode: bool,
        max_tokens: int | None,
        task: str = "",
    ) -> str:
        retry_index = 0
        while True:
            try:
                return self._complete_once(
                    messages,
                    temperature=temperature,
                    json_mode=json_mode,
                    max_tokens=max_tokens,
                    task=task,
                    attempt=retry_index + 1,
                )
            except AIError as exc:
                if not exc.retryable or retry_index >= len(_RETRY_DELAYS):
                    raise
                delay = _RETRY_DELAYS[retry_index]
                retry_index += 1
                logger.info(
                    "AI 瞬时失败，%.1fs 后重试（%s/%s）：%s",
                    delay,
                    retry_index,
                    len(_RETRY_DELAYS),
                    exc.message,
                )
                time.sleep(delay)

    def _complete_once(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        json_mode: bool,
        max_tokens: int | None,
        task: str = "",
        attempt: int = 1,
    ) -> str:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        started_at = time.perf_counter()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI 调用失败: %s", exc)
            error = translate_error(exc)
            self._record_call(
                task=task,
                json_mode=json_mode,
                attempt=attempt,
                latency_ms=_elapsed_ms(started_at),
                ok=False,
                error=error,
            )
            raise error from exc

        latency_ms = _elapsed_ms(started_at)
        _log_usage(response, latency_ms, json_mode=json_mode)
        try:
            _raise_if_truncated(response)
            content = _extract_content(response)
            if not content:
                raise AIError(
                    "服务返回了空内容",
                    "模型可能把 token 全用于思考，或本次输出被截断。"
                    "请重试；若反复出现，请换一个模型 id 或调大该任务的最大输出。",
                    kind="format",
                )
        except AIError as exc:
            self._record_call(
                task=task,
                json_mode=json_mode,
                attempt=attempt,
                latency_ms=latency_ms,
                ok=False,
                response=response,
                error=exc,
            )
            raise
        self._record_call(
            task=task,
            json_mode=json_mode,
            attempt=attempt,
            latency_ms=latency_ms,
            ok=True,
            response=response,
        )
        return content

    def _record_call(
        self,
        *,
        task: str,
        json_mode: bool,
        attempt: int,
        latency_ms: int,
        ok: bool,
        response: Any = None,
        error: AIError | None = None,
    ) -> None:
        """写一条调用记录；任何异常都只进日志，绝不影响业务请求。"""
        recorder = self._recorder
        if recorder is None:
            return
        prompt_tokens, completion_tokens, total_tokens = _extract_usage(response)
        call = AICall(
            called_at=now_iso(),
            task=task,
            model=self.config.model,
            base_url=self.config.base_url,
            json_mode=json_mode,
            attempt=int(attempt),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=int(latency_ms),
            ok=ok,
            error_kind=error.kind if error is not None else "",
            error_message=(error.message if error is not None else "")[:300],
        )
        try:
            recorder.insert(call)
        except Exception:  # noqa: BLE001 - 统计失败不能影响 AI 业务
            logger.debug("记录 AI 调用失败", exc_info=True)


def _needs_opencode_session(base_url: str) -> bool:
    """判断 base_url 是否指向 opencode 网关（需要额外的会话头）。"""
    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == item or host.endswith("." + item) for item in _OPENCODE_HOSTS)


def _json_mode_is_unsupported(key: tuple[str, str]) -> bool:
    with _JSON_MODE_LOCK:
        return key in _JSON_MODE_UNSUPPORTED


def _mark_json_mode_unsupported(key: tuple[str, str]) -> None:
    with _JSON_MODE_LOCK:
        _JSON_MODE_UNSUPPORTED.add(key)


def _first_choice(response: Any) -> Any | None:
    try:
        choices = response.choices
    except AttributeError:
        return None
    if not choices:
        return None
    return choices[0]


def _raise_if_truncated(response: Any) -> None:
    """把“输出被截断”从模糊的 JSON 解析失败变成明确提示。"""
    choice = _first_choice(response)
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise AIError(
            "模型输出被截断（finish_reason=length）",
            "本次任务的输出额度不够，或模型把额度用在了推理上。"
            "请重试；若反复出现，请换一个更稳定的模型。",
            kind="format",
        )


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _extract_usage(response: Any) -> tuple[int | None, int | None, int | None]:
    """从响应里取 token 用量；网关不返回 usage 时三项都是 None。"""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None

    def _value(name: str) -> int | None:
        value = getattr(usage, name, None)
        return int(value) if isinstance(value, (int, float)) else None

    return _value("prompt_tokens"), _value("completion_tokens"), _value("total_tokens")


def _log_usage(response: Any, latency_ms: int, *, json_mode: bool) -> None:
    """记录 token 用量与耗时，便于评估哪类调用最贵。"""
    choice = _first_choice(response)
    finish_reason = getattr(choice, "finish_reason", None) if choice else None
    prompt_tokens, completion_tokens, total_tokens = _extract_usage(response)
    logger.info(
        "AI 调用完成 json=%s finish=%s prompt_tokens=%s completion_tokens=%s "
        "total_tokens=%s latency_ms=%s",
        json_mode,
        finish_reason or "-",
        prompt_tokens if prompt_tokens is not None else "-",
        completion_tokens if completion_tokens is not None else "-",
        total_tokens if total_tokens is not None else "-",
        latency_ms,
    )


def _json_mode_unsupported(exc: AIError) -> bool:
    """根据错误文案粗略判断“只是不支持 JSON 模式”，而不是鉴权/网络问题。"""
    text = f"{exc.message} {exc.hint}".lower()
    return any(hint in text for hint in ("response_format", "response format", "json_object"))


def _extract_content(response: Any) -> str:
    """兼容不同 SDK 返回结构，取出第一条回复的文本。"""
    choice = _first_choice(response)
    if choice is None:
        return ""
    try:
        message = choice.message
        return (message.content or "").strip()
    except (AttributeError, TypeError):
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
            "（例如 https://api.deepseek.com/v1）。"
            "若 base_url 指向 Anthropic 协议端点（路径含 /anthropic），"
            "本项目只支持 OpenAI 协议，请改用该服务的 OpenAI 兼容地址"
            "（例如 https://opencode.ai/inference/go/openai/v1）。",
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
