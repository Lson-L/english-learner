# py-learn 项目说明（给 Claude 的工程上下文）

## 项目是什么

`py-learn` 现在**只包含一个应用**：Daily English（每日英语），一个本地运行的
AI 英语学习桌面应用。

- 目录：`src/english_learner/`
- GUI：PySide6；AI：OpenAI 兼容接口；数据：SQLite + 本地 JSON 配置
- 产品闭环：配置 AI → 水平测评 → 每日文章 → 阅读划词 → 间隔重复复习

## 常用命令

```bash
uv sync                                      # 安装全部依赖（PySide6 是核心依赖）
uv run english-learner                       # 启动 GUI
uv run python -m english_learner             # 等价
uv run english-learner --reconfigure         # 强制重新配置 AI
uv run pytest -q
uv run ruff check src/english_learner tests
uv run ruff format src/english_learner tests
```

- 配置 / 日志 /（M1 起）数据库都在 `%APPDATA%\EnglishLearner\`，**不在仓库内**。
- 想重演首次启动：删除 `%APPDATA%\EnglishLearner\config.json`，或加 `--reconfigure`。

## 文档索引（改代码前先读）

- `docs/english_learner/PRODUCT.md`：产品定位、MVP 范围、已确认决策；
- `docs/english_learner/PROGRESS.md`：**当前进度、各里程碑实现细节、M4 可选任务、
  已知问题、下次开工指令**。每完成一个里程碑必须更新它。

## 硬性约定

1. **分层**：`gui/` 只放 PySide6 视图；`services/` 是纯 Python，**禁止 import PySide6**，
   保证可单测；`storage/` 只写 SQL；`models/` 放 dataclass。
2. **线程**：所有 AI / 网络调用必须放进 `gui.workers.TaskWorker`（QThread），
   绝不能在 UI 线程直接调用。调用方持有 worker 引用。
3. **配置状态机**：任何字段改动都会让“连接测试通过”失效；未测试通过不允许进入下一步。
4. **AI 客户端**：只支持 OpenAI 兼容协议 + 自定义 `base_url`；异常统一翻译成
   `AIError`（中文 message + hint），不要把 SDK 原始异常抛给界面。
5. **数据访问**：SQLite 标准库 `sqlite3` + 手写 SQL，不引入 ORM。
6. **缓存优先**：查词释义、每日文章必须落库缓存，重复操作不再消耗 token。
7. **代码风格**：中文注释 / docstring；必要时用 Java 概念做类比；作者署名 `lson`；
   `@dataclass`；行宽 100，ruff 必须通过。
8. **页面导航**：`main_window.py` 中页面栈顺序与侧边栏按钮索引硬绑定
   （今日 0 / 阅读 1 / 生词本 2 / 复习 3 / 设置 4），加页面两处一起改。
9. **字体**：所有英文（界面、正文、测评、释义卡）的外观只由 `gui/fonts.py` 的
   字体链 + 全局 QSS 的 `*` 规则决定，新控件**不要**单独设 `font-family`
   （中文靠字体链回退到雅黑）。唯一例外是 QTextDocument 类控件（文章正文），
   必须调 `ArticleView.set_english_font(key)`。可选字体见 `ENGLISH_FONT_CHOICES`。

## 环境注意

- Python 3.13，Windows 10。
- `openai` SDK 是 **3.55**（不是 1.x），内部依赖 `httpx2`；测试里构造 HTTP 响应
  用 `import httpx2`。
- offscreen GUI 测试（`QT_QPA_PLATFORM=offscreen`）没有字体库，截图里文字是方框，
  属于环境限制；它只用于验证控件能构造。
- 用户未明确要求时，不要执行耗时的打包/构建；Python 改动以 `pytest` + `ruff` 为准。

## 当前状态

- **M0 / M1 / M1.5 / M2 / M3 核心项完成**（2026-11-19）：配置、测评、每日文章、
  阅读划词、生词本、SM-2 复习、数据导出、DPAPI Key 加密、可取消请求全部可用；
  复习与生词本完全本地化，不依赖 AI。
- **剩余为可选 M4**（深色模式、安装器 / 自动更新）与真实环境调优。任务说明与
  已知技术债见 `docs/english_learner/PROGRESS.md` 第 10、11 节。
