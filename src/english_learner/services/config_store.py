"""应用配置的读写：AI 服务地址、API Key、模型 id。

支持保存多套 AI 配置（`ai_profiles` 列表 + `active_ai_id`），设置页可以
随时切换当前使用的那一套；`ai` 段始终镜像当前配置，兼容旧版读取路径。

Java 对照：
- 本文件 ≈ 一个基于 JSON 文件实现的 ConfigurationService；
- AIConfig ≈ @ConfigurationProperties 绑定的配置对象（不可变）；
- AIProfile ≈ 配置 + 展示名，类似 Profile 里的一组连接参数；
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
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .readability import ReadabilityThresholds
from .secret_store import SecretError, is_encrypted, protect, unprotect

APP_DIR_NAME = "EnglishLearner"
CONFIG_FILE_NAME = "config.json"
CONFIG_VERSION = 2

# 多 AI 配置的默认名称 / 旧版单配置迁移后的固定 id。
DEFAULT_PROFILE_NAME = "默认配置"
LEGACY_PROFILE_ID = "default"

# 每日复习目标：默认 20 个，大约 10~15 分钟；范围防止手滑写成 0 或几千。
DEFAULT_DAILY_REVIEW_LIMIT = 20
MIN_DAILY_REVIEW_LIMIT = 5
MAX_DAILY_REVIEW_LIMIT = 200

# 主窗口首次启动的默认尺寸；之后由 config.json 的 `window` 段覆盖。
DEFAULT_WINDOW_WIDTH = 1240
DEFAULT_WINDOW_HEIGHT = 800


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


def log_dir() -> Path:
    """日志目录（数据目录下的 logs/，不自动创建）。

    运行日志与数据库、配置文件放在一起，设置页和日志查看器共用这个入口。
    """
    return config_dir() / "logs"


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
    """一个 OpenAI 兼容服务的连接配置。

    `temperature` 只是“调用方没有显式指定时的默认值”；业务层每个任务
    （出题、讲评、文章等）都会传自己的温度，因此界面不再暴露该字段。
    """

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


def default_profile_name(config: AIConfig) -> str:
    """给没填名称的配置生成一个可读名字：优先用模型 id。"""
    model = config.normalized().model.strip()
    return model or DEFAULT_PROFILE_NAME


def new_profile_id() -> str:
    """生成配置 id；用随机串而不是序号，删除 / 重排后也不会撞车。"""
    return "p-" + uuid.uuid4().hex[:10]


@dataclass(frozen=True)
class AIProfile:
    """一套命名后的 AI 连接配置。

    `id` 在配置的生命周期内保持不变（编辑只改 name / config），
    `active_ai_id` 用它指认当前使用哪一套。
    """

    id: str
    name: str
    config: AIConfig

    def normalized(self) -> AIProfile:
        config = self.config.normalized()
        return AIProfile(
            id=self.id.strip(),
            name=self.name.strip() or default_profile_name(config),
            config=config,
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化成扁平结构；api_key 的加密由 ConfigStore 负责。"""
        data = self.config.to_dict()
        data["id"] = self.id
        data["name"] = self.name
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AIProfile:
        if not isinstance(data, dict):
            raise ConfigError("AI 配置内容格式不正确")
        profile_id = str(data.get("id") or "").strip()
        if not profile_id:
            raise ConfigError("AI 配置缺少 id")
        config = AIConfig.from_dict(data).normalized()
        name = str(data.get("name") or "").strip() or default_profile_name(config)
        return cls(id=profile_id, name=name, config=config)


@dataclass(frozen=True)
class AIProfiles:
    """一组 AI 配置及其当前选中项。

    空集合表示“用户还没配置过”；业务层必须先判断 `active()` 是否为 None。
    """

    profiles: tuple[AIProfile, ...] = ()
    active_id: str = ""

    def active(self) -> AIProfile | None:
        """当前配置；active_id 失效时回退到第一个，避免界面无配置可用。"""
        for profile in self.profiles:
            if profile.id == self.active_id:
                return profile
        return self.profiles[0] if self.profiles else None

    def find(self, profile_id: str) -> AIProfile | None:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None

    def with_profile(self, profile: AIProfile) -> AIProfiles:
        """按 id upsert：存在则替换，不存在则追加。"""
        items: list[AIProfile] = []
        replaced = False
        for existing in self.profiles:
            if existing.id == profile.id:
                items.append(profile)
                replaced = True
            else:
                items.append(existing)
        if not replaced:
            items.append(profile)
        active_id = self.active_id or profile.id
        return AIProfiles(tuple(items), active_id)

    def without(self, profile_id: str) -> AIProfiles:
        items = tuple(profile for profile in self.profiles if profile.id != profile_id)
        active_id = self.active_id
        if active_id == profile_id or not any(item.id == active_id for item in items):
            active_id = items[0].id if items else ""
        return AIProfiles(items, active_id)

    def with_active(self, profile_id: str) -> AIProfiles:
        if not any(profile.id == profile_id for profile in self.profiles):
            return self
        return AIProfiles(self.profiles, profile_id)


def _coerce_bool(value: Any) -> bool:
    """把 JSON 里的布尔偏好转成 bool。

    手写配置里常见 `"false"` 字符串，直接 `bool("false")` 会得到 True，
    所以字符串要按取值单独判断；其余类型走 Python 真值语义。
    """
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


@dataclass(frozen=True)
class UiConfig:
    """界面偏好，和 AI 连接配置一起放在 config.json 的 `ui` 段里。

    `english_font` 是英文字体 key，留空表示“用户没选过”，默认值由界面层
    （`gui.fonts.DEFAULT_ENGLISH_FONT`）决定 —— services 层不依赖 PySide6，
    也不该知道有哪些可选字体。

    旧版本曾把复习模式（`review_mode`）存在这里；现在复习卡由
    `services.review_plan` 按学习阶段自动编排，该字段已移除，
    读到旧配置时直接忽略，不做迁移。

    `prefetch_enabled`：打开文章后是否在后台批量预查生词并写入查词缓存。
    默认开启，让阅读时双击查词尽量不走网络；关闭后只在真正双击时调用 AI。

    `low_profile`：低调主题（深色 + 等宽）。开启后窗口标题、侧边栏品牌与导航
    文案都会中性化，避免在办公环境里一眼看出是英语学习工具。
    """

    english_font: str = ""
    prefetch_enabled: bool = True
    low_profile: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UiConfig:
        if not isinstance(data, dict):
            return cls()
        # 兼容旧字段 reading_font：早期版本只把字体作用在正文上。
        raw = data.get("english_font") or data.get("reading_font") or ""
        # JSON 里写 "false" 时 bool("false") 是 True，字符串要单独判断。
        return cls(
            english_font=str(raw).strip().lower(),
            prefetch_enabled=_coerce_bool(data.get("prefetch_enabled", True)),
            low_profile=_coerce_bool(data.get("low_profile", False)),
        )


@dataclass(frozen=True)
class WindowState:
    """主窗口几何快照，放在 config.json 的 `window` 段。

    `geometry` 是 `QMainWindow.saveGeometry()` 结果的 base64 文本。没有拆成
    `width/x/maximized` 这类可读字段，是因为 Qt 的几何坐标带窗口框架
    （标题栏、边框），而 `setGeometry()` 用的是客户区坐标；自己换算会每次
    启动偏移一个标题栏高度。`saveGeometry()/restoreGeometry()` 由 Qt 负责
    框架换算、最大化和多屏兜底（旧位置不在任何屏幕上时会自动挪回来）。

    空字符串表示“没有记录过”，界面层回退默认尺寸。services 层不依赖
    PySide6，因此这里只当成一段不透明文本保存。
    """

    geometry: str = ""

    def normalized(self) -> WindowState:
        raw = self.geometry if isinstance(self.geometry, str) else ""
        return WindowState(geometry=raw.strip())

    def to_dict(self) -> dict[str, Any]:
        return {"geometry": self.normalized().geometry}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WindowState:
        if not isinstance(data, dict):
            return cls()
        return cls(geometry=str(data.get("geometry") or "")).normalized()


@dataclass(frozen=True)
class RealTextConfig:
    """真实文章供给偏好，放在 config.json 的 `real_text` 段（M7.1）。

    `auto_enabled`：关掉后永不再自动供给；阅读页仍可手动「换回 AI 文章」。
    `last_decision_on`：保证每天只决策一次（成功或跳过都算已决策）。
    """

    auto_enabled: bool = True
    last_decision_on: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_enabled": bool(self.auto_enabled),
            "last_decision_on": str(self.last_decision_on or "").strip(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> RealTextConfig:
        if not isinstance(data, dict):
            return cls()
        raw_enabled = data.get("auto_enabled", True)
        if isinstance(raw_enabled, str):
            enabled = raw_enabled.strip().lower() in ("1", "true", "yes", "on")
        else:
            enabled = bool(raw_enabled)
        return cls(
            auto_enabled=enabled,
            last_decision_on=str(data.get("last_decision_on") or "").strip(),
        )


@dataclass(frozen=True)
class ReviewConfig:
    """复习偏好，放在 config.json 的 `review` 段。

    `daily_limit`：每日目标复习数。复习页只按「目标 - 今日已完成」取词，
    剩余到期词留在积压里，可以手动加练；避免一次面对几百个词直接放弃。

    `spelling_enabled`：是否要求默写英文。默认关闭，只做看词回忆——先保证
    「看到词能想起意思」的闭环，需要产出练习时再手动打开。
    """

    daily_limit: int = DEFAULT_DAILY_REVIEW_LIMIT
    spelling_enabled: bool = False

    def normalized(self) -> ReviewConfig:
        """把目标数限制在合理范围，否则回退默认值。"""
        try:
            limit = int(self.daily_limit)
        except (TypeError, ValueError):
            limit = DEFAULT_DAILY_REVIEW_LIMIT
        limit = min(max(limit, MIN_DAILY_REVIEW_LIMIT), MAX_DAILY_REVIEW_LIMIT)
        return ReviewConfig(daily_limit=limit, spelling_enabled=bool(self.spelling_enabled))

    def to_dict(self) -> dict[str, Any]:
        config = self.normalized()
        return {
            "daily_limit": config.daily_limit,
            "spelling_enabled": config.spelling_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewConfig:
        if not isinstance(data, dict):
            return cls()
        try:
            limit = int(data.get("daily_limit", DEFAULT_DAILY_REVIEW_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_DAILY_REVIEW_LIMIT
        raw_spelling = data.get("spelling_enabled", False)
        if isinstance(raw_spelling, str):
            # JSON 里写 "false" 时 bool("false") 是 True，字符串要单独判断。
            spelling = raw_spelling.strip().lower() in ("1", "true", "yes", "on")
        else:
            spelling = bool(raw_spelling)
        return cls(daily_limit=limit, spelling_enabled=spelling).normalized()


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

    def save(self, config: AIConfig, *, name: str = "") -> AIConfig:
        """保存配置并设为当前配置（先写临时文件再替换，避免写到一半损坏）。

        兼容旧调用：没有多配置列表时自动创建一条；已有列表时更新当前那一条，
        这样 `--reconfigure` 与首次配置都不会让列表和 `ai` 段不一致。
        """
        normalized = config.normalized()
        errors = normalized.validate()
        if errors:
            raise ConfigError("；".join(errors))

        profiles = self.load_profiles()
        active = profiles.active()
        if active is None:
            profile = AIProfile(
                id=new_profile_id(),
                name=name.strip() or default_profile_name(normalized),
                config=normalized,
            )
            profiles = AIProfiles((profile,), profile.id)
        else:
            profile = AIProfile(
                id=active.id,
                name=name.strip() or active.name,
                config=normalized,
            )
            profiles = profiles.with_profile(profile).with_active(profile.id)
        self.save_profiles(profiles)
        return normalized

    # -------------------------------------------------------------- 多套配置
    def load_profiles(self) -> AIProfiles:
        """读取全部 AI 配置；旧版单配置会自动派生为一条「默认配置」。

        列表里的坏条目（字段缺失、Key 无法解密）会被跳过，不能让一个坏配置
        拖垮整个设置页；当前配置的有效性仍由 `load()` 负责校验。
        """
        profiles = self._profiles_from_payload(self._read_payload())
        if profiles.profiles:
            return profiles

        try:
            config = self.load()
        except ConfigError:
            return AIProfiles()
        if config is None:
            return AIProfiles()
        profile = AIProfile(
            id=LEGACY_PROFILE_ID,
            name=default_profile_name(config),
            config=config,
        )
        return AIProfiles((profile,), profile.id)

    def save_profiles(self, profiles: AIProfiles) -> AIProfiles:
        """整体保存多套配置，并把当前配置镜像到 `ai` 段。"""
        if not profiles.profiles:
            raise ConfigError("至少需要保留一个 AI 配置")

        normalized: list[AIProfile] = []
        seen: set[str] = set()
        for profile in profiles.profiles:
            item = profile.normalized()
            if not item.id or item.id in seen:
                raise ConfigError("AI 配置 id 为空或重复")
            errors = item.config.validate()
            if errors:
                raise ConfigError(f"配置「{item.name}」不完整：" + "；".join(errors))
            seen.add(item.id)
            normalized.append(item)

        active_id = profiles.active_id
        if not any(item.id == active_id for item in normalized):
            active_id = normalized[0].id
        active = next(item for item in normalized if item.id == active_id)

        # 保留其它段（ui / readability 等）：切换 AI 不应该把用户偏好冲掉。
        payload = self._strip_legacy_ai_fields(self._read_payload())
        payload["version"] = CONFIG_VERSION
        payload["ai_profiles"] = [self._profile_payload(item) for item in normalized]
        payload["active_ai_id"] = active_id
        payload["ai"] = self._encrypted_ai_dict(active.config)
        self._write_payload(payload)
        return AIProfiles(tuple(normalized), active_id)

    def add_profile(self, config: AIConfig, *, name: str = "") -> AIProfiles:
        """新增一套配置并设为当前配置。"""
        normalized = config.normalized()
        errors = normalized.validate()
        if errors:
            raise ConfigError("；".join(errors))
        profile = AIProfile(
            id=new_profile_id(),
            name=name.strip() or default_profile_name(normalized),
            config=normalized,
        )
        profiles = self.load_profiles()
        return self.save_profiles(profiles.with_profile(profile).with_active(profile.id))

    def update_profile(self, profile_id: str, config: AIConfig, *, name: str = "") -> AIProfiles:
        """修改一套已有配置；名称留空时保留原名。"""
        profiles = self.load_profiles()
        current = profiles.find(profile_id)
        if current is None:
            raise ConfigError("要修改的 AI 配置不存在")
        profile = AIProfile(
            id=current.id,
            name=name.strip() or current.name,
            config=config.normalized(),
        )
        return self.save_profiles(profiles.with_profile(profile))

    def delete_profile(self, profile_id: str) -> AIProfiles:
        """删除一套配置；至少保留一套，删除当前项时自动切到第一套。"""
        profiles = self.load_profiles()
        if profiles.find(profile_id) is None:
            raise ConfigError("要删除的 AI 配置不存在")
        if len(profiles.profiles) <= 1:
            raise ConfigError("至少需要保留一个 AI 配置")
        return self.save_profiles(profiles.without(profile_id))

    def activate_profile(self, profile_id: str) -> AIProfiles:
        """切换当前配置。"""
        profiles = self.load_profiles()
        if profiles.find(profile_id) is None:
            raise ConfigError("要切换的 AI 配置不存在")
        return self.save_profiles(profiles.with_active(profile_id))

    def load_ui(self) -> UiConfig:
        """读取界面偏好；文件缺失或损坏一律回退默认值，不能因为偏好把启动搞挂。"""
        return UiConfig.from_dict(self._read_payload().get("ui", {}))

    def save_ui(self, ui: UiConfig) -> UiConfig:
        """只更新 `ui` 段，其它段原样保留。"""
        payload = self._strip_legacy_ai_fields(self._read_payload())
        payload.setdefault("version", CONFIG_VERSION)
        payload["ui"] = ui.to_dict()
        self._write_payload(payload)
        return ui

    def load_window_state(self) -> WindowState:
        """读取主窗口几何；文件缺失或字段异常一律回退默认尺寸，不能挡住启动。"""
        return WindowState.from_dict(self._read_payload().get("window", {}))

    def save_window_state(self, state: WindowState) -> WindowState:
        """只更新 `window` 段，其它段原样保留（和 `save_ui` 一样）。"""
        normalized = state.normalized()
        payload = self._strip_legacy_ai_fields(self._read_payload())
        payload.setdefault("version", CONFIG_VERSION)
        payload["window"] = normalized.to_dict()
        self._write_payload(payload)
        return normalized

    def load_review(self) -> ReviewConfig:
        """读取复习偏好；文件缺失或格式损坏一律回退默认值。"""
        return ReviewConfig.from_dict(self._read_payload().get("review", {}))

    def save_review(self, config: ReviewConfig) -> ReviewConfig:
        """只更新 `review` 段，与 `save_ui` 一样保留其它配置。"""
        normalized = config.normalized()
        payload = self._strip_legacy_ai_fields(self._read_payload())
        payload.setdefault("version", CONFIG_VERSION)
        payload["review"] = normalized.to_dict()
        self._write_payload(payload)
        return normalized

    def load_real_text(self) -> RealTextConfig:
        """读取真实文章供给偏好；文件缺失或格式损坏一律回退默认值。"""
        return RealTextConfig.from_dict(self._read_payload().get("real_text", {}))

    def save_real_text(self, config: RealTextConfig) -> RealTextConfig:
        """只更新 `real_text` 段，与 `save_ui` 一样保留其它配置。"""
        payload = self._strip_legacy_ai_fields(self._read_payload())
        payload.setdefault("version", CONFIG_VERSION)
        payload["real_text"] = config.to_dict()
        self._write_payload(payload)
        return config

    def load_readability(self) -> ReadabilityThresholds:
        """读取本地可读性阈值；缺失或非法一律回退默认值。"""
        return ReadabilityThresholds.from_dict(self._read_payload().get("readability", {}))

    def save_readability(self, thresholds: ReadabilityThresholds) -> ReadabilityThresholds:
        """只更新 `readability` 段，与 `save_ui` 一样保留其它配置。"""
        payload = self._strip_legacy_ai_fields(self._read_payload())
        payload.setdefault("version", CONFIG_VERSION)
        payload["readability"] = thresholds.to_dict()
        self._write_payload(payload)
        return thresholds

    # ------------------------------------------------------------------ 内部
    _LEGACY_AI_KEYS: tuple[str, ...] = ("base_url", "api_key", "model", "temperature")

    @classmethod
    def _encrypted_ai_dict(cls, config: AIConfig) -> dict[str, Any]:
        """序列化一套配置；调用方通常传明文，已经是密文时不要二次加密。"""
        data = config.normalized().to_dict()
        if not is_encrypted(str(data.get("api_key") or "")):
            data["api_key"] = protect(str(data.get("api_key") or ""))
        return data

    @classmethod
    def _profile_payload(cls, profile: AIProfile) -> dict[str, Any]:
        data = cls._encrypted_ai_dict(profile.config)
        data["id"] = profile.id
        data["name"] = profile.name
        return data

    @classmethod
    def _profile_from_payload(cls, data: Any) -> AIProfile | None:
        """解析一条配置；坏条目返回 None，由调用方跳过。"""
        try:
            profile = AIProfile.from_dict(data)
            api_key = unprotect(profile.config.api_key)
        except (ConfigError, SecretError):
            return None
        if api_key != profile.config.api_key:
            profile = replace(
                profile,
                config=replace(profile.config, api_key=api_key).normalized(),
            )
        if profile.config.validate():
            return None
        return profile

    @classmethod
    def _profiles_from_payload(cls, payload: dict[str, Any]) -> AIProfiles:
        """只解析 `ai_profiles` 段；没有该段时返回空集合（由调用方回退旧格式）。"""
        raw = payload.get("ai_profiles")
        if not isinstance(raw, list):
            return AIProfiles()

        items: list[AIProfile] = []
        seen: set[str] = set()
        for entry in raw:
            profile = cls._profile_from_payload(entry)
            if profile is None or profile.id in seen:
                continue
            seen.add(profile.id)
            items.append(profile)
        if not items:
            return AIProfiles()

        active_id = str(payload.get("active_ai_id") or "").strip()
        if not any(item.id == active_id for item in items):
            active_id = items[0].id
        return AIProfiles(tuple(items), active_id)

    @classmethod
    def _strip_legacy_ai_fields(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """把早期“扁平格式”的顶层 AI 字段迁移到 `ai` 段后删除。

        `load()` 兼容顶层直接写 base_url/api_key/... 的旧文件，但 `save_ui()` /
        `save_readability()` 只改自己那一段；如果直接删掉顶层字段，旧配置的 AI
        信息就丢了，下次启动会变成“配置缺失”。所以这里先迁移（顺手把明文 Key
        加密），再删顶层键。已有 `ai` 段时只做清理，不覆盖。
        """
        legacy = {key: payload[key] for key in cls._LEGACY_AI_KEYS if key in payload}
        if legacy and not isinstance(payload.get("ai"), dict):
            api_key = legacy.get("api_key")
            if isinstance(api_key, str) and api_key and not is_encrypted(api_key):
                legacy["api_key"] = protect(api_key)
            payload["ai"] = legacy
        for legacy_key in cls._LEGACY_AI_KEYS:
            payload.pop(legacy_key, None)
        return payload

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
