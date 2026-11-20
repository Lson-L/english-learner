# Daily English：AI 学习闭环排期（M5）

- 最后更新：2026-11-20
- 状态：**M5.0 ~ M5.5 已完成；M5.6 按真实数据决定是否开工**
- 下一项：**M5.6 可选 P2**（先看 M5.5 指标，再决定做哪一项）；M4 仍排在 M5 之后
- 基线：M3.18 + M5.5 完成，SQLite schema v8，499 passed
- 执行顺序：M5.0 → M5.1 → M5.2 → M5.3 → M5.4 → M5.5；M5.6 按数据决定
- 与 M4 的关系：**M5 优先**；M4（深色模式 / 安装器 / 自动更新）继续作为可选工程项排在 M5 之后
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

## 7. M5.6 可选 P2（按数据决定）

| 项 | 说明 | 前置 |
|---|---|---|
| 查词语境义 | `word_senses`（`lemma + context_hash`）+「为什么是这个意思」「易混词」追问，按需计费、按语境缓存。注意：`context_mismatch` + 「按本句重查」M3.12 已有，这是补缓存与追问，不是修 bug | 真实多义词样本 |
| 文章教学包 | `glossary / grammar_point / discussion` 可选字段，缺失不校验、不触发重试 | — |
| 窄式阅读 | 每周一个主题带，目标词跨 3 天复现，优先覆盖"反复忘"的词 | M5.2 数据 |
| 预生成明日文章 | 后台低峰生成，需开关，失败不影响当天流程 | — |
| 流式输出 | 首字可见；`TaskWorker` 加 chunk 信号，取消语义改为"停止接收" | — |
| 离线降级打磨 | 最近一次成功配置提示与重试入口 | — |

---

## 8. 明确不做

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

---

## 9. 每个里程碑的固定收尾

- `uv run pytest -q` 全绿（当前 472，只增不减）；`ruff check` 与
  `ruff format --check` 通过；
- `services/` / `storage/` 不 import PySide6；所有 AI 调用走 `gui.workers.TaskWorker`
  且带 `task` 标签，元数据统一由 `AIClient` 写 `ai_calls`；
- prompt 改动同步升 `PROMPT_VERSION`；
- schema 改动写旧库升级测试，并同步 `export_service` 与导出说明；
- 更新 `PROGRESS.md` 看板（只放状态），交付细节追加 `HISTORY.md`；
- 不执行耗时的打包 / 构建，除非用户明确要求。
