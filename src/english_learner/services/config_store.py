"""应用配置的读写：AI 服务地址、API Key、模型 id。

Java 对照：
- 本文件 ≈ 一个基于 JSON 文件实现的 ConfigurationService；
- AIConfig ≈ @ConfigurationProperties 绑定的配置对象（不可变）；
- ConfigStore ≈ 负责加载/保存配置的 Repository。

存放位置（不放在项目目录里，避免 API Key 被误提交到 Git）：
- Windows: %APPDATA%/EnglishLearner/config.json
- macOS:   ~/Library/Application Support/EnglishLearner/config.json
- 其他:    $XDG_CONFIG_HOME/english_learner/config.json 或 ~/.config/english_learner/config.json

安全说明：
- API Key 在 Windows 上用 DPAPI 加密后写入配置（见 `secret_store.py`），
  密文与当前 Windows 用户绑定；非 Windows 或加密失败时回退明文，文件权限
  在类 Unix 下收紧为 600；
- 旧版明文配置可以直接读取，下次保存时自动升级为密文。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .secret_store import SecretError, is_encrypted, protect, unprotect

APP_DIR_NAME = "EnglishLearner"
CONFIG_FILE_NAME = "config.json"
CONFIG_VERSION = 1


class ConfigError(RuntimeError):
    """配置文件损坏或内容不合法。"""


def config_dir() -> Path:
    """返回应用数据目录（不自动创建）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    base_dir = Path(xdg_home) if xdg_home else Path.home() / ".config"
    return base_dir / "english_learner"


def config_path() -> Path:
    """返回配置文件完整路径。"""
    return config_dir() / CONFIG_FILE_NAME


def normalize_base_url(url: str) -> str:
    """规范化服务地址。

    用户常见输入：
    - "https://api.deepseek.com/v1/"       → 去掉末尾斜杠
    - "https://api.deepseek.com/v1/chat/completions" → 去掉具体接口路径

    注意：不会自动补 "/v1"。不同服务商的根路径不同（DeepSeek 支持根路径，
    Ollama 则必须带 /v1），自动猜测反而容易出错，由预设或用户自己填写。
    """
    value = url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    return value


@dataclass(frozen=True)
class AIConfig:
    """一个 OpenAI 兼容服务的连接配置。"""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.7

    def normalized(self) -> AIConfig:
        """去掉首尾空白、规范 URL、把温度限制在 [0, 2]。"""
        try:
            temperature = float(self.temperature)
        except (TypeError, ValueError):
            temperature = 0.7
        return AIConfig(
            base_url=normalize_base_url(self.base_url),
            api_key=self.api_key.strip(),
            model=self.model.strip(),
            temperature=min(max(temperature, 0.0), 2.0),
        )

    def validate(self) -> list[str]:
        """返回错误信息列表，空列表表示合法。"""
        config = self.normalized()
        errors: list[str] = []
        if not config.base_url:
            errors.append("服务地址不能为空")
        elif not config.base_url.lower().startswith(("http://", "https://")):
            errors.append("服务地址必须以 http:// 或 https:// 开头")
        if not config.api_key:
            errors.append("API Key 不能为空")
        if not config.model:
            errors.append("模型 id 不能为空")
        return errors

    def masked_key(self) -> str:
        """给界面展示用的脱敏 Key。"""
        key = self.api_key.strip()
        if len(key) <= 8:
            return "*" * len(key) if key else "（未配置）"
        return f"{key[:4]}{'*' * 8}{key[-4:]}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AIConfig:
        if not isinstance(data, dict):
            raise ConfigError("配置内容格式不正确")
        try:
            return cls(
                base_url=str(data.get("base_url") or ""),
                api_key=str(data.get("api_key") or ""),
                model=str(data.get("model") or ""),
                temperature=float(data.get("temperature", 0.7)),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"配置内容格式不正确：{exc}") from exc


@dataclass(frozen=True)
class UiConfig:
    """界面偏好，和 AI 连接配置一起放在 config.json 的 `ui` 段里。

    `english_font` 是英文字体 key，留空表示“用户没选过”，默认值由界面层
    （`gui.fonts.DEFAULT_ENGLISH_FONT`）决定 —— services 层不依赖 PySide6，
    也不该知道有哪些可选字体。
    """

    english_font: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UiConfig:
        if not isinstance(data, dict):
            return cls()
        # 兼容旧字段 reading_font：早期版本只把字体作用在正文上。
        raw = data.get("english_font") or data.get("reading_font") or ""
        return cls(english_font=str(raw).strip().lower())


class ConfigStore:
    """JSON 配置文件仓库。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> AIConfig | None:
        """读取配置；文件不存在返回 None，内容损坏抛 ConfigError。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConfigError(f"读取配置文件失败：{exc}") from exc

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件不是合法 JSON：{exc}") from exc

        # 兼容两种结构：{"ai": {...}} 和直接 {...}。
        ai_data = data.get("ai") if isinstance(data, dict) else None
        if not isinstance(ai_data, dict):
            ai_data = data
        config = AIConfig.from_dict(ai_data).normalized()
        try:
            api_key = unprotect(config.api_key)
        except SecretError as exc:
            raise ConfigError(
                f"{exc}\n请点击「重新配置」重新填写 API Key，或删除配置文件后重启。"
            ) from exc
        if api_key != config.api_key:
            config = replace(config, api_key=api_key).normalized()

        errors = config.validate()
        if errors:
            raise ConfigError("配置文件缺少必要字段：" + "；".join(errors))
        return config

    def save(self, config: AIConfig) -> AIConfig:
        """保存配置（先写临时文件再替换，避免写到一半损坏）。"""
        normalized = config.normalized()
        errors = normalized.validate()
        if errors:
            raise ConfigError("；".join(errors))

        # 保留 ui 段：重新配置 AI 不应该把用户选的字体冲掉。
        ui_data = self._read_payload().get("ui")
        ai_data = normalized.to_dict()
        # 调用方通常传明文；若已经是密文（例如原样回写），不要二次加密。
        if not is_encrypted(normalized.api_key):
            ai_data["api_key"] = protect(normalized.api_key)
        payload: dict[str, Any] = {"version": CONFIG_VERSION, "ai": ai_data}
        if isinstance(ui_data, dict):
            payload["ui"] = ui_data
        self._write_payload(payload)
        return normalized

    def load_ui(self) -> UiConfig:
        """读取界面偏好；文件缺失或损坏一律回退默认值，不能因为偏好把启动搞挂。"""
        return UiConfig.from_dict(self._read_payload().get("ui", {}))

    def save_ui(self, ui: UiConfig) -> UiConfig:
        """只更新 `ui` 段，`ai` 段原样保留。"""
        payload = self._read_payload()
        payload.setdefault("version", CONFIG_VERSION)
        payload["ui"] = ui.to_dict()
        self._write_payload(payload)
        return ui

    # ------------------------------------------------------------------ 内部
    def _read_payload(self) -> dict[str, Any]:
        """读取原始 JSON（不做校验）：文件缺失、编码异常、内容损坏都返回空 dict。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_payload(self, payload: dict[str, Any]) -> None:
        """原子写入：先写 .tmp 再替换，避免写到一半留下半个 JSON。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def clear(self) -> None:
        """删除配置文件（用于“恢复初始配置”）。"""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
