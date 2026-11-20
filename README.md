# Daily English · 每日英语

一个本地运行的 AI 英语学习桌面应用：每天读一篇略高于当前水平的英文文章，
随手标注生词，再用间隔重复把它们记住。

- GUI：PySide6
- AI：任何 OpenAI 兼容接口（DeepSeek / Kimi / 通义 / 智谱 / 硅基流动 / OpenRouter / Ollama）
- 数据：SQLite + 本地 JSON 配置，全部留在本机
- 当前进度：**M3.15（AI 调用记录与日志查看；此前含本地后验、自适应出题、盲评、每周复盘）**

产品方向见 [`docs/english_learner/PRODUCT.md`](docs/english_learner/PRODUCT.md)，
当前进度与下一步任务见 [`docs/english_learner/PROGRESS.md`](docs/english_learner/PROGRESS.md)；
架构与踩坑说明见 [`ARCHITECTURE.md`](docs/english_learner/ARCHITECTURE.md)，
已知问题见 [`TECH_DEBT.md`](docs/english_learner/TECH_DEBT.md)。

## 核心闭环

```
首次启动：配置 AI → 真实连接测试 → 水平测评 → 进入首页
每天：打开 → 今日文章 → 阅读划词 → 阅读完成（答题检测）→ 生词入本 → 复习到期词 → 打卡
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
| 水平测评（固定题库 + AI 评分，CEFR 定级） | ✅ M1 |
| 每日文章生成（结构化 JSON、当天缓存；换一篇支持提示词并携带原文上下文） | ✅ M1 / M3.5 |
| 阅读器 + 双击划词 + 语境释义（正文全宽 + 悬浮释义卡） | ✅ M1 / M3.6 |
| 阅读反馈调难度（AI 重新定级 + 换一篇） | ✅ M1.5 |
| 阅读完成检测（两轮自适应、边界题、英译中产出题、盲评 + 本地后验、置信度、选项随机，支持全屏） | ✅ M3.11 / M3.17 |
| 每周复盘（AI 汇总检测/生词/复习，用户确认后改级） | ✅ M3.11 |
| 生词本列表、筛选、搜索、排序、编辑释义、批量删除、正文高亮持久化 | ✅ M2/M3 |
| SM-2 间隔重复复习（四档反馈、复习历史落库） | ✅ M2 |
| 复习拼写回忆（看释义输入英文、本地判分与建议评分） | ✅ M3.16 |
| 复习卡片自动编排（按学习阶段自动出看词 / 拼写 / 看词+拼写，模式穿插） | ✅ M3.18 |
| 学习者记忆 + 上周计划回流（本地派生摘要注入 AI 调用） | ✅ M5.2 |
| 本地可测 i+1（生词比例 / 句长 / 目标词覆盖，阅读页展示） | ✅ M5.3 |
| 回译练习（看中文写英文，AI 批量给最小修改、原因与 0/2 分） | ✅ M5.4 |
| 学习效果看板（首次通过率 / 回炉率 / 计划完成率 / 重试率 / 得分趋势，纯本地） | ✅ M5.5 |
| 首页待复习按到期日统计、复习入口 | ✅ M2 |
| 数据导出（JSON 快照 / CSV 分表） | ✅ M3 |
| API Key 加密（Windows DPAPI）、请求可取消、错误分类 | ✅ M3 |
| AI 调用记录（token / 耗时 / 错误落库，设置页看今日与累计用量、调用明细） | ✅ M3.15 |
| 应用内日志查看器（轮转文件、关键字筛选、自动刷新） | ✅ M3.15 |
| 生词按原形入库、记录文章词形、AI 记忆方法 | ✅ M3.5 |
| 复习「忘记 / 模糊」原因收集 + AI 针对性建议 | ✅ M3.5 |
| 生词本回显复习求助（用户输入 + AI 帮记，最近 5 条） | ✅ M3.10 |
| 查词与生词本分离（双击只查词，手动加入才入本） | ✅ M3.7 |
| 深色模式、安装器 / 自动更新 | 可选 M4 |

## 配置与数据位置

不在仓库内，避免 API Key 与学习数据被误提交：

| 平台 | 目录 |
|---|---|
| Windows | `%APPDATA%\EnglishLearner\` |
| macOS | `~/Library/Application Support/EnglishLearner/` |
| Linux | `~/.config/english_learner/` |

- `config.json`：AI 服务地址、Key、模型 id、温度；
- `logs/app.log`：运行日志（每份 1 MB 轮转，另保留 3 个备份）；
- `data.db`：学习数据（文章、生词、高亮、统计、阅读检测、AI 调用记录）；
- `backups/data-YYYYMMDD.db`：每次启动自动生成，保留最近 7 份，可直接拷走。

> API Key 在 Windows 上使用系统 DPAPI 加密保存（与当前用户绑定）；其他平台
> 回退明文并收紧文件权限。设置页可导出 JSON / CSV 备份学习数据。

换电脑：在「设置 → 数据导出与备份」点「导出数据库」生成 `.db` 快照，
到另一台电脑点「导入数据库」选择它。校验通过后可选择「立即重启」（自动重启
应用并启用新数据）或「回退」（放弃本次导入、数据保持原样）；重启替换前会
自动把本机数据备份到 `backups/`。API Key 不随数据库迁移，新机器需要重新填写
自己的 Key。

## 代码结构

```
src/english_learner/
├── __init__.py             版本号与分层约定
├── __main__.py             支持 python -m english_learner
├── app.py                  入口：日志、QApplication、首次配置与首次测评
├── models/                 领域 dataclass（Profile / Article / Word / Highlight ...）
├── storage/                SQLite 连接、建表迁移、纯 SQL 仓库（不依赖 PySide6）
├── services/               业务层，纯 Python，可独立单测
│   ├── ai_client.py        OpenAI 兼容客户端 + 错误中文化 + JSON 模式降级
│   ├── app_context.py      配置 + 数据库 + 各服务的组装点
│   ├── config_store.py     AIConfig 与 JSON 配置仓库
│   ├── prompts.py          全部提示词（带版本号）
│   ├── assessment_*.py     固定题库、本地计分、AI 评分、手动选级
│   ├── article_service.py  每日文章生成、校验、缓存与换一篇
│   ├── dictionary_service.py 划词查词缓存、上下文句子、加入生词本
│   ├── level_feedback_service.py 阅读反馈重新定级 + 软约束重写文章
│   ├── reading_quiz_service.py 阅读检测：两轮自适应、盲评、本地后验与调级
│   ├── level_estimator.py      纯函数等级后验估计器
│   ├── quiz_calibration.py     难度/技能正确率统计
│   ├── weekly_review_service.py 每周复盘
│   ├── srs.py              简化 SM-2 调度 + 复习服务（纯本地，不依赖 AI）
│   ├── recall.py           拼写回忆判分与挖空题面（纯函数，不依赖 AI）
│   ├── review_help_service.py 复习“忘记/模糊”求助：AI 建议 + 记忆方法
│   ├── secret_store.py     API Key 的 Windows DPAPI 加解密
│   ├── log_files.py        日志尾部读取、轮转排序与关键字筛选
│   └── export_service.py   JSON / CSV 数据导出（含 ai_calls）
└── gui/                    PySide6 视图层
    ├── fonts.py            内置英文字体注册与字体链
    ├── theme.py            全局 QSS
    ├── workers.py          TaskWorker(QThread)：AI 调用不得阻塞 UI，支持协作式取消
    ├── onboarding.py       三步配置向导
    ├── assessment_dialog.py 摸底测评弹窗
    ├── level_feedback_dialog.py 阅读感受/调整难度弹窗
    ├── reading_quiz_dialog.py 阅读检测弹窗（两轮答题 → 讲评/置信度）
    ├── weekly_review_dialog.py 每周复盘弹窗
    ├── review_help_dialog.py 复习求助弹窗（原因 → AI 建议）
    ├── ai_usage_dialog.py   AI 调用记录与 token 统计
    ├── log_viewer_dialog.py 应用内运行日志查看器
    ├── main_window.py      侧边导航 + 页面栈
    ├── widgets/            阅读器正文视图、悬浮释义卡
    └── pages/              今日 / 阅读 / 生词本 / 复习 / 设置（后两者 M2 已接真实数据）

run_app.py                  根目录启动入口（PyCharm 右键运行）
tests/                      存储、JSON、测评、文章、阅读器、阅读检测、等级估计、每周复盘、AI 调用记录、日志读取、复习、生词本、导出与 GUI 冒烟测试
docs/english_learner/       产品方向、开发进度与后续交接清单
```

分层原则：`gui/` 只管界面，`services/` 与 `storage/` 不 import PySide6；
所有网络调用都在 `TaskWorker` 子线程执行。

## 开发命令

```bash
uv run pytest -q                              # 单元测试
uv run ruff check src/english_learner tests   # 静态检查
uv run ruff format src/english_learner tests  # 格式化

# 打包成单文件 exe → dist/DailyEnglish.exe（约 61MB，无需安装 Python）
uv run pyinstaller --noconfirm --clean DailyEnglish.spec
```

打包产物是自包含的：解释器、PySide6、内置字体全部塞进一个 exe，
配置和日志仍然读写 `%APPDATA%\EnglishLearner\`。首次启动会解压到临时目录，
比直接跑源码慢 3~5 秒，属正常现象。

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

## 在 PyCharm 里运行

1. 确认解释器：`Settings → Project: py-learn → Python Interpreter` 选择
   `.venv\Scripts\python.exe`；没有就 Add Local Interpreter → Existing。
2. **推荐**：打开根目录的 `run_app.py`，点编辑器左侧绿色三角 → Run。
3. 或者在 `Run → Edit Configurations` 新建 Python 配置：
   - 运行方式选 **Module name**（不是 Script path），填 `english_learner`；
   - Working directory 填项目根目录 `D:\py-space\py-learn`；
   - Interpreter 选 `.venv\Scripts\python.exe`；
   - 需要重新配置时，Parameters 填 `--reconfigure`。
4. 也可以直接用 PyCharm 底部 Terminal：`uv run english-learner`。

> **不要**直接右键运行 `src/english_learner/app.py`：src 布局下会破坏包内相对导入，
> 报 `attempted relative import with no known parent package`。
