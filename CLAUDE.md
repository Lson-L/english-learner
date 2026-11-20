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

## 会话开工协议

当用户说「完成本次开工计划」「开工」「继续 M6」或类似短句时，**不要要求用户再贴长指令**：

1. 读 `docs/english_learner/PROGRESS.md` 看板与 `docs/english_learner/AI_TASKS.md`；
2. 取 AI_TASKS 中第一个未完成里程碑，按任务卡的改动点与验收执行；
3. 默认一次只做一个里程碑：完成、跑测试、汇报后停下，等用户说「继续」再做下一个；
4. 收尾更新 PROGRESS 看板，并在 HISTORY.md 追加交付记录；
5. 任务卡与代码现状不符、或需要扩大范围时，先停下说明差异，不要静默改计划。

## 文档索引（按需阅读，避免污染上下文）

- `docs/english_learner/PROGRESS.md`：**每个会话唯一必读**，当前看板（约 150 行：
  里程碑状态、环境、快速开始、M4/M5、下次开工指令）；
- `docs/english_learner/ARCHITECTURE.md`：**按需**。文件地图 + 关键实现与踩坑说明；
  改 AI 客户端、存储、阅读检测、导出等模块前定向读对应小节；
- `docs/english_learner/TECH_DEBT.md`：**按需**。已知问题与技术债完整表格；
- `docs/english_learner/AI_TASKS.md`：**按需**。AI 学习闭环与等级体验排期
  （M5.0~M5.6、M6.4~M6.7 任务卡、依赖、验收、schema v8 清单），开发 / 排期前读，
  **执行以本文为准**；
- `docs/english_learner/HISTORY.md`：**默认不读**。已完成里程碑的交付记录、
  旧任务书与验证记录，只在排查历史决策时检索；
- `docs/english_learner/PRODUCT.md`：产品定位、MVP 范围、已确认决策。

里程碑收尾规则：更新 `PROGRESS.md` 看板（只放当前状态），细节与验证记录追加到
`HISTORY.md`；不要把长篇实现说明堆回 PROGRESS。

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
10. **AI 调用记录**：每次 AI 调用都要带 `task` 标签（`complete/complete_json(..., task="查词")`）；
    记录统一在 `AIClient` 内写 `ai_calls`，业务层不要自己插表。表里只存
    token/耗时/错误等元数据，**禁止**写入 prompt 或模型回复全文。

## 环境注意

- Python 3.13，Windows 10。
- `openai` SDK 是 **3.55**（不是 1.x），内部依赖 `httpx2`；测试里构造 HTTP 响应
  用 `import httpx2`。
- offscreen GUI 测试（`QT_QPA_PLATFORM=offscreen`）没有字体库，截图里文字是方框，
  属于环境限制；它只用于验证控件能构造。
- 用户未明确要求时，不要执行耗时的打包/构建；Python 改动以 `pytest` + `ruff` 为准。

## 当前状态

- **M0 / M1 / M1.5 / M2 / M3 核心项 / M3.5 ~ M3.15 完成**（2026-11-19）：
  配置、测评、每日文章、阅读划词（正文全宽 + 双击悬浮释义卡）、阅读完成检测
  （混合难度选择题 + 英文短答、本地判分、逐题讲评、错因归因、连续证据调级）、
  生词本（双击只查词、手动加入才入本）、SM-2 复习、数据导出、DPAPI Key 加密、
  可取消请求、生词原形与 AI 记忆方法、复习求助与生词本求助记录回显全部可用；
  复习与生词本主体本地化；阅读检测已升级为两轮自适应 + 盲评 + 本地后验/置信度 +
  双产出题，并新增每周复盘（用户确认后才改级）；M3.15 起每次 AI 请求的
  token/耗时/错误写入 `ai_calls`，设置页可查看今日/累计用量与调用明细，
  并内置日志查看器（只记元数据，不保存提示词与模型回复）。
- **M6.1（2026-11-20）**：阅读查词提速。文章生成时顺带返回 8~15 个难词的
  语境义写入查词缓存；打开文章后由独立的 `_prefetch_worker` 批量预查最多
  10 个候选词（本地筛选、一次请求、失败静默），双击这些词直接命中缓存。
  预取只补缓存（`in_vocabulary=0`），不覆盖已有词条，可在设置页「阅读」关闭。
- **M6.2（2026-11-20）**：阅读检测选择题支持「不知道 + 补充说明」。选项之后
  追加 `E. 不知道`（`UNKNOWN_ANSWER=-2`，-1 仍为未作答），选中后才显示原因输入框；
  -2 按答错计分，说明随第一轮答案落库（`reading_quizzes.choice_notes_json`，
  v8 内幂等补列），并进入讲评 / 盲评 prompt 作为错因线索。
- **M6.4（2026-11-20）**：档内进度仪表盘。新增纯函数
  `services/level_progress.py`，今日页显示「B1 → B2 · 把握 38%」+ 进度条 +
  置信度 / pending + 最多 3 条证据（最近检测、生词比例趋势、词档认识率）；
  检测结果页补难度分布与高两档题答对数。不新增 AI 调用、不改等级与 SRS。
- **M6.5（2026-11-20）**：档内难度浮动。`difficulty_hint()` 推导 low / mid / high
  并注入文章 prompt（空值 / mid 与旧版逐字节一致），hint 与理由写入
  `articles.metrics_json`；可读性重试会撤掉 high，用户阅读反馈优先于 hint。
- **剩余为 M6.6 / M6.7**（强证据升级与边界文案）、可选 M4（深色模式、
  安装器 / 自动更新）与真实环境调优。当前看板见
  `docs/english_learner/PROGRESS.md`，已知技术债见 `TECH_DEBT.md`。
