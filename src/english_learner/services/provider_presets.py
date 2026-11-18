"""常见 OpenAI 兼容服务商的预设值。

只作为界面上的快捷填充，用户可以随意修改。
模型 id 会随时间变化，这里给的是“能跑通的最小示例”，不保证长期有效。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    name: str
    base_url: str
    model: str
    note: str = ""


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        note="国内直连，性价比高",
    ),
    ProviderPreset(
        key="moonshot",
        name="Kimi（Moonshot）",
        base_url="https://api.moonshot.cn/v1",
        model="moonshot-v1-8k",
        note="长文本能力好",
    ),
    ProviderPreset(
        key="dashscope",
        name="通义千问（阿里云）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        note="OpenAI 兼容模式",
    ),
    ProviderPreset(
        key="zhipu",
        name="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4-flash",
        note="有免费额度",
    ),
    ProviderPreset(
        key="siliconflow",
        name="硅基流动",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen2.5-7B-Instruct",
        note="聚合多种开源模型",
    ),
    ProviderPreset(
        key="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-4o-mini",
        note="需要外网",
    ),
    ProviderPreset(
        key="ollama",
        name="本地 Ollama",
        base_url="http://localhost:11434/v1",
        model="qwen2.5:7b",
        note="API Key 随便填（如 ollama）",
    ),
)


def find_preset(key: str) -> ProviderPreset | None:
    for preset in PRESETS:
        if preset.key == key:
            return preset
    return None
