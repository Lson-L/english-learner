# Daily English：AI 学习闭环与等级体验排期（M5 / M6.4~M6.7）

- 最后更新：2026-11-20
- 状态：**M5.0 ~ M5.5 已完成；M6.1 ~ M6.5 已交付；M6.6 ~ M6.7 待开工**
- 下一项：**M6.6 强证据升级 + 主客观一致**（第 7 节）；M5.6 可选 P2 与 M4 继续顺延
- 基线：M6.5 完成，SQLite schema v8，583 passed
- 执行顺序：M6.4 → M6.5 → M6.6 → M6.7；M5.6 按数据决定，M4 最后
- 与 M4 的关系：**M5 / M6 优先**；M4（深色模式 / 安装器 / 自动更新）继续作为可选工程项排在最后
- 来源：合并自两份 AI 能力提案（原 `AI_LEARNING_LOOP.md` 与 `AI_STRATEGY.md`，
  已于 2026-11-20 清理；需要追溯论证时看 git 历史）
- **执行以本文为准**，不要再引入第二份 AI 排期文档
- 阅读时机：开发或排期任何 AI 相关功能前读本文；PROGRESS 看板只放状态

---

## 0. 排期总览

| 里程碑 | 任务 | 估时 | schema | 新增调用 | 依赖 | 状态 |
|---|---|---|---|---|---|---|
| M5.0 | 文档收口 + 词表方案决策 | 0.5 天 | — | 0 | — | ✅ 2026-11-20 |
| M5.1 | 复习证据落库 | 1 天 | v8 | 0 | — | ✅ 2026-11-20 |
| M5.2 | 学习者记忆 + 计划回流 | 1.5 天 | — | 0 | M5.1 | ✅ 2026-11-20 |
| M5.3 | 本地可测 i+1 | 1 天 | v8 | 0（最坏 +1 次已有重试） | —（词表由 AI 一次性生成） | ✅ 2026-11-20 |
| M5.4 | 回译练习 | 2 天 | v8 | +2/次练习（出题 + 批改，均有缓存） | M5.2 | ✅ 2026-11-20 |
| M5.5 | 效果度量看板 | 1 天 | — | 0 | M5.1 / M5.3 | ✅ 2026-11-20 |
| M5.6 | 可选 P2（语境义 / 教学包 / 窄式阅读等） | 按需 | v9+ | 按需 | 真实数据 | ⬜ 未开始 |

合计约 8 天；其中 0 新增调用的部分约 5 天。

> **M6 后续**：M6.1 ~ M6.3（查词预取 / 检测「不知道」/ 生词补全）已交付，细节见
> `HISTORY.md`；M6.4 ~ M6.7 是「等级体验优化」四期，任务卡见第 7 节。
> `M5.6` 保持可选、暂缓，不作为下一个开工项。

### 为什么是这个顺序

1. **M5.1 先做**：给 SM-2 补客观证据，同时是 M5.2 的"拼错词"统计的数据前提，
   避免记忆摘要里有一项来源是空的（原提案把"证据落库"排在"记忆注入"之后，
   存在依赖倒置，本排期已修正）。
2. **M5.2 合并所有 prompt 改动**：学习者记忆注入与计划回流一次做完，`PROMPT_VERSION`
   只升一次，回归测试只做一轮。
3. **M5.3 / M5.4 与 M5.1 共用 schema v8**：一次迁移、一次升级测试。
4. **M5.4 是唯一新增日常调用的项**：放在闭环合拢时做，先确认前四项收益。
5. **M5.5 是验证、M5.6 是可选**：等 M5.1~M5.4 的数据出来，再决定 M5.6 做什么。

### schema v8 一次做完

| 表 | 改动 | 归属 |
|---|---|---|
| `review_logs` | `+answer_text, +verdict, +distance` | M5.1 |
| `articles` | `+metrics_json` | M5.3 |
| `writing_attempts`（新） | 回译题目、用户答案、批改结果 | M5.4 |

迁移纪律：只 `ALTER TABLE ... ADD COLUMN` / `CREATE TABLE IF NOT EXISTS`；
加列前用 `PRAGMA table_info` 判断，重复启动幂等；`export_service` 同步加列，
新增表成为第十二张导出表；旧库升级必须有无损测试。

> **实际执行备注（M5.3）**：M5.1 先把 v8 发布了出去，`articles.metrics_json`
> 只能在 v8 上幂等补列（`_add_column` 每次启动检查，不放进 `version < 8` 分支），
> 否则已经是 v8 的旧库永远补不上。M5.4 的 `writing_attempts` 是新表，
> `CREATE TABLE IF NOT EXISTS` 会自动补齐，无需再升版本号。
>
> **实际执行备注（M5.4）**：`writing_attempts` 除任务卡列出的字段外，还加了
> `article_date`（按天统计 / 导出排序）、`position`（0~2，保证题面顺序）与
> `graded_at`（NULL = 尚未批改），并用 `UNIQUE(article_id, position)` 防重复出题。

---

## 1. M5.0 文档收口与前置决策（0.5 天）✅ 2026-11-20

### 已完成

- 建立本排期文档，两份提案顶部标注状态并互链；
- 登记 `CLAUDE.md` 与 `PROGRESS.md` 文档索引；
- 记录决策：learner memory 走**本地派生**，不建表、不让 AI 压缩
  （理由：0 调用、无漂移、可控、空数据可完全回退）；
- 记录决策：本项目为**个人自用、不对外分发**，词表许可不作为约束；
  M5.3 的词表改由 AI **一次性生成**并作为本地静态文件存档
  （理由：保留本地指标的独立性与可复现性，同时不引入外部依赖）；
- 记录决策：**不做多模型槽位 / 成本路由**。个人使用、调用量可控，成本不是瓶颈；
  若将来查词延迟明显，再单独评估。

**验收**：索引生效；三份文档不再互相冲突；上述决策已写入本节。

---

## 2. M5.1 复习证据落库（1 天，0 新增调用）✅ 2026-11-20

**目标**：让 `review_logs` 记录"用户写了什么、判得对不对"，成为统计与学习者记忆的
数据源，关闭 `TECH_DEBT` 第 57 条。

**改动点**：

- `storage/db.py`：`SCHEMA_VERSION` 7 → 8；`review_logs` 增加
  `answer_text TEXT NOT NULL DEFAULT ''`、`verdict TEXT NOT NULL DEFAULT ''`、
  `distance INTEGER NOT NULL DEFAULT 0`；
- `models/entities.py`：`ReviewLog`（约 339 行）增三个字段与 `from_row` 映射；
- `storage/repositories.py`：`ReviewRepo.log()`（约 464 行）增参数与 INSERT 列；
- `services/srs.py`：`review()`（约 155 行）增 `answer_text / verdict / distance`
  参数并透传落库，**不参与 `schedule()`**；
- `gui/pages/review.py`：`_submit_spelling()`（约 580 行）已拿到
  `RecallResult.status / answer / distance`，暂存后在 `_rate()` 传给 `review_service`；
  看词模式与拼写跳过统一写 `verdict = "skipped"`；
- `services/export_service.py`：CSV 列同步。

**验收**：

- 拼写答案、判定、编辑距离可在 `review_logs` 回放；导出包含新列；
- 旧库升级不丢数据；离线全流程可走完；
- 评分仍由用户按 1~4 确认，调度结果与改动前一致（单测对照）。

**风险**：统计口径漂移 → `verdict` 只允许 `correct / near / wrong / skipped`
四个值，用常量集中定义，禁止散落字符串。

**交付（2026-11-20）**：schema v8 已落地（旧库无损升级、`export_service` 同步
三列）；`srs.py` 集中定义 `VERDICT_*` 常量，`recall.STATUS_*` 复用同一套字符串，
`ReviewService.review()` 先校验 verdict 再写调度，证据不参与 `schedule()`；
复习页只在真正提交拼写答案时写 `correct / near / wrong`，看词回忆、拼写跳过与
短语回退统一写 `skipped`；352 passed，ruff 通过。

---

## 3. M5.2 学习者记忆 + 计划回流（1.5 天，0 新增调用）✅ 2026-11-20

**目标**：让每次 AI 调用知道自己面对的是谁——把已有学习数据变成 ≤200 token 的本地
摘要，并把上周计划变成下周约束。

### 3.1 `services/learner_memory.py`（新增，纯 Python）

| 内容 | 来源 |
|---|---|
| 等级 + 置信度 + 最近一次调级原因 | `profile` |
| 测评画像薄弱点（保留刚测评完的个性化提示） | `profile.weaknesses_json` |
| 最近 7 天（含今天）错因 Top2 | `level_evidence.error_types_json` |
| 反复评"忘记"的词 5 个 | `review_logs`（rating = 1） |
| 拼写错误最多的词 5 个 | `review_logs`（M5.1 新列） |
| 到期未复习词数（积压压力） | `words` |
| 上周复盘未完成的重点 | `weekly_reviews.focus_json` |

**硬约束**：

- 输出 ≤200 token；空数据返回空串；
- 只放统计结论，不放原文，不做历史对话回灌；
- 不新增 AI 调用、不建表、不做"AI 压缩档案"。

### 3.2 统一注入 builder

- `services/prompts.py` 增 `build_learner_memory_block(memory, task)`，按任务过滤；
- 注入四处：文章生成（替换 `_focus_from_profile()`，`article_service.py:146`）、
  检测出题（并入 `_weak_skills()`，`reading_quiz_service.py:852`）、查词、复习求助；
- `PROMPT_VERSION` 升一次（与 3.3 合并升级）。

### 3.3 计划回流

- `build_article_messages()` 增 `learner_plan` 参数，把上周 `focus_json` 作为软约束；
- 下一次每周复盘 prompt 带上周计划，让 AI 回答"计划完成度如何"；
- 等级变更仍走用户确认，`apply_suggestion()` 的边界不变。

**验收**：

- memory 为空时所有 prompt 与当前逐字节一致（快照测试）；
- 按任务过滤、超长截断、空表不报错有单测；
- 上周有 focus 时文章 prompt 出现计划行，无则不变。

**风险**：注入误导或挤占正文 → 上限 + 只放统计结论 + 预留按任务开关常量。

**交付（2026-11-20）**：`services/learner_memory.py` 已落地（`LearnerMemory`
冻结 DTO + `LearnerMemoryService.build()`，新仓库聚合方法 3 个）；`PROMPT_VERSION`
升到 `m5-2`，`build_learner_memory_block()` 按任务过滤并用 `MAX_MEMORY_CHARS`
截断；记忆经 `AppContext` 的 `memory_provider` 注入文章 / 查词 / 复习求助三处，
阅读检测改为把 `error_types` 并入 `_weak_skills()`；上周 `focus` 经 `learner_plan`
回流文章、经 `previous_focus` 进入本次复盘；空记忆 prompt 逐字节一致有快照用例；
373 passed，ruff 通过。

---

## 4. M5.3 本地可测 i+1（1 天，0 新增调用，最坏 +1 次已有重试）✅ 2026-11-20

**目标**：把"模型自称 B1"变成"本地能验证对你只难一点"。

**改动点**：

- 新建 `services/readability.py`（纯函数，无 AI、无网络）：未知词比例、目标词覆盖率、
  平均 / 最长句长；
- **词表生成（一次性，开发期完成，不进入运行时）**：让 AI 输出常用英文原形 +
  CEFR 档，去重、转小写，人工抽查前 100 个，存为
  `assets/wordlists/ai_frequency.json`（约 50~100KB）；该文件入库随程序分发，
  指标只依赖这份静态文件，结果可复现；不满意可直接手改；
- 已知词集合 = 该词表 + `interval_days >= 30` + `in_vocabulary = 1`；
- `articles` 增 `metrics_json`（schema v8 一并做）；
- 超阈值走**现有** `repair_note` 重试通道（`article_service.py:284`），不新增调用类型；
- 阅读页展示"本篇 X% 生词，Y 个目标词"。

**边界**：

- 指标只用于"记录 + 区间告警 + 展示"，**不**参与改等级；
- 先观察一周再决定是否强制重试；
- 词表缺失按未知计，但给宽容系数，宁可不重试也不误伤；
- 个人使用、不对外分发，词表许可不作为约束；若将来公开仓库，需替换为可分发来源
  或将词表文件排除出版本库。

**验收**：

- 纯函数单测（给定词表与文本 → 指标）；
- 阈值可配置；旧文章无 `metrics_json` 正常显示；
- `ai_calls` 的失败率与重试次数有前后对比。

**交付（2026-11-20）**：`services/readability.py` 已落地（分词与屈折还原、
生词比例 / 句长 / 目标词覆盖、`ReadabilityThresholds` 宽松解析、宽容模式）；
`assets/wordlists/ai_frequency.json` 为 AI 一次性生成的 3212 词静态词表
（60.8KB，A1~C1；生成脚本 `scripts/generate_wordlist.py` 保留，开发期不进入运行时）；
`articles.metrics_json` 幂等补列，生成后写入指标并在阅读页 meta 行展示
`生词 X% · 目标词 N 个`，超阈值追加“偏难”；`config.json` 的 `readability` 段
可配阈值，默认只记录不重试，开启后走现有 `repair_note` 第二次尝试；
414 passed，ruff 通过。`ai_calls` 前后对比需要真实使用一周的数据，
当前只把指标写进日志与 `articles`，供后续统计。

---

## 5. M5.4 回译练习（2 天，+2 次/次练习）✅ 2026-11-20

**目标**：补上产品里唯一缺失的英文产出练习。M3.17 之后用户在整个产品里不再写英文句子。

**改动点**：

- `writing_attempts` 新表（schema v8 一并做）：`article_id / sentence_en /
  reference_cn / answer_en / verdict_json / score / created_at`；
- 阅读完成后本地抽 3 句（优先含目标词、长句），给中文译文；用户写英文；
- AI 批改对照原文给"最小修改 + 原因 + 0/2 分"，**一次批量调用**，当日缓存、可跳过；
- 错误类型（时态 / 搭配 / 冠词）写入 learner memory 输入，供下一篇文章自然覆盖。

**边界**：

- 只作练习反馈，不影响等级与 SRS；
- 失败 / 离线可跳过，不阻塞阅读；
- 新增 `task="回译批改"` 标签并写 `ai_calls`。

**验收**：

- 出题每篇文章最多 1 次调用（落库缓存，重复打开不重新请求）；
  批改每次提交最多 1 次（格式不合法时带原因重试 1 次）；
- 批改结果落库可导出；旧库升级正常；
- AI 调用走 `TaskWorker`，`services` 不 import PySide6。

> **2026-11-20 决策（取消“一天最多 1 次调用”）**：原验收按“翻译免费”估算，
> 但任务卡同时要求“本地抽 3 句 + 给中文译文 + AI 批改”，译文本产品里没有任何
> 现成缓存，必然要多一次调用。项目为个人自用、调用量可控，**以学习效果优先**：
> 出题与批改各算一次，二者都做缓存；不再限制每天总调用次数。

**交付（2026-11-20）**：

- `writing_attempts` 表在 v8 上新增（`CREATE TABLE IF NOT EXISTS`，无需升版本），
  除任务卡字段外增加 `article_date / position / graded_at` 与
  `UNIQUE(article_id, position)`；文章替换时级联删除；
- 新增纯函数 `services/back_translation.py`：本地切句（缩写 / 小数 / 引号）、
  按“含目标词 + 长度适中”抽 3 句、译文与批改 JSON 校验、错误类型固定词表
  （tense / agreement / article / preposition / collocation / word_form /
  word_order / vocabulary / spelling / other）；
- 新增 `services/back_translation_service.py`：`load_or_create()` 命中缓存直接返回，
  否则一次 `回译出题` 调用翻译并落库；`grade()` 一次 `回译批改` 调用完成 3 句批改，
  回写答案、`verdict_json`、`score`、`graded_at`；失败保留已写答案可重试；
- 提示词新增 `build_back_translation_translate_messages()` /
  `build_back_translation_grade_messages()`，`PROMPT_VERSION` 升到 `m5-4`；
- 学习者记忆新增 `writing_error_types`（近 7 天批改错误类型 Top2），
  文章 prompt 增加“英文写作薄弱”一行，供下一篇自然覆盖；
- 阅读页新增「回译练习」入口与 `BackTranslationDialog`：加载 / 练习 / 已批改三态，
  逐句展示中文、英文输入、0/2 分、最小修改、原因与问题类型；出题与批改都在
  `TaskWorker`，请求中拒绝关闭；
- 导出增加第十二张表 `writing_attempts`（JSON 快照 + CSV）；
- 测试：454 passed（新增 40 条：切句 / 抽句 / 译文与批改解析 / 缓存只调一次 /
  非法译文重试 / 批改回写 / AI 失败不落库 / 级联删除 / 记忆错因 / 旧 v8 补表 /
  导出新表 / 弹窗三种状态与失败重试 / 阅读页按钮），`ruff check` 与
  `ruff format --check` 通过。

---

## 6. M5.5 效果度量看板（1 天，0 新增调用）✅ 2026-11-20

- 新建 `services/metrics_service.py`（纯本地）：
  生词首次通过率（首次 `rating >= 3`）、词回炉率、计划完成率、
  文章 / 检测重试率、同等级检测得分趋势；
- 展示位置：设置页「学习效果（近 30 天）」卡片；
- 数据源：`review_logs` / `daily_stats` / `ai_calls` / `weekly_reviews` /
  `reading_quizzes` / `writing_attempts`；
- **不要**用"AI 自评质量"当指标；
- 后续可用于校准 `level_estimator` 经验阈值（`TECH_DEBT` 第 38 / 42 条）。

**验收**：指标可离线计算、有单测；不新增 AI 调用；统计口径写进本文件，
避免以后口径漂移。

### 6.1 统计口径（固定，后续不得静默修改）

窗口默认 **近 30 天（含今天）**，展示在设置页；`window_days` 可传参，
界面暂不提供切换。

| 指标 | 口径 |
|---|---|
| 生词首次通过率 | 窗口内发生“首次复习”的词中，首次评分 >= 3 的比例 |
| 词回炉率 | 窗口内复习过的词中，至少一次评分 <= 2（忘记 / 模糊）的比例 |
| 计划完成率 | 上周复盘 `focus` 中，本周能找到对应行为的条目 / 可映射条目 |
| 文章 / 检测重试率 | `ai_calls` 中 `attempt > 1` 的行数 / 该任务全部尝试数 |
| 同等级得分趋势 | 非练习检测按文章等级分组，最近 3 次均值 − 更早 3 次均值 |

补充规则：

- 窗口是日期闭区间 `[今天 - N + 1, 今天]`，上下界都生效；系统时钟异常 /
  手工写入的未来记录不进入统计；
- “首次复习”按 `reviewed_at, id` 取第一条；窗口按首次复习日期过滤，
  窗口外的首次记录不进入分母，同一词后续复习不重复计入；
- 计划完成率只看**紧邻的上周复盘**；更早计划不再跟踪。focus 条目按关键词
  映射到行为：阅读→`daily_stats.article_read`、复习→`reviews_done`、
  生词→`words_added`、检测→按 `completed_at` 完成的非练习检测、
  写作/回译→已批改 `writing_attempts`；听力 / 口语等本产品不提供的条目
  无法映射，**不进分母**；没有上周计划或没有可映射条目时显示 “—”。
  跨天提交（周日文章、周一凌晨提交）算在完成的那一天；
- 重试率按任务统计：文章 `task = "每日文章"`，检测 `task LIKE "阅读检测%"`；
  重试尝试数 / 全部尝试数（≤100%），窗口内无请求显示 “—”；
- 趋势按**文章等级**（`reading_quizzes.level`）分组，排除 `practice_mode`；
  窗口与排序都按 `completed_at`，文章日期在窗口外但完成日期在窗口内也算；
  “最近 3 次”与“更早 3 次”不重叠，少于 4 次不判断趋势；差值在 ±3 个百分点
  内（含边界）视为持平（→），否则 ↑ / ↓；
- 所有指标只读本地 SQL，不写库、不新增 AI 调用，也不参与等级与 SRS。

**交付（2026-11-20）**：

- `services/metrics_service.py`：`MetricsService.build()` + 冻结 DTO
  （`MetricsSnapshot / RateMetric / RetryMetric / PlanCompletion /
  QuizPoint / QuizTrend`），空数据返回 `None` 而不是 0；
- 仓库新增只读聚合：`ReviewRepo.first_review_stats / relearn_stats`
  （`ROW_NUMBER() OVER` 取每词第一条）、`AICallRepo.attempt_stats`
  （首次 / 重试 / 失败）、`QuizRepo.count_completed_between`、
  `WritingAttemptRepo.count_graded_between`；
- `AppContext.metrics_service()` 组装；设置页新增「学习效果（近 30 天）」卡片，
  进入页面与切回时刷新，展示 5 个指标与按等级分组的得分趋势；
- 测试：472 passed（新增 18 条：空库 / 窗口可配 / 首次通过只看窗口内首次 /
  同秒按 id 取首条 / 未来记录排除 / 关键词映射 / 上周计划 + 本周行为 /
  无上周计划 / 全部不可映射返回 None / 跨周完成计入本周 / 写作行为 /
  按任务重试 / 等级分组与练习排除 / 趋势方向 ±3 边界 / 趋势按完成日期 /
  设置页空库与有数据渲染），`ruff check` 与 `ruff format --check` 通过。

---

## 7. M6.4 ~ M6.7 等级体验优化（P1~P4，2026-11-20 排期）

**背景**：等级制度保持四档 CEFR（A2/B1/B2/C1）不变。评审结论是「刻度粗不是主要
瓶颈」：真正影响体验的是（1）等级长时间不动、看不见进步；（2）文章只按 CEFR 标签
生成，档内贴合度差；（3）强证据也要等第二次检测，主观调级却能立即生效。因此四期
都**不升 schema、不新增 AI 调用、不改 SRS**，只做展示层、prompt 参数与可选的策略增强。

### 7.0 总览

| 期 | 任务 | 估时 | schema | 新增调用 | 依赖 | 状态 |
|---|---|---|---|---|---|---|
| M6.4 | 档内进度仪表盘 | 1 天 | — | 0 | — | ✅ 2026-11-20 |
| M6.5 | 档内难度浮动 | 1 天 | — | 0 | M6.4（同模块 `level_progress`） | ✅ 2026-11-20 |
| M6.6 | 强证据升级 + 主客观一致 | 1 天 | — | 0 | — | ⬜ 未开始 |
| M6.7 | 边界用户与文案 | 0.5 天 | — | 0 | M6.5 | ⬜ 未开始 |

顺序理由：M6.4 只读已有数据、零风险，先拿反馈；M6.5 直接复用 M6.4 的纯函数；
M6.6 动的是判定策略，单独成期并默认关闭；M6.7 收尾文案与边界。

### 7.1 M6.4 档内进度仪表盘（1 天，0 新增调用，无 schema）

**目标**：等级标签不变，但用户每天能看到「向下一档走了多远」的可见证据，
解决「等级不动 = 没进步」的停滞感。

**改动点**：

1. 新增 `services/level_progress.py`（纯 Python，不 import PySide6、不写库）：
   - `LevelProgress` 冻结 dataclass：`current_level / next_level / progress /
     confidence / pending_level / pending_count / quiz_summary / article_summary /
     band_summary / practice_only / has_data`；
   - `build_level_progress(profile, quizzes, band_stats, articles, *, today)`：
     - `progress = P(next) / (P(current) + P(next))`，取最近一次**非练习**检测的
       `ReadingQuiz.posterior`；没有 posterior 的旧数据回退为
       `pending_count / 2`，两者都没有则 `None`；
     - `quiz_summary`：最近一次正式检测按题目难度统计「答对 X/Y」，并单独统计
       「高两档题答对 N 道」；
     - `article_summary`：近 7 天 `articles.metrics_json` 的生词比例均值，
       与更早 3 天比较给 ↑ / ↓ / →（阈值 ±2 个百分点，见 7.5）；
     - `band_summary`：当前档与下一档的认识率，`ReviewRepo.list_band_stats()`
       各 ≥4 次复习才展示，否则省略；
     - `practice_only`：最近一次检测是练习模式时为 True，只标注「练习不计入」。
2. 仓库层：优先复用 `QuizRepo.list_recent_completed_with_titles()`、
   `ReviewRepo.list_band_stats()`、`ArticleRepo.list_recent()`；
   缺聚合时只加只读方法，**不写库**。
3. `gui/pages/today.py` 等级区改造：
   - 首行「B1 → B2 把握 38%」+ `QProgressBar`（`objectName="levelProgress"`，
     样式进全局 QSS）；
   - 第二行置信度 + `pending 1/2`（已有字段）；
   - 第三行最多 3 条证据（本周题目正确 / 生词比例 / 词档认识率），没有的条目省略；
   - 空状态：未测评沿用现有文案；已测评未检测显示「完成首次阅读检测后显示进度」；
     C1 显示「已到最高档」。
4. `gui/reading_quiz_dialog.py` 结果页补充：
   - 本卷难度分布（如 `B1×3 / B2×2 / C1×1`）；
   - 「本次答对 N 道高两档题」——只在 N > 0 时显示。

**验收**：

- 纯函数单测：无检测 / 无 posterior / 练习模式 / pending 1/2 / 刚降级 /
  空 articles / 词档复习不足 4 次 / C1；
- 0 新增 AI 调用，不升 schema，不改等级判定与 SRS；
- offscreen 构造测试；QSS 不单独设 `font-family`（CLAUDE.md 第 9 条）；
- 全量 pytest + ruff 通过。

**风险**：数字堆成装饰 → 最多 3 条证据，每条都对应一个可行动作
（做题 / 阅读 / 复习）；口径漂移 → 纯函数 + 7.5 冻结 + 单测。

**交付（2026-11-20）**：`services/level_progress.py` 已落地（`LevelProgress` /
`QuizProgressSummary` / `ArticleRatioSummary` / `BandProgressSummary` 冻结 DTO +
`build_level_progress()` / `summarize_quiz()`）；今日页等级区改为
「B1 → B2 · 把握 38%」+ `QProgressBar#levelProgress` + 元信息（置信度 / 词汇量 /
pending）+ 最多 3 条证据，无检测 / C1 / 练习模式均有空状态；结果页补充本卷难度
分布与「答对高两档题 N 道」；`QuizRepo.list_recent_formal_completed()` 在 SQL 层
排除练习，避免练习行挤占最近窗口；`progress_source` 区分 posterior / pending，
tooltip 按来源切换；外部验收 3 轮通过（第 3 轮无 P0/P1/P2）。583 passed，ruff 通过。

### 7.2 M6.5 档内难度浮动（1 天，0 新增调用，无 schema）

**目标**：文章生成从「只有 CEFR 标签」升级为「档内位置」，让低 B1 / 高 B1
拿到不同难度的文章，等级标签不变。

**改动点**：

1. `services/level_progress.py` 增 `difficulty_hint(...)`，返回
   `DifficultyHint(within_level: "low"|"mid"|"high", reason: str)`：
   - `high`：`P(上一档) >= 0.45` 或 `pending 1/2`，且最近一篇生词比例未超阈值；
   - `low`：最近 2 篇生词比例超阈值，或 7 天内发生过降级；
   - 其余 `mid`；`low` 与 `high` 同时命中时取 `low`；
   - 规则固定写进 7.5，不在调用方各写一套。
2. `services/prompts.py` 的 `build_article_messages()` 增 `within_level: str = ""`：
   - 空串 → 输出与当前逐字节一致（快照测试）；
   - `low`：要求「本档内偏易：高频词为主、句长靠近本档下限、避免抽象概念」；
   - `high`：要求「本档内偏难：可含 1~2 个上一档词、句长靠近本档上限，
     但语法与抽象度不得越过上一档」。
3. `ArticleService.generate_article()` 增 `within_level=""` 并透传；
   `get_or_create_today()` 与 `LevelFeedbackService` 重写文章时计算并传入；
   hint 由 `AppContext` 以 `hint_provider` 注入（与 `memory_provider` 同款），
   `ArticleService` 不直接依赖 `QuizRepo / ReviewRepo`，保持分层。
4. `ReadabilityThresholds` 按 hint 派生（`dataclasses.replace`）：
   `low` 收紧生词比例阈值，`high` 放宽；不改 `config.json` 的默认值。
5. hint 与 reason 写进 `articles.metrics_json`（已有 JSON 字段，**不升 schema**），
   阅读页 meta 行不展示。
6. `PROMPT_VERSION` 升到 `m6-5`。

**验收**：

- 不传 `within_level` 的 prompt 快照逐字节一致；
- 各 hint 分支、空数据、用户反馈优先于 hint 有单测；
- 旧文章无新字段正常显示；可读性重试仍走现有 `repair_note` 通道；
- 0 新增调用。

**风险**：模型越档 → prompt 写明「不得越过上一档」+ 可读性阈值兜底；
hint 与用户明确反馈冲突 → 用户反馈优先（`learner_feedback` 已有通道）。

**交付（2026-11-20）**：`difficulty_hint()` 按 7.5 口径落地（low 优先；两篇生词
比例偏高或近 7 天降级；上一档后验 ≥0.45 或 pending 指向下一档）；`build_article_messages()`
新增 `within_level`，空值 / mid 时 prompt 逐字节一致，high 引用**高一档**（B1 → B2），
C1 只要求句式更复杂；`ArticleService` 新增 `hint_provider` 与
`within_level/hint_reason`，写入 `articles.metrics_json`（readability 关闭也写），
provider 异常静默退化；`_thresholds_for()` 按 low 收紧 / high 放宽可读性阈值；
可读性重试会撤掉 high，metrics 记录实际发送值；阅读反馈中“太难/太简单”优先于
相反 hint。583 passed，ruff 通过。

### 7.3 M6.6 强证据升级 + 主客观一致（1 天，0 新增调用，无 schema）

**目标**：强证据不必等第二天；主观调级的权限与提示和自动策略一致。

**改动点**：

1. `services/level_estimator.py` 增纯函数
   `has_strong_upgrade_evidence(observations, posterior, current_level)`：
   - 置信度 >= `STRONG_UPGRADE_CONFIDENCE = 0.75`；
   - `posterior.recommended_level` 恰为**当前 +1 档**；
   - 至少 1 条 `difficulty >= 当前 + 2` 且 `correct=True`、`weight >= 1.0`
     的题目观测（只看 `quiz / production`，不看盲评与自评）；
   - 常量集中在本模块。
2. `apply_level_policy()` 增 `strong: bool = False`：升级方向且 strong 时一次
   生效，不写 pending；降级路径不变。
3. `ReadingQuizService.submit()`：非练习模式计算 strong 并透传；
   `level_updated_reason` 区分「强证据升级 / 连续两次升级 / 主观调整」。
4. 配置开关：`config.json` 增 `level` 段（`strong_upgrade_enabled`，默认 **false**），
   `ConfigStore.load_level()` 宽松解析；开关关闭时行为与当前完全一致。
5. 手动调级一致化：
   - `LevelFeedbackService.adjust()` 拆为 `reassess()`（已有）+
     `apply(profile, result, feedback, ...)`（原后半段：clamp → 生成文章 →
     原子替换 → 保存 profile）；`adjust()` 保留为两步包装，测试与旧调用不变；
   - `LevelFeedbackDialog` 改为两段 worker：先 `reassess()`；回到 UI 线程，
     若目标等级高于当前，弹确认「手动调级会立即升档，自动策略通常需要两次检测。
     仍要应用吗？」；确认后第二个 worker 执行 `apply()`；降级直接 `apply()`；
   - 取消确认时画像与文章都不动。
6. 设置页新增「等级」卡片：一个开关 + 一句说明（默认关）。

**验收**：

- 开关关：所有升级仍需连续两次（回归用例）；
- 开关开：高置信 + 高两档题答对 → 一次升级；普通同档全对仍两次；
- 降级路径不变；练习模式不写证据、不改级；
- 主观升级必须二次确认；降级立即生效；apply 失败不改画像与文章；
- `level_updated_reason` 能区分三种来源；
- 0 新增调用；不升 schema。

**风险**：升级抖动 → 双条件 + 默认关闭；开启后观察「升级后 7 天内降级次数」，
超过 1 次就回退开关。拆分服务时保持原子性：`apply()` 内部先生成文章再替换，
任何一步失败都不写 profile。

### 7.4 M6.7 边界用户与文案（0.5 天，0 新增调用）

**目标**：明确产品覆盖范围，降低 A2 底部与 C1 顶部的挫败。

**改动点**：

1. 测评结果页、设置页文案：「本产品覆盖 CEFR A2–C1；A2 以下建议先打基础，
   C1 以上建议搭配真实材料」；
2. A2 用户：`difficulty_hint` 优先 `low`，可读性阈值收紧；
3. C1 用户：hint 上限保护（不生成更高档要求），「太简单」反馈仍可触发重写；
4. `PRODUCT.md` 补一条决策记录：**刻度保持四档**，以及本轮评估的理由
   （测量精度、稳定性、外部参照价值）。

**验收**：文案快照；无逻辑回归；0 调用。

### 7.5 口径冻结（后续不得静默修改）

| 项 | 固定口径 |
|---|---|
| 档内进度 | `P(next) / (P(current) + P(next))`，取最近一次非练习检测 posterior；旧数据回退 `pending_count / 2`；再无则「—」 |
| 生词比例趋势 | 近 7 天 vs 更早 3 天的均值差；±2 个百分点内算持平（→） |
| 词档认识率 | `ReviewRepo.list_band_stats()`，单档复习 ≥4 次才展示 |
| hint 优先级 | 同时满足 `low` 与 `high` 时 **`low` 优先**（读不下去先降难度） |
| hint `high` | `P(上一档) >= 0.45` 或 pending 1/2，且最近一篇生词比例未超阈值 |
| hint `low` | 最近 2 篇生词比例超阈值，或 7 天内降过级 |
| C1 | `next_level` 为空：进度显示「已到最高档」，hint 固定 `mid` |
| strong 升级 | 置信度 >= 0.75 + 建议等级恰为当前 +1 + 至少 1 条高两档题答对（quiz/production，weight >= 1.0） |

改口径必须同步更新本节 + 单测 + `ARCHITECTURE.md` 对应小节；
统计只读本地 SQL，不写库、不新增 AI 调用。

### 7.6 观察指标与收尾

- 观察（不建新表，全部来自现有数据 / 日志）：
  「太难」反馈次数 / 周、「换一篇」次数、文章可读性重试率、检测完成率、
  升级等待天数分布、升级后 7 天内降级次数（仅 M6.6 开启后）；
- 每期独立可回滚；M6.6 默认关闭，先观察一周再决定是否打开；
- 固定收尾同第 10 节：pytest / ruff / 文档（PROGRESS 只放状态，HISTORY 放细节）。

---

## 8. M5.6 可选 P2（按数据决定，暂缓）

| 项 | 说明 | 前置 |
|---|---|---|
| 查词语境义 | `word_senses`（`lemma + context_hash`）+「为什么是这个意思」「易混词」追问，按需计费、按语境缓存。注意：`context_mismatch` + 「按本句重查」M3.12 已有，这是补缓存与追问，不是修 bug | 真实多义词样本 |
| 文章教学包 | `glossary / grammar_point / discussion` 可选字段，缺失不校验、不触发重试 | — |
| 窄式阅读 | 每周一个主题带，目标词跨 3 天复现，优先覆盖"反复忘"的词 | M5.2 数据 |
| 预生成明日文章 | 后台低峰生成，需开关，失败不影响当天流程 | — |
| 流式输出 | 首字可见；`TaskWorker` 加 chunk 信号，取消语义改为"停止接收" | — |
| 离线降级打磨 | 最近一次成功配置提示与重试入口 | — |

---

## 9. 明确不做

| 不做 | 原因 |
|---|---|
| 让 AI 决定 SRS 调度或评分 | 不可解释、不可复现，违反产品决策 3 |
| 让 AI 自由生成测评题库 | 违反产品决策 2（固定题库 + AI 只评分） |
| 让 AI 直接改等级 | 现有"用户确认 + 连续证据"约束是对的，不要在 AI 侧绕过 |
| 每张复习卡都调 AI 判分 | 成本爆炸，收益低于本地判分 |
| 多模型槽位 / 成本路由 | 个人使用、调用量可控，成本不是瓶颈；查词延迟明显时再单独评估 |
| 历史对话 / 文章全文回灌 prompt | 成本高，且与"不保存 prompt 与回复正文"的隐私约定冲突 |
| 把缓存改成每次实时调用 | 成本爆炸且离线不可用；缓存是本项目最正确的设计之一 |
| 口语陪练 / 发音评分 / TTS | PRODUCT 已明确 V2，V1 不做 |
| 首页堆"学习报告" | 没有对应动作的报告等于装饰；要报告先要计划（M5.2） |
| 拆细 CEFR 刻度（A2+ / 半档 / C2） | 每天 5~7 个观测撑不起更细刻度，属于假精确；改动要牵动 schema、题库、prompt、导出，收益低于展示层与档内浮动（见第 7 节） |
| 为进度展示新增 AI 评估调用 | `posterior / level_evidence / metrics_json` 已足够；新增调用违背缓存优先与成本约束 |
| 回填或重算历史等级数据 | 旧数据没有 posterior，重算等于伪造证据；只做向前兼容与「—」展示 |

---

## 10. 每个里程碑的固定收尾

- `uv run pytest -q` 全绿（当前 **678**，只增不减；以开工时实际为准）；
  `ruff check` 与 `ruff format --check` 通过；
- `services/` / `storage/` 不 import PySide6；所有 AI 调用走 `gui.workers.TaskWorker`
  且带 `task` 标签，元数据统一由 `AIClient` 写 `ai_calls`；
- prompt 改动同步升 `PROMPT_VERSION`；
- schema 改动写旧库升级测试，并同步 `export_service` 与导出说明；
- 更新 `PROGRESS.md` 看板（只放状态），交付细节追加 `HISTORY.md`；
- 不执行耗时的打包 / 构建，除非用户明确要求。

---

## 11. M7 真实文章自动供给（重点排期）

**执行依据：[M7_REAL_TEXT.md](M7_REAL_TEXT.md)。** 本节只做索引，细节以该文件为准。

**目标**：普通用户不需要找文章、判断难度、点「抓取」。App 每次打开时根据最近的
阅读/检测状态和上一篇真实文章的反馈，自动从本地分级真实文章库选一篇作为今天的
阅读；读完的「轻松 / 刚好 / 吃力」决定下一篇的难度和间隔。内容与进度都存 SQLite，
靠现有整库快照在两台电脑之间搬运。

**核心决策**：

- **离线优先、在线补充**：SQLite 缓存是主来源；有网时从内置 URL 源后台抓取并缓存；
  全部失败回退 AI 文章，并提供手动 URL / 粘贴入口；
- 自动供给、每天最多一篇；阅读页提供「换回 AI 文章」；
- 真实文章固定练习模式，不计等级证据、不改 SRS；
- 内置离线文章：预置题目 + 本地判分，**0 次 AI**；在线抓取文章：出题 1 次 AI，
  提交仍本地判分；
- 反馈驱动：success / neutral / struggle / abandoned 对应不同间隔与难度；
- 两台机器用 `database_transfer` 整库快照，不支持合并；
- **运行期降级**：缓存为空 / 抓取失败 / 种子失败 / 供给失败都回退 AI 文章，不阻塞启动；
- **内容包**（`corpus.json`）不做运行时自动下载，用手动导入（M7.6）；
  文章抓取是另一条可选链路（M7.7），有节制的后台补充。

**排期**：

| 期 | 内容 | 估时 | 依赖 | 状态 |
|---|---|---|---|---|
| M7.0 | 内容库 + SQLite 存储 + 种子 + 导出同步 | 1~1.5 天 | — | ✅ 基础设施 + 2 篇起步（A2/C1 待补） |
| M7.1 | 自动供给 + 阅读接入 + 本地练习 + 换回 AI | 1.5~2 天 | M7.0 | ✅ 2026-11-25 |
| M7.2 | 反馈闭环 + 弃读结算 + 间隔调整 | 0.5~1 天 | M7.1 | ✅ 2026-11-25 |
| M7.3 | 阅读剂量（累计词数） | 0.5 天 | — | ✅ 2026-11-25 |
| M7.4 | 语境识别复习 | 1.5 天 | M7.0（v9 的 mode 列） | ✅ 2026-11-25 |
| M7.5 | 真实文章基准趋势 | 0.5 天 | M7.0~M7.2 | ⬜（可选） |
| M7.6 | 内容包手动导入（无外网环境） | 0.5 天 | M7.0 | ⬜（可选） |
| M7.7 | 在线内容源 + 手动 URL / 粘贴 | 2~3 天 | M7.0、M7.1 | ⬜（可选，推荐） |

**schema v9 一次做完**：`articles` +`source/source_url/source_ref`；`review_logs` +`mode`；
新表 `real_texts`、`real_text_history`、`content_sources`；`config.json` +`real_text` 段。
全部幂等 `ALTER TABLE ADD COLUMN` / `CREATE TABLE IF NOT EXISTS`，不重建表。

**顺序**：M7.0 ~ M7.4 已完成；下一步 M7.6（内容包导入）或 M7.7（在线源），
M7.5 可选。一次只做一个里程碑，完成、跑测试、更新文档后停下等确认。
M6.6 / M6.7 继续顺延。
