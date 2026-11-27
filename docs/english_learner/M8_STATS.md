# M8 统计看板深化（2026-11-27）

目标：让统计页从“做了多少”升级为“学得怎么样、接下来做什么”，同时把
学习效果与 AI 用量从设置页收拢到统计页，并按重要程度重排布局。

约束（全部满足）：

- **不新增表、不改 schema**；只读现有数据；
- **0 次 AI 调用**；服务层纯 Python，可单测；
- 统计页只读 `AppContext` 快照，不自己拼 SQL；
- 低调主题与浅色主题共用同一套控件，颜色只从 `theme.py` 调色板取。

## 布局（重要数据靠上，成就最后）

1. 顶部 5 张指标卡：连续学习 / 累计学习 / 累计生词 / 累计阅读 / 累计复习；
2. 学习效果（近 30 天）+ AI 用量（并排）；
3. 学习足迹（26 周热力图）；
4. 累计成长 + 近 30 天活动（并排）；
5. 检测得分趋势 + 错因与薄弱点（并排）；
6. 词汇掌握 + 复习负载（并排）；
7. 真实文章（有供给历史才显示）；
8. 成就（始终最后）。

数据不足 3 个活跃日时，4~6 的趋势卡自动收起，只保留热力图 / 词汇 / 负载，
并显示“再积累几天”的提示；完全空库时只显示引导与成就。

## 任务卡

### M8.1 学习效果迁入统计页

- 从设置页删除「学习效果（近 30 天）」整卡，新增统计页同名卡；
- 复用 `MetricsService.build()`（首次通过率 / 回炉率 / 计划完成率 /
  文章与检测重试率 / 同等级趋势方向）；
- `gui/pages/common.py` 新增公共 `MetricTile`（说明 + 数字，两套主题）。

### M8.2 AI 用量迁入统计页

- 设置页「AI 使用与日志」改为「运行日志」，只保留日志查看器入口；
- 统计页新增「AI 用量」卡：今日调用 / 失败 / tokens、累计调用 / tokens、
  平均耗时，按钮打开原 `AIUsageDialog`；
- 数据来自 `AICallRepo.summary(since=today)` 与 `summary()`，不新增表。

### M8.3 复习负载前瞻

- `WordRepo.due_counts(start, end)` / `count_overdue()` / `count_stalled()`；
- `StatsOverview` 新增 `due_forecast`（未来 7 天）、`due_today`、`overdue_words`；
- 新增 `DueForecastChart`（7 根柱，悬停显示日期与数量）；
- 统计页卡片摘要：「今天到期 N · 其中逾期 M · 未来 7 天共 K」。

### M8.4 错因与薄弱点

- 新增 `services/quiz_insights.py`：取近 30 天完成且非练习的检测，
  复用 `quiz_calibration` 的错因 / 技能 / 难度纯函数；
- 输出错因计数、技能正确率、难度正确率、“不知道”比例；
- 新增 `HBarChart` 横向条形图；卡片摘要显示检测次数、不知道比例、
  最弱 3 个技能。

### M8.5 口径修正 + 加载性能

- `ReadingVolume` 增加 `completed_words / completed_articles`（完成过检测的
  严格阅读剂量）与 `real_articles / generated_articles`；
- `StatsOverview` 增加 `today_active`，连续卡改为显示“今天已打卡 / 还没打卡”；
- 累计曲线支持 30 天 / 90 天 / 1 年 / 全部（`build(cumulative_days=...)`，
  `0` 表示全部历史，窗口前的活动仍折成基线）；
- 性能：阅读剂量改用 `ArticleRepo.list_reading_volume()` 只取四列，
  不再 `SELECT *` 加载全部正文；检测趋势改用 `QuizRepo.list_formal_completed_light()`
  只取必要列，不再反序列化答案 / 讲评 / 产出题大字段；
- `_longest_streak` 每次 build 只算一次。

### M8.6 真实文章闭环

- `StatsOverview.real_text`：上架 / 完成 / 换回 / 弃读计数与 outcome 分布；
- 数据来自 `real_text_history`，无历史时整卡隐藏；
- 口径与 `real_text_feedback.py` 保持一致（`abandon` 不是 `abandoned`）。

### M8.7 词汇深挖

- `StatsOverview.word_bands`：复用已有但此前未接入界面的 `WordRepo.count_by_band()`；
- `stalled_words`：学习中且到期超过 7 天未复习的词；
- 词汇卡新增词档摘要行与“停滞 N 个”提示。

### M8.8 体验打磨

- 空 / 少数据折叠（见布局说明）；
- 顶部指标卡最小宽度 136px，窄窗口不把数字挤成两行；
- 成就解锁日期：`Achievement.unlocked_on` 由 `daily_stats` 累计首次达标日
  或连续区间结束日推导，徽章显示「已解锁 · MM-DD」；
- 热力图点击跳转未做（需要跨页路由，列入后续可选项）。

## 统计口径

- 复习负载：`words.due_date` 在窗口内、`status <> 'mastered'`、`in_vocabulary = 1`；
- 逾期：`due_date < today`；今日到期（`due_today`）含逾期；
- 停滞词：`status = 'learning'` 且 `due_date < today - 7 天`；
- 严格阅读剂量：`articles.read_at` 非空且该文章存在已完成检测；
- 错因：`reading_quizzes.error_types`，`none` 不计；只统计完成、非练习、有题目的检测；
- 真实文章：按 `real_text_history.status` / `outcome` 原样计数；
- 成就解锁日：累计类用 `daily_stats` 对应字段求和首次达标日；连续类用
  连续区间起始日 + target - 1；quizzes 用完成日期累计。

## 测试

- 新增 `tests/test_english_learner_quiz_insights.py`：空库 / 错因 / 技能 /
  难度 / 不知道比例 / 练习与窗口排除；
- `tests/test_english_learner_stats_overview.py`：新增复习负载、逾期、词档、
  停滞词、严格阅读剂量、today_active、累计窗口、成就解锁日、真实文章统计；
- `tests/test_english_learner_stats_page.py`：新增卡片渲染、成就最后、
  少数据折叠、窗口切换、低调主题英文与调色板；
- `tests/test_english_learner_metrics_page.py`：改为统计页学习效果卡；
- `tests/test_english_learner_usage_dialogs.py`：设置页入口改为统计页入口。

全量 `pytest`、`ruff check`、`ruff format --check` 均通过。

## 后续可选

- 热力图点击某天跳转到当天文章（需要 MainWindow 路由与 ReaderPage 打开指定日期）；
- 真实文章 outcome 趋势（按月）；
- 统计快照缓存（当前每次进入页面全量重算，数据量小，暂不需要）。
