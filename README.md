# Daily English · 每日英语

一个本地运行的 AI 英语学习桌面应用：每天读一篇略高于当前水平的英文文章，
随手标注生词，再用间隔重复把它们记住。

- GUI：PySide6
- AI：任何 OpenAI 兼容接口（DeepSeek / Kimi / 通义 / 智谱 / 硅基流动 / OpenRouter / Ollama）
- 数据：SQLite + 本地 JSON 配置，全部留在本机
- 当前进度：**M0（配置向导 + 连接测试 + 主窗口骨架）**

产品方向见 [`docs/english_learner/PRODUCT.md`](docs/english_learner/PRODUCT.md)，
开发进度与下一步任务见 [`docs/english_learner/PROGRESS.md`](docs/english_learner/PROGRESS.md)。

## 核心闭环

```
首次启动：配置 AI → 真实连接测试 → 水平测评 → 进入首页
每天：打开 → 今日文章 → 阅读划词 → 生词入本 → 复习到期词 → 打卡
```

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖（含开发依赖）
uv sync

# 2. 启动（首次强制进入配置向导）
uv run english-learner

# 等价写法
uv run python -m english_learner

# 3. 已有配置时强制重新配置
uv run english-learner --reconfigure
```

首次启动必须填写服务地址、API Key、模型 id，并通过一次**真实请求**的连接测试，
才能进入主窗口。常见服务商已内置预设，通常只需要粘一个 Key。

## 功能进度

| 模块 | 状态 |
|---|---|
| AI 配置向导 / 连接测试 / 主窗口导航 | ✅ M0 |
| 水平测评（固定题库 + AI 评分，CEFR 定级） | M1 |
| 每日文章生成（结构化 JSON、当天缓存） | M1 |
| 阅读器 + 双击划词 + 语境释义 | M1 |
| 生词本、正文高亮持久化 | M1/M2 |
| SM-2 间隔重复复习 | M2 |
| 首页真实统计、数据导出、打包 exe | M2/M3 |

## 配置与数据位置

不在仓库内，避免 API Key 与学习数据被误提交：

| 平台 | 目录 |
|---|---|
| Windows | `%APPDATA%\EnglishLearner\` |
| macOS | `~/Library/Application Support/EnglishLearner/` |
| Linux | `~/.config/english_learner/` |

- `config.json`：AI 服务地址、Key、模型 id、温度；
- `logs/app.log`：运行日志（轮转，最多 3 份）；
- `data.db`：学习数据（M1 起）。

> M0 的 Key 以明文 JSON 保存，后续会换成 Windows DPAPI / 系统 keyring。

## 代码结构

```
src/english_learner/
├── __init__.py             版本号与分层约定
├── __main__.py             支持 python -m english_learner
├── app.py                  入口：日志、QApplication、首次配置判断
├── services/               业务层，纯 Python，可独立单测
│   ├── ai_client.py        OpenAI 兼容客户端 + 错误中文化
│   ├── config_store.py     AIConfig 与 JSON 配置仓库
│   └── provider_presets.py 常见服务商预设
└── gui/                    PySide6 视图层
    ├── theme.py            全局 QSS
    ├── workers.py          TaskWorker(QThread)：AI 调用不得阻塞 UI
    ├── onboarding.py       三步配置向导
    ├── main_window.py      侧边导航 + 页面栈
    └── pages/              今日 / 阅读 / 生词本 / 复习 / 设置

tests/                      配置、AI 客户端、GUI 冒烟测试
docs/english_learner/       产品方向、开发进度与 M1 交接清单
```

分层原则：`gui/` 只管界面，`services/` 不 import PySide6；所有网络调用都在
`TaskWorker` 子线程执行。

## 开发命令

```bash
uv run pytest -q                              # 单元测试
uv run ruff check src/english_learner tests   # 静态检查
uv run ruff format src/english_learner tests  # 格式化
```

## 接入自定义 AI 服务

只要服务商兼容 OpenAI Chat Completions 协议，就能用。配置项：

| 字段 | 说明 |
|---|---|
| `base_url` | 一般以 `/v1` 结尾；Ollama 为 `http://localhost:11434/v1` |
| `api_key` | 本地 Ollama 可随便填（如 `ollama`） |
| `model` | 以服务商控制台为准，例如 `deepseek-chat` |
| `temperature` | 0 更稳定，1 更有创造性 |

连接测试会发送一句极短的 chat 请求，用于同时验证地址、Key 和模型权限。

## 与 Java / Maven 对照

| Java / Maven | Python / uv |
|---|---|
| `pom.xml` | `pyproject.toml` |
| `src/main/java` | `src/english_learner` |
| `src/test/java` | `tests/` |
| `mvn dependency:resolve` | `uv sync` |
| `mvn test` | `uv run pytest` |
| `mvn package` | `uv build` |
| `mvn checkstyle:check` | `uv run ruff check .` |
| `@ConfigurationProperties` | `@dataclass` + `ConfigStore` |
| `FeignClient` | `services/ai_client.py` |
| `Handler` / 回调 | Qt Signal / Slot |

## PyCharm

打开项目后，PyCharm 通常会自动识别 `.venv`。如果没有：
`Settings → Project: py-learn → Python Interpreter → Add Local Interpreter → Existing →
选择 .venv\Scripts\python.exe`。

之后可以右键运行 `src/english_learner/app.py`，或在 Terminal 执行
`uv run english-learner`。
