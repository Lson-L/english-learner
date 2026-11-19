# Daily English 开发进度与交接

- 最后更新：2026-11-19
- 当前阶段：**M3 核心 + M3.5 ~ M3.10 + M3.11（Phase 2 / Phase 3）已完成**
- 产品方向：[PRODUCT.md](PRODUCT.md)

> 本文档是跨会话交接的唯一事实来源。每完成一个里程碑，更新「当前状态」「验证记录」
> 和「下一步」，不要只依赖聊天记录。

---

## 1. 当前状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 工程骨架、依赖分组、配置向导、真实连接测试、主窗口导航 | ✅ 完成 |
| M1 | SQLite、摸底测评、每日文章、阅读器、划词释义 | ✅ 完成 |
| M1.5 | 阅读反馈调难度：AI 重新定级 + 按反馈重写今日文章 | ✅ 2026-11-18 |
| M2 | 生词本、SM-2 复习、高亮持久化、首页真实数据 | ✅ 2026-11-19 |
| M3 | 数据导出、Key 加密、请求取消、错误分类、生词本增强、性能 | ✅ 核心完成 2026-11-19 |
| M3.5 | 生词原形/文章词形、AI 记忆方法、复习“忘记/模糊”求助 | ✅ 2026-11-19 |
| M3.6 | 阅读器改为正文全宽 + 双击弹出悬浮释义卡 | ✅ 2026-11-19 |
| M3.7 | 查词与生词本分离（in_vocabulary）+ 生词本右侧排版修复 | ✅ 2026-11-19 |
| M3.8 | 阅读完成后 AI 出题、答题讲评与动态调级 | ✅ 2026-11-19 |
| M3.9 | 阅读检测第一阶段：混合难度、短答、错因、连续证据 | ✅ 2026-11-19 |
| M3.10 | 生词本详情回显复习求助记录（用户输入 + AI 帮记） | ✅ 2026-11-19 |
| M3.11 | Phase 2：本地后验 + 置信度 + 历史证据；Phase 3：自适应出题、盲评、产出题、每周复盘 | ✅ 2026-11-19 |
| M4 | 深色模式、安装器 / 自动更新（可选，未排期） | ⬜ 未开始 |

M0 结束时，应用可以：启动 → 强制配置 AI → 真实请求测试 → 保存配置 →
进入带 5 个页面的主窗口。

M1 结束时，应用可以走完**首次使用与每日阅读闭环**：
启动 → 配置 AI → 摸底测评（或手动选级）→ 首页显示等级/词汇量/连续天数 →
阅读页生成今日文章（落库缓存）→ 双击单词看语境释义（查词缓存）→
加入生词本并永久高亮 → 重启后文章与高亮仍在。生词本列表与 SM-2 复习留到 M2。

M1.5 追加：阅读页「调整难度」按钮。用户写一句阅读感受（或点快捷选项），
AI 结合当前等级、今日文章和今日生词重新定级，并按新等级 + 反馈重写今日文章；
今日生词会尽量复用，但流畅优先、不强制。旧文章的高亮会被清除，生词本保留。

M2 结束时，产品闭环第一次完整可走：
今日页显示「到期 N 词」→ 点「开始复习」进入卡片复习（空格翻面、1~4 评分）→
复习记录落 `review_logs`、`daily_stats.reviews_done` +1、打卡更新；
生词本页支持全部/未复习/学习中/已掌握筛选、关键词搜索、修改中英文释义、删除；
复习完全本地化，断网 / AI 不可用也能正常进行。首页「今日待复习」改为按
`due_date <= 今天` 统计。

M3 核心项完成（2026-11-19）：

- **数据导出**：设置页可导出 JSON（完整快照）或 CSV（当时七张表各一个文件，
  `utf-8-sig`，Excel 直接打开；M3.8 起为八张表）；导出在 TaskWorker 里执行；
- **API Key 加密**：Windows DPAPI（与当前用户绑定），旧明文配置可直接读取；
- **请求可取消**：`TaskWorker` 增加协作式取消与 `cancelled` 信号，
  关闭窗口时先取消并等待，避免 QThread 销毁警告；
- **错误分类**：`AIError` 增加 `kind / retryable / is_offline`，
  网络、超时、429、5xx 标记可重试；界面补充“复习离线可用”的提示；
- **生词本增强**：排序（最新/最早/字母/到期日）、批量删除、
  未保存释义的脏检查（切页、切筛选、关闭窗口都会提示）；
- **性能**：大列表刷新时暂停表格重绘、复习队列上限 200、导出不阻塞 UI。

深色模式与安装器 / 自动更新列为可选 M4，尚未实现。

M3.5 完成（2026-11-19）：

- **生词按原形入库**：查词时由 AI 判断词典原形（went→go），`words.lemma` 存原形，
  `words.display_form` 保留文章中的形态；新增 `word_forms` 词形缓存，
  同一词形第二次查询直接命中、不再调用 AI；
- **AI 记忆方法**：查词时一并生成 `mnemonic` 写入词库，阅读释义卡、生词本详情、
  复习答案页都会展示；老词条可在生词本详情点「AI 生成记忆方法」补生成；
- **复习求助**：复习时点「忘记 / 模糊」会弹出原因框（快捷原因 + 自由文本），
  AI 结合原因给出针对性建议并更新记忆方法；用户也可跳过 AI 直接记录评分，
  离线时复习不受影响；原因与建议写入 `review_logs.note / advice`；
- **数据库升级**：schema v1 → v2（`words.mnemonic`、`review_logs.note/advice`、
  `word_forms` 表），原地 `ALTER TABLE`，旧数据保留；
- **换一篇带上下文**：用户在「换一篇」里写了提示词时，请求会附带当前文章
  （标题、等级、主题、正文），便于理解“这个话题 / 这种结构”等指代；
  没写提示词时不带原文。

M3.6 完成（2026-11-19）：

- **阅读器布局**：移除右侧固定释义栏，正文占满整页；
- **悬浮释义卡**：双击单词后在词旁浮出 380px 卡片（带阴影、可滚动、带关闭按钮），
  点击正文 / 滚动 / 按 Esc / 切页都会收起；卡片始终夹在页面可视范围内。

M3.7 完成（2026-11-19）：

- **查词不再等于加入生词本**：`words` 新增 `in_vocabulary` 标记；双击只写查词缓存，
  必须点「加入生词本」才进入生词本、复习队列和首页统计；
- **生词本右侧排版**：详情内容放进滚动区，字段多或窗口矮时不再重叠/挤压；
- 数据库 schema v2 → v3，升级前的旧词统一保留为“已加入”（无法区分历史行为，
  宁可保留也不误删），新查词默认只进缓存。

M3.8 完成（2026-11-19）：

- **阅读完成入口**：阅读页标题栏新增「阅读完成」主按钮；已有当天检测结果时
  自动变为「查看答题结果」；
- **AI 出题**：按当天文章出 5 道单选题（主旨 / 细节 / 推断 / 语境词义），
  题目与解析落库缓存，同一篇文章只出一次题，重开不重复消耗 token；
- **答题与讲评**：弹窗内逐题选择后提交；客观题本地判分，AI 返回逐题中文解析、
  总评与新等级建议；结果页展示得分、正确率、等级变化和每题解析；
- **大屏答题**：弹窗带最大化按钮，并支持 F11 全屏 / Esc 退出全屏，
  题目与解析区随窗口自动铺满；
- **动态调级**：每次最多调整一档；全对至少升一档、答对不到一半至少降一档，
  其余尊重 AI 建议；调级结果写回 `profile`；
- **失败兜底**：出题/讲评失败都可重试，题目缓存保留；讲评失败时画像与题目
  状态都不改，不会出现“等级变了但没有结果”；
- **数据库升级**：schema v3 → v4，新增 `reading_quizzes` 表
  （`article_id` 唯一，文章替换时级联删除）；导出与 CSV 同步包含该表。

M3.9 第一阶段完成（2026-11-19）——让阅读检测更像“测评”，而不是“五道对错题”：

- **混合难度出题**：5 道选择题按“2 题当前等级 + 1 题低一档 + 1 题高一档 +
  1 题高两档”生成，每题带 `skill`（主旨/细节/推断/词汇/语法）和
  `difficulty`（A2/B1/B2/C1）；高难度题用来探上限，低难度题排查粗心；
- **英文短答**：额外 1 道短答题，要求写 1~2 句；选择题本地判分，短答由 AI
  给 0/1 分，补充“主动产出”信号；
- **错因归因**：AI 对每题返回 `error_types`
  （vocabulary / inference / detail / main_idea / grammar / careless / unknown），
  结果页对错题展示“错因”；
- **用户自评**：提交前可选择“这篇读起来：轻松 / 刚好 / 吃力”，作为弱证据；
- **本地证据评分**：按题目难度加权（答对高难度加分更多、答错低难度扣分更多），
  形成 -1~+1 的证据强度；AI 建议与本地证据同向才走，强证据可压过矛盾建议；
- **连续证据策略**：升级需要连续 2 次向上证据，第一次只记录
  `pending_level / pending_count`；降级立即生效；结果页明确显示
  “保持 B1 · 建议升到 B2（1/2）”；
- **数据库升级**：schema v4 → v5（`profile.pending_level/pending_count`；
  `reading_quizzes` 增加短答、错因、自评、建议等级、待定状态、证据强度）；
- **导出**：JSON / CSV 同步包含新增字段。

M3.10 完成（2026-11-19）——生词本回显复习求助：

- 复习时点「忘记 / 模糊」，在弹窗里写的原因（`review_logs.note`）和点
  「请 AI 帮我记」得到的建议（`review_logs.advice`）此前只落库、界面看不到；
- 生词本右侧详情新增「复习求助记录」区块，按时间倒序展示最近 5 条，
  每条带时间、评分档位（忘记 / 模糊）、「我的输入」和「AI 帮记」；
- 没有原因/建议的普通评分记录（认识 / 太简单）不展示；超过 5 条时提示
  “更早的 N 次求助已省略”；
- 数据仍只存 `review_logs`，不新增表/列，导出行为不变；
- 外部验收：两轮 GLM-5，**复用同一 session**，最终无 P0/P1/P2；详见
  [VERIFICATION_M3.10.md](VERIFICATION_M3.10.md)。

M3.11（Phase 2 / Phase 3）完成（2026-11-19）——把“单次检测调级”升级为“长期、可置信的水平估计”：

**Phase 2：本地证据模型 + 置信度**

- **等级证据表**：新增 `level_evidence`（题目级观测 `observations_json`、后验、
  置信度、错因、耗时等）；文章替换/检测被级联删除时证据仍保留（`quiz_id` 置空）；
- **本地后验**：`services/level_estimator.py` 用逻辑斯蒂模型
  `P(correct | L, d) = 1/(1+exp(k*(index(d)-index(L))))` 计算 A2/B1/B2/C1 后验；
  先验锚定当前等级，历史证据按 0.75 衰减回放最近 5 次；
- **置信度与决策**：只在“当前 ±1 档”窗口内取后验峰值；置信度 < 0.6 时保持当前等级；
  升级仍需连续两次向上证据，降级立即生效；
- **弱证据**：AI 盲评等级（权重 0.5）、自评（0.25）、按 CEFR 词档聚合的复习正确率
  （最多 0.8）都只作为弱观测；查词时 AI 顺带标注 `words.cefr_band`；
- **历史趋势**：讲评 prompt 附带最近 8 次检测摘要与难度/技能正确率；今日页显示
  近 4 周等级记录；结果页显示「B1（置信度 0.62）」与四档后验分布。

**Phase 3：自适应出题与更强诊断**

- **两阶段自适应**：第一轮 3 道当前等级题；答对 ≥ 2 道往上一档、否则往下一档，
  再出 2 道边界选择题 + 2 道产出题（多一次出题调用）；
- **题目审核**：每次出题后增加审核 pass，明确不合格的题丢弃并重出；
- **更强产出信号**：短答 + 句子改写/翻译各 1 道，评分固定「内容 / 语法 / 词汇」；
- **盲评与防作弊**：等级评估与讲评分两次调用，盲评 prompt 不含当前等级；
  选项顺序随机打乱并重映射正确答案；同一天只允许一次正式检测，
  换文章后的第二次检测自动进入练习模式（不写画像、不写证据）；
- **每周复盘**：今日页新增「本周复盘」，AI 汇总检测/生词/复习数据，
  只给建议；用户点「应用建议等级」后才写回画像，同一周结果缓存；
- **数据库升级**：schema v5 → v6（profile 置信度/原因；words.cefr_band；
  reading_quizzes 多产出题/后验/练习模式/阶段；新增 level_evidence、weekly_reviews）；
- **测试**：273 passed（新增估计器、自适应、盲评、练习模式、每周复盘、v5→v6 迁移等）；
- **外部验收**：见 [VERIFICATION_M3.11.md](VERIFICATION_M3.11.md)。

## 2. 环境（实测）

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10 Home China 10.0.19045 |
| Python | 3.13.7（`.python-version` = 3.13） |
| 包管理器 | uv 0.12.3 |
| GUI | PySide6 **6.12.0** |
| AI SDK | openai **3.55.0**（注意：不是网上常见的 1.x，内部依赖 `httpx2`） |
| 测试 / 检查 | pytest 9.2.0、pytest-cov、ruff 0.17.5 |
| 虚拟环境 | `.venv/`，由 `uv sync` 创建 |

## 3. 快速开始

```bash
# 安装依赖（PySide6 与 openai 都是核心依赖）
uv sync

# 启动（首次会进配置向导）
uv run english-learner
uv run python -m english_learner            # 等价

# 已有配置时强制重新配置
uv run english-learner --reconfigure

# 测试 / 检查
uv run pytest -q
uv run ruff check src/english_learner tests
uv run ruff format src/english_learner tests

# 打包成单文件 exe → dist/DailyEnglish.exe（约 61MB，双击即用）
uv run pyinstaller --noconfirm --clean DailyEnglish.spec
```

配置文件位置（Windows）：`%APPDATA%\EnglishLearner\config.json`
日志：`%APPDATA%\EnglishLearner\logs\app.log`
想重演首次启动：删除上面的 `config.json`，或加 `--reconfigure`。

## 4. 文件地图（M3.11 完成后的现状）

```
src/english_learner/
├── __init__.py                 版本号、产品说明、分层约定
├── __main__.py                 支持 python -m english_learner
├── app.py                      入口：日志、配置、Database、主窗口、首次测评
├── assets/
│   └── fonts/                  内置开源字体 Inter + Literata（OFL 1.1，见 OFL-*.txt）
├── models/
│   └── entities.py             Profile / Article / Word / Highlight / DailyStats /
│                               ReadingQuiz 等 frozen dataclass + from_row / to_params
├── storage/
│   ├── db.py                   SQLite 连接、PRAGMA、建表迁移（schema v6）、事务、仓库入口
│   └── repositories.py         Profile / Article / Word / Review / Stats / Quiz / Evidence / WeeklyReview 仓库（纯 SQL）
├── services/
│   ├── ai_client.py            OpenAI 兼容客户端；test_connection / complete / complete_json
│   ├── ai_protocol.py          TextCompleter 协议（业务层只依赖一个方法）
│   ├── app_context.py          AppContext：配置 + DB + 各 Service 的组装点
│   ├── config_store.py         AIConfig、JSON 配置读写、URL 规范化、Key 脱敏
│   ├── provider_presets.py     服务商预设
│   ├── json_utils.py           parse_json_object 三档兜底 + 字段类型转换
│   ├── prompts.py              全部提示词 + PROMPT_VERSION = "m3-11"
│   ├── assessment_bank.py      3 套固定题（A2/B1/B2），默认 B1
│   ├── assessment_service.py   本地计分、AI 评分、CEFR 回退、手动选级、落库
│   ├── article_service.py      文章生成、JSON 校验、重试一次、当天缓存、换一篇
│   ├── dictionary_service.py   lemma 规范化、句子提取、查词缓存、加入生词本
│   ├── level_feedback_service.py 阅读反馈调级：AI 重新定级 + 软约束重写今日文章
│   ├── reading_quiz_service.py 阅读检测：自适应两阶段、盲评、产出题、本地后验与调级
│   ├── level_estimator.py      纯函数后验估计器（逻辑斯蒂 + 先验 + 历史衰减）
│   ├── quiz_calibration.py     难度/技能正确率与逐题对错统计
│   ├── weekly_review_service.py 每周复盘：汇总数据 + AI 建议 + 用户确认
│   ├── srs.py                  简化 SM-2 调度 + ReviewService（纯本地，不依赖 AI）
│   ├── secret_store.py         API Key 的 Windows DPAPI 加解密（非 Windows 回退明文）
│   ├── export_service.py       JSON / CSV 数据导出（十张表，含等级证据与每周复盘）
│   └── review_help_service.py  复习“忘记/模糊”求助：AI 建议 + 更新记忆方法
└── gui/
    ├── fonts.py                内置字体注册 + 全局英文字体（默认 Lexend，可选 Literata/Georgia/Inter）
    ├── theme.py                全局 QSS（含阅读器/释义卡/测评短文样式）
    ├── workers.py              TaskWorker(QThread)：AI 调用必须走这里；支持协作式取消
    ├── onboarding.py           3 步配置向导
    ├── assessment_dialog.py     摸底测评弹窗（答题 → 评分 → 结果/手动选级）
    ├── level_feedback_dialog.py 阅读感受弹窗（快捷选项 + 文本 → 调级并换文章）
    ├── reading_quiz_dialog.py  阅读检测弹窗（两轮自适应 + 产出题 + 置信度结果）
    ├── review_help_dialog.py   复习求助弹窗（原因 → AI 建议 → 更新记忆方法）
    ├── weekly_review_dialog.py 每周复盘弹窗（AI 总结 → 用户确认后改级）
    ├── main_window.py          导航 + 页面栈 + AppContext + 首次测评触发
    ├── widgets/
    │   ├── article_view.py     只读 QTextEdit：双击取词、高亮、17px/1.8 行距
    │   └── word_panel.py       悬浮释义卡（可滚动/关闭）+ 加入生词本按钮
    └── pages/
        ├── common.py           Card / PageHeader / StatCard(可刷新) / set_status
        ├── today.py            今日页：连续天数/到期复习/累计生词/文章状态/测评入口
        ├── reader.py           阅读页：正文全宽 + 悬浮释义卡、换一篇、调整难度、阅读完成
        ├── vocabulary.py       生词本页：筛选/搜索/排序/编辑/批量删除/脏检查/求助记录回显
        ├── review.py           复习页：卡片翻面、1~4 评分、会话统计（M2）
        └── settings.py         配置、字体、数据位置、JSON/CSV 导出（M3 滚动区）

tests/（共 218 个用例，全部 mock AI、无网络）
├── test_english_learner_config.py        配置读写/校验/脱敏
├── test_english_learner_ai_client.py     连接测试与错误翻译
├── test_english_learner_storage.py       建表幂等/CRUD/唯一约束/级联/统计/文章替换
├── test_english_learner_json_utils.py    围栏 JSON/噪声/类型转换
├── test_english_learner_assessment.py    计分/评分/容错/手动选级/落库
├── test_english_learner_article.py       生成校验/软约束/重试/缓存/换一篇
├── test_english_learner_feedback.py      反馈调级/软约束重写/失败不改旧数据
├── test_english_learner_reading_quiz.py  混合难度/短答/证据评分/连续调级/失败兜底
├── test_english_learner_reading_quiz_dialog.py 弹窗：缓存/答题/短答/自评/结果/全屏
├── test_english_learner_reader.py        lemma/句子提取/查词缓存/高亮/阅读页
├── test_english_learner_srs.py           SM-2 四档调度/易度边界/间隔上限/复习服务落库
├── test_english_learner_review_help.py   复习求助：建议解析/记忆方法落库/失败兜底
├── test_english_learner_review_help_dialog.py 求助弹窗：快捷原因/AI 建议/跳过
├── test_english_learner_review_page.py   复习页状态机/翻面/评分/会话统计/队列上限
├── test_english_learner_vocabulary_page.py 生词本筛选/搜索/排序/编辑/批量删除/脏检查/求助记录回显
├── test_english_learner_secret_store.py   DPAPI 往返/明文兼容/损坏密文
├── test_english_learner_export.py        JSON 快照/CSV 分表/空库/原子写入
├── test_english_learner_workers.py       TaskWorker 成功/失败/取消/关闭等待
├── test_english_learner_settings_export.py 设置页导出入口与状态提示
├── test_english_learner_fonts.py         字体文件/字体链/Qt 注册/偏好读写/设置页切换
└── test_english_learner_gui_smoke.py     offscreen 冒烟（主窗口/导航/测评/反馈/脏检查）
```

## 5. 验证记录

### 5.1 M0（2026-11-18）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **18 passed** |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format` | 已格式化 |
| offscreen 渲染 4 个界面（欢迎、配置、今日、设置） | 布局正常 |

### 5.2 M1（2026-11-18）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **91 passed**（M1 主体 62 + 字体 11 + 反馈调级及配套 18） |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format --check` | 已格式化 |
| 临时 DB 连续初始化两次 | 通过（`user_version = 1`，表齐全） |
| offscreen GUI（主窗口 5 页、今日页读库、测评弹窗、反馈弹窗） | 通过 |
| 反馈调级：AI 改级 → 文章原子替换 → 高亮清除、生词保留 | 通过（mock） |
| 反馈调级：AI 失败时旧文章与旧画像不变 | 通过（mock） |

M1 仍未验证 / 需要人工确认：

1. **真实模型端到端**：需要真实 Key 跑完整闭环（测评 → 生成文章 → 查词 → 高亮
   → 阅读反馈调级 → 新文章）。
   代码路径已用 fake completer 覆盖，但真实网关的 JSON 稳定性、文章质量未测。
2. **`response_format` 兼容性**：`AIClient.complete_json` 在识别到
   “不支持 response_format” 时会自动去掉该参数重试一次；真实网关的报错文案
   是否都能被识别，需要实测（识别规则见 `ai_client._json_mode_unsupported`）。
3. **真实窗口排版 / 字体**：offscreen 没有字体库，字号/行距/高亮颜色与内置字体
   实际效果需在真实 Windows 桌面确认。
4. **文章词数**：本地校验放宽到 200~400，prompt 要求 250~350；不同模型可能
   频繁卡在边缘，需要看实际重试率。
5. **反馈调级效果**：AI 是否真的按“至少降一档”执行、新文章生词密度是否明显下降，
   需要真实模型实测；prompt 已把反馈传给写文章的模型，但仍依赖模型遵守。

> offscreen 模式下 Qt 字体库为空，截图里中文是方框，这是**离屏环境限制**，
> 不是应用 bug；真实 Windows 桌面会正常加载微软雅黑。

### 5.3 M2（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **122 passed** |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format --check` | 54 files already formatted |
| SRS 纯函数 | 忘记重置、1/6 天起步、易度下限 1.3、间隔上限 365、掌握阈值 30 天 |
| 复习服务 | 到期过滤、调度落库、`review_logs`、`daily_stats.reviews_done` |
| 复习页 offscreen | 概览 / 翻面 / 评分 / 会话统计 / 无到期禁用开始 |
| 生词本页 offscreen | 筛选 / 搜索 / 编辑释义 / 删除 / 空状态 |

M2 仍未验证 / 需要人工确认：

1. **真实窗口排版与快捷键**：复习卡片的字号、按钮配色，数字键 1~4 在中文输入法
   或按钮焦点下是否稳定触发；
2. **调度节奏体感**：1 / 6 / 15 天……的间隔是否符合真实记忆曲线，是否需要
   调 `services/srs.py` 里的系数；
3. **生词本规模**：几百条词时 `QTableWidget` 的滚动与全量刷新性能；
4. **积压复习**：一轮上限 200 词，积压更多时「再检查一次到期词」的体验。

### 5.4 M3 核心项（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **156 passed** |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format --check` | 60 files already formatted |
| DPAPI | 加密往返、同一明文两次密文不同、旧明文配置可直接读、损坏密文报 ConfigError |
| 导出 | JSON 快照字段/计数、CSV 六表/中文编码、空库、临时文件原子替换 |
| 取消 | 启动前取消、执行中取消丢弃结果、AIError/未知异常翻译、`stop()` 等待 |
| 生词本 | 四种排序、批量删除、脏检查保存/丢弃/取消、300 条列表刷新 |
| 设置页导出 | 按钮可用性、真实写文件、结果/失败/取消状态提示 |

M3 仍未验证 / 需要人工确认：

1. **DPAPI 的跨机器行为**：复制 `config.json` 到另一台电脑/用户后应提示重新配置，
   需在真实双机环境确认错误文案与恢复流程；
2. **关闭窗口的等待体验**：网络请求进行中关闭窗口时最长等待一次 HTTP 超时，
   需要真实弱网环境观察是否可接受；
3. **深色模式 / 安装器**：未实现（可选 M4）。

### 5.5 M3.5（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **179 passed** |
| `ruff check` / `ruff format --check` | 通过 / 64 files formatted |
| schema v1 → v2 | 旧库原地升级、旧词条与复习记录保留、新列/`word_forms` 就位 |
| 原形与词形 | went→go 入库、display_form=Went、二次查 went 命中缓存不调 AI |
| 记忆方法 | 查词写入、老词单次生成、失败抛 AIError；释义卡/生词本/复习页展示 |
| 复习求助 | 建议解析、mnemonic 落库、无 advice 报错、跳过/取消不误记评分 |
| 复习日志 | `note / advice` 随评分落库并可导出 |
| 导出 | 当时七张表（新增 `word_forms`）与新增字段全部包含（M3.8 起为八表） |
| 换一篇上下文 | 有提示词时 prompt 含当前文章正文；无提示词时不含（含 builder 兜底） |

M3.5 仍未验证 / 需要人工确认：

1. **真实模型的原形判断质量**：went→go、better→good 等是否稳定；错判时目前没有
   “手动改原形”的入口（只能删词重查）；
2. **记忆方法质量**：联想/谐音是否真的容易记，需真实使用后调 prompt；
3. **复习求助的成本与节奏**：每点一次“忘记/模糊”就多一次 AI 调用，
   若觉得太频繁，可增加“本次复习不再提示”或按等级开关。

### 5.6 M3.6 阅读器悬浮卡（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **180 passed** |
| `ruff check` / `ruff format --check` | 通过 / 64 files formatted |
| 布局 | 阅读页无 QSplitter，正文占满；释义卡是页面子控件，默认隐藏 |
| 定位 | 有取词矩形时贴在词下方，越界会翻到上方并夹在页面内 |
| 收起 | 点击正文、滚动正文、按 Esc、点关闭按钮、切文章/切页都会隐藏 |
| 内容 | 卡片宽度 380px，释义内容放滚动区，长内容不截断 |

M3.6 仍未验证 / 需要人工确认：

1. **真实窗口的视觉效果**：阴影、圆角、卡片与单词的相对位置需要在真实桌面看；
2. **offscreen 布局限制**：`cursorRect` 在无窗口的 offscreen 环境下可能尚未计算，
   因此相关单测只验证“记录了矩形 + 卡片夹取”，真实取词位置需人工确认。

### 5.7 M3.7 查词/生词本分离与排版（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **181 passed** |
| `ruff check` / `ruff format --check` | 通过 / 64 files formatted |
| schema v2 → v3 | 真实库副本升级成功：13 个旧词全部保留并标记为已加入 |
| 查词缓存 | 新的 `lookup()` 写入 `in_vocabulary=0`，不出现在生词本/复习队列/统计 |
| 加入生词本 | `add_to_vocabulary()` 置 1；重复加入幂等，状态不回退 |
| 生词本排版 | 右侧详情放入 QScrollArea；offscreen 验证无重叠、无横向滚动 |
| 导出 | 导出仍包含全部缓存词（`include_cache=True`） |

M3.7 仍未验证 / 需要人工确认：

1. **真实窗口右侧排版**：滚动条外观、底部按钮位置需要真实桌面确认；
2. **历史自动词条**：升级前的词无法区分“查过”与“真正加入”，全部保留；
   如不需要可在生词本里批量删除。

### 5.8 M3.8 阅读检测（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **205 passed** |
| `ruff check` / `ruff format --check` | 通过 / 68 files formatted |
| 出题解析 | 支持 `answer_index` 数字 / “B” / “B. xxx”；不足 3 题判失败并重试 |
| 缓存 | 同一篇文章第二次 `get_or_create` 不再调用 AI |
| 判分 | 本地按下标判分；满分 5/5、3/5、0/5 均正确 |
| 调级约束 | 全对至少升一档、低分至少降一档、每次最多一档、非法等级回退 |
| 失败兜底 | 讲评 JSON 失败不改 profile、题目仍未完成，可直接重试 |
| 存储 | `reading_quizzes` 往返、唯一约束、文章删除级联清理 |
| 导出 | JSON 含题目数组与 total；CSV 第 8 张表 `reading_quizzes.csv` |
| GUI | 缓存题目直接展示、未答完拦截、提交后结果页、已完成直接看结果 |
| 全屏 | 最大化按钮存在；F11 进入全屏、再按 Esc 只退出全屏不关窗 |

M3.8 仍未验证 / 需要人工确认：

1. **真实模型的出题质量**：干扰项是否合理、答案是否唯一、解析是否准确，
   需要真实文章多次实测后调 prompt；
2. **调级体感**：全对必升一档是否过于激进、3/5 保持是否合适，
   建议连续使用一周后看等级变化曲线；
3. **真实窗口排版**：题目选项较长时的换行、结果页多题解析的滚动体验需要
   真实桌面确认；
4. **重复检测**：当前设计是一篇文章一套题、只能提交一次；如果用户中途关闭，
   重开会继续用同一套题（这是有意的缓存行为）。

### 5.9 M3.9 阅读检测第一阶段（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **212 passed** |
| `ruff check` / `ruff format --check` | 通过 / 68 files formatted |
| 混合难度 | 5 道选择题覆盖低一档/当前/高一档/高两档；解析缺失难度回退本卷等级 |
| 短答 | 必须写 1~2 句；空短答强制 0 分；AI 给 0/1 分 |
| 错因 | 每题 error_types 与题目对齐；答对强制 none，短答固定 short_answer |
| 证据评分 | 高难度答对权重更高；本地 ratio 与 AI 建议冲突时强证据可压过 AI |
| 连续调级 | 第一次向上只记 pending(1/2)；第二次才升级；降级立即生效 |
| 存储 | schema v5 迁移、profile pending 字段、quiz 新字段往返 |
| 导出 | JSON / CSV 包含短答、错因、自评、建议等级、待定状态、证据强度 |
| 外部验收 | 两轮 GLM-5 复验：第 1 轮发现 1 个 P1 + 2 个 P2，已修复；第 2 轮通过。详见 [VERIFICATION_M3.9.md](VERIFICATION_M3.9.md) |

M3.9 仍未验证 / 需要人工确认：

1. **真实模型出题质量**：混合难度的配比是否被模型遵守、短答评分是否稳定；
2. **等级变化体感**：连续两次升级是否太慢/太快，需要连续使用一周观察；
3. **真实窗口排版**：短答输入框、自评下拉、结果页长解析在全屏/小窗口下的体验；
4. **降级策略**：目前降级不等待连续证据，是否会在疲劳答题时误降，需要实测。

### 5.10 M3.10 生词本回显复习求助（2026-11-19）

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest` | **218 passed** |
| `ruff check` / `ruff format --check` | 通过 / 68 files formatted |
| 有求助记录 | 详情显示时间、评分档位、用户输入、AI 帮记内容 |
| 无求助记录 | 显示引导文案；「认识 / 太简单」的空记录不展示 |
| 多条记录 | 最新在前，最多 5 条；更多时提示更早 N 次已省略 |
| 数据层 | 复用 `review_logs.note / advice`，无 schema 变更、导出行为不变 |
| 仓库层 | `list_help_for_word / count_help_for_word` 的过滤、倒序、并列时间、limit 边界有直接单测 |
| 外部验收 | 两轮 GLM-5 **同一 session**：第 1 轮 13 项通过 + 2 个 P3；第 2 轮确认无 P0/P1/P2。详见 [VERIFICATION_M3.10.md](VERIFICATION_M3.10.md) |

M3.10 仍未验证 / 需要人工确认：

1. **真实窗口排版**：右侧 360px 面板里长建议的换行与滚动体验需真实桌面确认；
2. **记录时效**：本次求助只有在点「完成」记录评分后才落库；如果用户生成
   AI 建议后直接关窗取消评分，该条建议不会出现在生词本（符合现有评分语义）。

### 5.11 M3.11 Phase 2 / Phase 3（2026-11-19）

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **273 passed** |
| `uv run ruff check` / `ruff format --check` | 通过 / 通过 |
| 本地后验 | 逻辑斯蒂模型 + 先验 + 历史衰减；全对/全错/混合/缺失难度/边界等级单测；简单题不升级、高难题升一档场景回放 |
| 自适应出题 | 3 道当前等级 → 按 2/3 正确率选边界 → 2 选择题 + 2 产出题；第一轮答案先落库、重开回显 |
| 盲评 | 独立调用、prompt 不含当前等级与难度；高于当前 → 正向证据，低于当前 → 负向证据；失败不阻塞 |
| 反作弊 | 选项打乱 + answer_index 重映射；同一天第二次检测进入练习模式，不写画像/证据/历史统计 |
| 每周复盘 | 同一周缓存；AI 只建议；最多一档、置信度 < 0.6 保持；用户确认后才写 profile |
| 迁移 | v5 → v6 原地迁移；旧检测不回填、stage 默认 2、可继续提交；新增 `level_evidence` / `weekly_reviews` |
| 导出 | JSON / CSV 十张表，含两张新表与全部新列 |
| 外部验收 | 4 轮静态审查（同一 session + 新 session）：P2 盲评方向 + 多个 P3 已修复，无 P0/P1/P2；外部 CLI 权限层始终不放行 pytest/ruff，动态检查以开发会话自检 273 passed 归档。详见 [VERIFICATION_M3.11.md](VERIFICATION_M3.11.md) |

M3.11 仍未验证 / 需要人工确认：

1. **真实模型出题/审核质量**：自适应边界题、改写题和审核 pass 的实际稳定性；
2. **后验阈值体感**：斜率 1.1、先验 0.4、置信度 0.6、历史衰减 0.75 仍是经验值；
3. **真实窗口排版**：两轮答题、长解析、产出题输入框和每周复盘弹窗需真实桌面确认；
4. **AI 调用成本**：一次完整检测最多 4 次调用（两轮出题 + 盲评 + 讲评）+ 审核，
   真实网关较慢时需要观察体感。

## 6. 关键实现说明（避免下次踩坑）

### 6.1 为什么连接测试用 chat.completions 而不是 /models

不少兼容网关没有实现 `/models`。发一句极短的 chat 请求最接近真实使用场景，
同时能验证模型权限。**不要加 `max_tokens` / `temperature` 参数**：
推理模型可能把 token 全花在思考上，或拒绝非默认温度，导致误报失败。

### 6.2 线程模型

- `services/*` 全是同步阻塞代码，**禁止**在 UI 线程调用；
- 所有调用包进 `gui.workers.TaskWorker`，通过 `succeeded(object)` /
  `failed(str)` / `cancelled()` 回主线程；
- 调用方必须持有 worker 引用（`self._worker`），否则 QThread 被 GC 会丢信号；
- M3 起 `cancel()` 是协作式取消：任务返回后若已取消就丢弃结果；
  长循环可调用 `workers.is_cancellation_requested()`；HTTP 请求本身无法中断，
  靠 30s 超时兜底，关闭窗口时页面 `shutdown()` 先等 3 秒，主窗口再兜底等待。

### 6.3 配置向导状态机

- 只有 `self._tested_config is not None` 时「下一步」才可点；
- 任意字段 `textChanged` / `valueChanged` 都会调用 `_invalidate_test()`；
- 测试成功后才 `ConfigStore.save()`，保存失败停在向导并弹窗；
- `reject()` / `closeEvent()` 在测试进行中会被拒绝，避免 QThread 被销毁。

### 6.4 openai 3.55 的差异

- 异常类（`AuthenticationError`、`APIConnectionError` 等）仍从 `openai` 顶层导入；
- SDK 内部用 `httpx2`，测试构造响应请 `import httpx2`，**不要 import httpx**；
- `OpenAI(..., timeout=30, max_retries=0)` 仍可用；
- 若升级 SDK，先跑 `test_english_learner_ai_client.py`。

### 6.5 页面导航索引

`main_window.py` 里页面栈顺序与侧边栏按钮索引**硬绑定**：
今日 0、阅读 1、生词本 2、复习 3、设置 4，代码里用 `PAGE_*` 常量表达。
加页面时必须同步改常量、`_pages`、侧边栏按钮三处。

### 6.6 SQLite 连接的线程模型（M1）

- AI 调用在 `TaskWorker` 子线程里，会写文章 / 生词，所以连接必须
  `check_same_thread=False`；
- `Database` 内部用一把 `RLock` 包住所有执行，**业务代码不要拿连接自己跑 SQL**；
- 连接是自动提交模式（`isolation_level=None`），需要原子性时用
  `Database.transaction()`（内部 `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`），
  且**不要嵌套**；
- `PRAGMA foreign_keys=ON` 已开启：删文章会级联删 highlights，
  words.article_id 置 NULL 但词条保留。

### 6.7 密度最高的三个坑（M1 实测）

1. `sqlite3.Row` 支持 `row["列名"]` 和 `row[0]`，但**不支持 `.get()`**；
   模型层的 `from_row` 统一处理，别在业务里直接碰 Row。
2. `words.lemma` 是 `COLLATE NOCASE UNIQUE`，`WHERE lemma = ?` 自动大小写不敏感；
   `INSERT ... ON CONFLICT(lemma)` 也靠这个索引生效，不要改成普通 TEXT。
3. `INSERT OR IGNORE` 的 `cursor.rowcount` 为 0 表示被 UNIQUE 忽略——
   “加入生词本是否新增”就靠它判断，别再写一次 SELECT。

### 6.8 JSON 契约与兜底（M1）

- 所有业务 prompt 都在 `services/prompts.py`，版本号 `PROMPT_VERSION`；
- `AIClient.complete_json()` 先带 `response_format={"type":"json_object"}`，
  识别到“不支持 response_format”时去掉参数重试一次；
- 无论哪种模式，业务层都必须走 `json_utils.parse_json_object()`：
  原样 → 去 ```json 围栏 → 截取首个 `{` 到末尾 `}`；
- 字段缺失一律回退默认值；CEFR 不在 A2/B1/B2/C1 时回退题库的 `level_hint`。

### 6.9 阅读器字符偏移

- 高亮存的是 `QTextEdit` 纯文本的**字符偏移**（`selectionStart/End`），
  不是字节偏移，也不是段落行号；
- 重绘时把偏移 clamp 到 `[0, len(content)]`，避免旧数据越界；
- 句子提取 `extract_sentence()` 以 `.!?\n` 为界，超长时以单词为中心截断到 300 字符；
- 双击取词走 `cursorForPosition(pos)` + `WordUnderCursor`，不要自己写正则分词。

### 6.10 每日文章缓存与“换一篇”

- `articles.article_date` 唯一，`get_or_create_today()` 命中即返回，不花 token；
- 校验失败重试一次；仍失败抛 `AIError`，界面显示重试按钮；
- “换一篇”= 删当天文章（级联删高亮）再生成，UI 必须二次确认；
- 并发写入同一天时 `ArticleRepo.insert` 抛 `DuplicateError`，
  `ArticleService` 捕获后回读已有文章，不会把错误抛给界面。

### 6.11 字体：全应用只有一处决定英文外观

- 原来全局 QSS 用 `Microsoft YaHei UI`，英文只能落到雅黑自带的拉丁字形上，
  小写偏挤、x-height 偏高，长文阅读很累；
- 现在内置三款 OFL 字体（`assets/fonts/`），英文外观由 `gui/fonts.py` 的字体链
  统一决定，通过全局 QSS 的 `*` 规则下发 —— **所有页面、所有控件自动生效，
  新加功能不需要单独设 font-family**（这是硬性约定，见 CLAUDE.md）；
- 可在设置页「外观 → 英语字体」切换：**Lexend（默认）** / Literata（衬线）/
  Georgia（系统自带、不打包）/ Inter（界面风）；字体来自 npm 包
  `@expo-google-fonts/*`（本机 Google Fonts / GitHub 都不通，只能走 npmmirror 取 TTF）；
- 注册必须发生在 `app.main()` 里、`setStyleSheet` **之前**
  （`QFontDatabase.addApplicationFont`），否则 QSS 里的族名解析不到会静默回退；
- 字体都不含 CJK，字体链末尾必须保留 `Microsoft YaHei UI`，Qt 逐字回退，
  中英混排才不会出现方块；
- 静态字重文件（400/500/600/700）在 Qt 里都归入同一个族，
  `font-weight: 600` 能匹配到 SemiBold，不要自己拼 “Lexend SemiBold” 这种族名；
- **唯一例外**：文章正文是 QTextDocument 的默认字体，QSS 管不到，
  必须调 `ArticleView.set_english_font(key)`；切换字体时主窗口同时做两件事 ——
  `QApplication.setStyleSheet(build_stylesheet(key))` + 通知阅读页同步正文；
- QSS 不支持 `line-height`：测评短文改用富文本 `<p style="line-height:180%">`
  （Qt 富文本引擎支持该属性），正文 QTextEdit 仍用 `QTextBlockFormat` 设 180%；
- 偏好存在 `config.json` 的 `ui.english_font`（旧字段 `reading_font` 仍可读），
  `ConfigStore.save_ui()` 只改 `ui` 段，`save()` 也会保留 `ui` 段，
  重配 AI 不会把字体选择冲掉；未知 key 一律回退默认。

### 6.12 打包成 exe（PyInstaller）

- 配置在项目根 `DailyEnglish.spec`，命令：`uv run pyinstaller --noconfirm --clean DailyEnglish.spec`，
  产物 `dist/DailyEnglish.exe`（单文件约 61MB，onedir 可另改 spec）；
- 三个必须踩对的点：
  1. `SPECPATH` 是 **spec 文件所在目录**（不是它的父目录），算路径时别多套一层 `.parent`；
  2. `pathex=[src]`：项目是 src 布局 + editable 安装，让 PyInstaller 直接从源码找包；
  3. `datas` 把 `src/english_learner/assets` 原样放到包内 `english_learner/assets`，
     这样 `gui/fonts.py` 里 `Path(__file__).parent.parent / "assets"` 在 `_MEIPASS`
     下也能算对 —— 否则内置字体会静默回退成系统字体，界面看不出报错；
- `console=False`：不留黑窗；未捕获异常走 `app.py` 的 excepthook 弹窗 + 写
  `%APPDATA%/EnglishLearner/logs/app.log`，所以打包版排查问题先看日志；
- `dist/`、`build/` 已在 `.gitignore` 里，不要入库；
- 首次启动 onefile 会把内容解压到临时目录，比直接跑源码慢几秒，属正常现象。

### 6.13 阅读反馈调难度（M1.5）

入口：阅读页标题栏「调整难度」（在「换一篇」右边）。

- **数据来源**：`WordRepo.list_highlighted_words(article_id)`（本篇标过的词，优先）
  + `WordRepo.list_created_on(today)`（今天查过的词，补充），去重后最多 8 个；
- **重新定级**：`build_level_feedback_messages()` 把当前等级/词汇量、今日文章、
  今日生词、用户反馈交给 AI，只返回评级 JSON；prompt 明确“反馈说太难就至少降一档”；
  解析复用 `assessment_service.assessment_result_from_data()`，非法等级回退当前等级；
- **重写文章**：`ArticleService.generate_article(..., strict_targets=False,
  learner_feedback=用户反馈)`：
  - `soft_targets` 只要求“尽量自然复用”今日生词，不做“必须出现”的本地校验；
  - 用户反馈也传给写作模型，要求优先高频词、缩短句子、控制生词密度；
- **顺序很重要**：先定级 → 再生成新文章 → 最后原子替换 + 保存 profile。
  任何一步失败，旧文章、旧高亮、旧画像都不动（`ArticleRepo.replace_by_date()`
  用同一事务删除旧文并插入新文）；
- **副作用**：旧文章高亮随级联删除消失，`words` 保留；`article_read` 可能 +1；
  新文章保留原 `topic`，主题不跳；
- **失败兜底**：解析失败/生成失败都抛 `AIError`，弹窗内显示错误并允许重试；
  用户也可直接关闭弹窗，原文章继续可读。

### 6.14 复习调度与两个新页面（M2）

- `services/srs.py` 分成两层：`schedule()` 是纯函数（只算新 Word，不落库），
  `ReviewService` 负责「算法 → 写回 → 记日志 → 打卡」的组合；想换 FSRS 只改这一层；
- 四档规则写在文件头表格里，关键常量：`MIN_EASE=1.3`、`MASTERED_INTERVAL_DAYS=30`、
  `MAX_INTERVAL_DAYS=365`；前两次成功复习固定 1 / 6 天，之后才乘易度；
- `due_date` 统一 `YYYY-MM-DD` 字符串，SQL 直接 `due_date <= today` 比较，
  不需要转日期；跨时区/换系统日期只影响“今天”的取值，数据本身不受影响；
- `WordRepo.save_schedule()` **只写调度字段**，不碰释义，避免复习把用户手改的
  释义覆盖掉；`WordRepo.update_meaning()` 反过来只改释义；
- `review_logs` 只追加、不修改；外键 `ON DELETE CASCADE`，删词自动清理历史；
- 复习页**不用 TaskWorker**（纯本地 SQLite，毫秒级）；会话状态在页面实例里，
  切到别的页面再回来不会重置当前卡片；
- 快捷键用 `QShortcut` + `WidgetWithChildrenShortcut`，只在复习页内生效；
  空格/回车翻面，1~4 评分，翻面前评分会被忽略；
- 生词本页用 `QTableWidget` 全量重建，刷新时按 `word.id` 恢复选中行；
  筛选下拉的 `currentData()` 就是 `words.status`，全部为 `None`；
- 编辑释义后的脏检查已在 M3 补齐：切页 / 筛选 / 搜索 / 排序 / 关窗都会提示，
  实现见 6.15 的 `can_deactivate()` 协议。

### 6.15 M3：加密、导出、取消与脏检查

- **DPAPI 密文格式**：`dpapi:` + base64(CryptProtectData 输出)。读取时只有带前缀的
  值才会解密，旧版明文原样返回；保存时若发现已经是 `dpapi:` 前缀则不二次加密。
  加密失败（非 Windows / API 失败）回退明文并写日志 —— 宁可降级也不能让用户
  存不了配置。跨用户复制配置会在 `ConfigStore.load()` 抛 `ConfigError`，
  应用走“配置损坏 → 重新配置”的既有路径。
- **导出格式**：JSON 是 `{app, version, exported_at, counts, profile, articles,
  words, highlights, review_logs, daily_stats}`；CSV 每表一个文件，编码
  `utf-8-sig`（Excel 中文），列表字段用 `"; "` 连接。导出服务只读仓库，
  新增的 `list_all*` 方法不要写业务判断。UI 侧 JSON 选文件、CSV 选目录后
  自动建 `daily-english-export-<时间戳>/` 子目录，避免覆盖旧导出。
- **取消的边界**：`cancelled` 信号不是“立刻停止”，只是“结果作废”。
  页面统一把 `cancelled` 接到自己的 `_on_worker_finished`，清理 busy 状态；
  不要在 `cancel()` 之后假设线程已经结束。
- **页面关闭协议**：任何持有 worker 的页面实现
  `shutdown(timeout_ms=3000) -> TaskWorker | None`；返回仍存活的 worker 表示
  超时。`MainWindow._stop_worker_threads()` 收集这些返回值并最终 `wait()`，
  这是消除 QThread 销毁警告的最后一道保险。
- **脏检查协议**：页面可实现 `can_deactivate() -> bool`；`MainWindow._go()` 在
  切页前、`closeEvent()` 在关闭前都会调用，返回 False 就取消这次操作并恢复
  导航按钮状态。生词本用 `_baseline`（进入详情时的原始释义）判断是否有改动。
- **性能取舍**：生词本仍在 UI 线程全量查询 + 重建表格（几百到几千条），
  但重建期间 `setUpdatesEnabled(False)`；如果将来上万条，再上分页或模型视图，
  不要在没有实测的情况下提前引入 `QAbstractTableModel`。

### 6.16 M3.5：原形、词形缓存、记忆方法、复习求助

- **schema v2**：`SCHEMA_SQL` 里的新库直接带 `words.mnemonic`、
  `review_logs.note/advice`、`word_forms`；旧库在 `_apply_schema()` 里按版本
  `ALTER TABLE ADD COLUMN`。加列前用 `PRAGMA table_info` 判断，重复启动幂等。
  **不要删列重建表**，SQLite 的 ADD COLUMN 足够且不会丢数据。
- **原形判定**：`DictionaryService.lookup()` 先查 `word_forms`（文章词形），
  再查 `words.lemma`，都未命中才调 AI。AI 返回的 `lemma` 用
  `normalize_lemma()` 校验，非法时回退当前词形；`display_form` 以**原文中
  实际选中的文本**为准（保留大小写、去首尾标点），不信任模型回填。
- **词形缓存**：`word_forms.form` 是 `COLLATE NOCASE PRIMARY KEY`，
  `remember_form()` 用 `ON CONFLICT DO UPDATE` 更新映射；删词时外键级联清理。
  旧版本已入库的词不回填词形，但这些词按原形查询仍能命中 `words.lemma`。
- **记忆方法**：查词 prompt 一次返回 `mnemonic`；老词条用
  `DictionaryService.generate_mnemonic()` + `WordRepo.update_mnemonic()`。
  `upsert` 对 mnemonic 采用“新值非空才覆盖”，避免查词把用户/复习中更新过的
  记忆方法冲掉；`display_form` 也是保留第一次记录的文章形态。
- **复习求助**：只有 rating 1/2 触发。流程是「弹窗收集原因 → 可选 AI →
  然后才调用 `ReviewService.review()`」，所以用户 Esc/关闭弹窗**不会记录评分**；
  “跳过，直接记录”会带着原因落库但不调 AI。AI 失败时评分仍可跳过完成，
  保证离线复习可用。
- **日志**：`review_logs.note` 存用户原因（截断 500 字），`advice` 存 AI 建议
  （截断 1000 字）；两者只用于回顾/导出，不参与调度。
- **弹窗细节**：复习页 `_collect_review_help()` 在弹窗期间禁用 1~4 快捷键，
  避免在原因输入框里按数字误触评分；弹窗请求进行中拒绝关闭（同其他 AI 弹窗）。
- **导出**：新增 `word_forms` 表和 `words.mnemonic`、`review_logs.note/advice`
  列；JSON 自动包含，CSV 列顺序在 `export_service.CSV_COLUMNS` 固定。
- **prompt 版本**：M3.5 时为 `PROMPT_VERSION = "m3-2"`（M3.8 起为 `m3-8`），新增
  `build_mnemonic_messages` / `build_review_help_messages`，查词契约新增
  `lemma 必须是原形` 与 `mnemonic` 字段。
- **换一篇的上下文**：只有用户在「换一篇」弹窗里写了要求时，
  `regenerate_today()` 才把**当前文章**传给 prompt（标题、等级、主题、正文），
  让模型理解“这个话题 / 这种结构”等指代，并明确要求不要逐句改写；
  没写要求时不带原文，避免模型被带偏、也节省 token。
  同时，有用户要求时当前标题不再列进 `avoid_titles`，避免“参考这篇”和
  “禁止重复这个主题”两条指令互相打架。prompt builder 里也做了兜底：
  `user_request` 为空时忽略 `current_article`。

### 6.17 M3.6：正文全宽与悬浮释义卡

- **为什么不用 QSplitter**：右侧固定栏在没查词时是一大片空白，而且挤压正文宽度。
  现在 `ReaderPage` 直接用 `layout.addWidget(self._view, 1)`，`WordPanel` 以
  `self` 为 parent 但不加入布局，按几何位置浮在页面上，`raise_()` 保证在最上层。
- **为什么不用 Qt.Popup**：Popup 是独立窗口，会抢鼠标抓取、样式和主窗口不完全一致，
  也难保证滚动/切页时的收起行为；页内子控件更可控，阴影用
  `QGraphicsDropShadowEffect` 实现。
- **取词位置**：`ArticleView.word_at_position()` 在取词时记录
  `cursorRect(start).united(cursorRect(end))`（视口坐标）；`ReaderPage` 用
  `viewport().mapToGlobal()` + `mapFromGlobal()` 转成页面坐标。
  `cursorRect` 依赖文档布局，offscreen 下未显示时可能返回默认值，真实窗口正常。
- **定位规则**：默认贴在单词下方 10px、水平居中；下方放不下翻到单词上方；
  x/y 都夹在页面边距内，卡片高度最大 480px，超出由内部滚动区消化。
- **收起时机**：正文 `mousePressEvent` 发 `background_clicked`、滚动条
  `valueChanged`、Esc 快捷键、卡片关闭按钮、加载/切换文章、页面 resize 都会隐藏。
  收起时同时清空 `_pending`：查询中收起卡片后，迟到的结果会被丢弃，不会再次弹出。
- **WordPanel 结构**：宽度固定 380；标题/释义/例句/记忆方法放进 `QScrollArea`，
  「加入生词本」和状态固定在底部；关闭按钮 `×` 通过 `close_requested` 信号通知页面。
  它不再显示占位文案（卡片未选中时直接隐藏）。
- **测试注意**：不要断言 offscreen 下的具体 `cursorRect` 像素，测试应验证
  “矩形已记录 + 页面定位/夹取 + 各收起路径”。

### 6.18 M3.7：查词缓存 vs 生词本（in_vocabulary）

- **问题根源**：`lookup()` 为了缓存释义会把词写进 `words`，而 M1/M2 的生词本
  直接 `list_all()` 全表，于是“双击查词”看起来等于“自动加入”。M3.7 之前只有
  `highlights` 能间接区分，但它会随文章替换被级联删除，不可靠。
- **字段语义**：`words.in_vocabulary`：0 = 仅查词缓存，1 = 用户明确加入。
  `DictionaryService.add_to_vocabulary()` 先 `mark_in_vocabulary()` 再插高亮；
  查词 `upsert` 永远写 0，但 SQL 里做了
  `CASE WHEN words.in_vocabulary = 1 THEN 1 ELSE excluded.in_vocabulary END`，
  已加入的词不会被后续查词降级。
- **所有“生词本口径”的查询都要过滤 `in_vocabulary = 1`**：
  `WordRepo.list_all / count / list_due / list_created_on / count_unmastered`、
  `StatsRepo.count_due`；导出是例外，用 `list_all(include_cache=True)` 保留全量。
  新增查询时先想清楚：这是“生词本”还是“查词缓存”。
- **迁移 v2 → v3**：`ALTER TABLE words ADD COLUMN in_vocabulary ...` 后，
  把旧词全部 `UPDATE ... SET in_vocabulary = 1`。旧版本无法区分，保留比误删安全；
  已在真实库副本上验证 13 个旧词全部保留。
- **UI 文案**：缓存命中时提示“已有释义缓存，点加入才会收进生词本”；
  已加入的词按钮显示“加入本篇高亮”，不会重复入本。
- **排版修复**：生词本右侧详情字段变多后，QSplitter 分配的高度不足会导致
  QLabel/QPlainTextEdit 互相重叠；把 `_detail_body` 放进 `QScrollArea`
  （隐藏/显示滚动区而不是 body）即可，内容少时无滚动条、内容多时可滚动。

### 6.19 M3.8：阅读检测与动态调级

- **表结构（schema v4）**：`reading_quizzes` 一篇文章一行，`article_id UNIQUE` +
  `ON DELETE CASCADE`；题目、作答、解析都存 JSON 文本，结果字段平铺
  （`score / previous_level / result_level / feedback / completed_at`）。
  `completed_at IS NULL` 是“已出题未提交”的唯一判据，不要用 `score == 0` 判断。
- **两层缓存**：`get_or_create()` 先查 `reading_quizzes`，命中就完全不再调 AI；
  判分失败时题目保留，用户重试只重新请求讲评。每日文章缓存与检测缓存相互独立，
  但文章被“换一篇/调整难度”替换时会级联删掉旧检测（旧题已不对应新正文）。
- **本地判分**：答案下标在出题时就由 AI 给出，提交后 `submit()` 在本地算
  `score`；讲评 prompt 明确“得分已由系统算好，不要重算”，避免模型算术漂移。
- **调级硬约束**（`constrain_level`，纯函数）：
  - 全对（5/5）→ 至少升一档；
  - 答对不到一半 → 至少降一档；
  - 其他情况尊重 AI，但夹在当前等级 ±1 内；A1 归一到 A2，非法等级回退当前。
  这样即使模型给出跨两级或与得分矛盾的结论，用户画像也不会跳变。
- **失败语义**：调级 JSON 解析失败 → 抛 `AIError`，profile 不动、quiz 仍未完成；
  只有讲评成功后才依次保存 profile 与 quiz 结果。界面右上角始终可重试。
- **UI 状态机**：`loading → quiz → result` 三页；已完成的检测再次打开直接进
  `result`，不重新判分；阅读页按钮文案据此切换为「查看答题结果」。
  出题/判分都在弹窗自己的 `TaskWorker` 里，进行中拒绝关闭。
- **全屏**：`setWindowFlags(WindowMinMaxButtonsHint)` 必须在 `setModal(True)` 之前
  （重建原生窗口会丢模态状态）；F11 用 `WidgetWithChildrenShortcut`，
  `keyPressEvent` 拦截“全屏状态下的 Esc”，避免用户想退出全屏却把弹窗关掉。
- **循环导入**：`gui/pages/__init__.py` 改为模块级 `__getattr__` 按需导入。
  弹窗要 import `pages.common`，如果包初始化时急切加载 reader，会形成
  “弹窗 → pages 包 → reader → 弹窗”的循环；按需加载后单独跑弹窗测试也正常。
- **导出**：`snapshot()` 中 `reading_quizzes` 用 `asdict` + 补充 `total`；
  CSV 的 `questions` 是嵌套结构，`_csv_value` 对“含 dict/list 的列表”整体
  `json.dumps`，简单列表仍用 `; ` 拼接。

### 6.20 M3.9：混合难度、短答与连续证据

- **schema v5**：`profile` 增加 `pending_level / pending_count`；
  `reading_quizzes` 增加 `error_types_json`、`short_answer_text`、
  `short_answer_score`、`self_report`、`recommended_level`、`pending_level`、
  `pending_count`、`evidence_ratio`。旧库用 `_add_column` 原地升级。
- **题目结构**：`QuizQuestion.kind` 区分 `choice / short`；选择题有
  `options + answer_index`，短答题有 `reference_answer`。每题都有
  `skill / difficulty`，AI 不返回时分别回退 `unknown` / 本卷等级。
- **混合难度**：`prompts._quiz_difficulty_plan()` 根据当前等级算出
  低一档/当前/高一档/高两档；prompt 要求 2+1+1+1 配比，再加 1 道短答。
  本地允许 3~6 道选择题，但必须有且只有 1 道短答，否则重试。
- **证据评分**（`score_quiz_evidence`，纯函数）：
  - 同档答对 +1、答错 -1；
  - 每高一档，答对额外 +0.5、答错少扣 0.5；每低一档反之；
  - 短答按题目难度同样计分；自评“轻松/吃力”只做 ±0.25 弱修正；
  - `ratio = points / max(1, 题数)`，夹在 [-1, 1]；
  - `>= 0.45` 偏强、`<= -0.35` 偏弱、其余中性。
- **AI 与证据结合**（`decide_recommended_level`）：AI 建议先归一到合法等级，
  最多一档；同向直接走，一方中性听另一方；双方相反时，只有
  `abs(ratio) >= 0.6` 的强证据才压过 AI，否则保持不动。
- **连续证据**（`apply_level_policy`）：向上需要 2 次同向建议，第一次只写
  `pending_level / pending_count`；第二次才真正改 `cefr_level`。中间出现
  “保持当前等级”会清空待定；降级立即生效，保护学习体验。
- **短答评分**：AI 返回 0/1；空短答本地强制 0，避免“没写也得 1 分”；
  短答反馈优先写进 `explanations` 的短答位置，缺失时用
  `short_answer_feedback` 兜底。
- **错因**：`_parse_error_types()` 把 AI 结果整理成与题目等长的元组；
  选择题答对强制 `none`，短答固定 `short_answer`，非法值归 `unknown`。
- **UI**：答题页有自评下拉、短答输入框、难度/题型标签；结果页显示
  “选择题 X/Y · 短答 0/1 · 证据强度 ±x.xx”，等级卡区分
  “保持 / 建议升级（1/2）/ 已升级 / AI 建议但本次保持”。
- **测试注意**：证据与连续策略都是纯函数，直接单测；服务测试用两个不同
  日期的文章模拟“连续两次向上”。

### 6.21 M3.10：生词本回显复习求助

- **数据来源**：求助原因与 AI 建议在 M3.5 就已写入 `review_logs.note / advice`，
  本次只做展示，**不要**再往 `words` 表加字段，也不要改变求助弹窗/评分流程；
- **查询**：`ReviewRepo.list_help_for_word(word_id, limit=5)` 在 SQL 里用
  `TRIM(note) <> '' OR TRIM(advice) <> ''` 过滤后按 `reviewed_at DESC, id DESC`
  取最近 5 条；`count_help_for_word()` 单独统计总数用于“更早的 N 次”。
  过滤必须下推到 SQL，不能先按普通复习记录取 N 条再过滤，否则大量
  「认识 / 太简单」记录会把更早的求助挤出窗口；
- **刷新时机**：生词本 `on_activated()` → `refresh()` → 重建表格并恢复选中行
  → `_show_detail()`，所以复习完切到生词本即可看到新记录，无需额外信号；
- **展示口径**：标题行 `YYYY-MM-DD HH:MM · 忘记/模糊`，正文分
  「我的输入」「AI 帮记」两行；只有其中一项时另一行省略；
- **空状态**：没有记录时显示引导文案，不隐藏整个区块，避免用户以为功能缺失。

### 6.22 M3.11：本地后验、自适应出题与每周复盘

- **schema v6**：`profile.level_confidence / level_updated_reason`；
  `words.cefr_band`；`reading_quizzes.productions_json / level_confidence /
  posterior_json / practice_mode / stage`；新增 `level_evidence`、
  `weekly_reviews`。`idx_words_band` 必须等 ALTER TABLE 之后再建，
  否则旧库执行 `SCHEMA_SQL` 会报 `no such column: cefr_band`（已实测踩过）。
- **估计器（`level_estimator.py`）**：纯函数。先验以当前等级为中心
  （距离权重 0.4），似然用 `1/(1+exp(1.1*(d-L)))`，概率裁剪到 [0.02, 0.98]。
  决策只在“当前 ±1 档”窗口内取峰值；窗口概率 < 0.6 时保持当前等级。
  `confidence` 是**窗口内**峰值占比，不是全四档的 MAP 概率——这样“B1 全对高难度题”
  不会因为 C1 分走部分概率而显示成低置信。
- **证据分层**：本次检测的题目观测 weight=1.0；盲评 0.5；自评 0.25；
  复习词档最多 0.8。**只有题目/产出题观测写入 `level_evidence`**；盲评与自评
  只影响当前这次后验，不参与历史回放（否则 0.5 的盲评会沿衰减回放累积到 ~1.5）。
  历史检测按 0.75^age 回放最近 5 次。练习模式不写证据，也不进入讲评历史/难度统计。
- **两阶段状态机**：`stage=1` 只有 3 道题；`advance_stage()` 先把第一轮答案写入
  `answers_json`（只写前 3 个），再追加阶段二题目并置 `stage=2`。重开弹窗时
  第一轮答案锁定展示，用户只需补第二轮。
- **盲评**：`build_blind_level_messages()` 不传 profile 等级、也不展示题目难度
  （第一轮难度恒为当前等级，展示会反推出画像）；盲评失败只记日志、不阻塞讲评与
  本地后验。盲评建议按方向编码：高于当前 → `(盲评档, correct=True)` 向上；
  低于当前 → `(盲评档, correct=False)` 向下；相同不产生观测。讲评 prompt 才包含
  当前等级、历史摘要与难度/技能正确率。
- **题目审核**：`_review_questions()` 调用一次审核 prompt，按 `invalid_indices`
  剔除；审核失败（网络/JSON）保留原题。审核后数量不足才重出，最多两次。
- **选项打乱**：`shuffle_question_options()` 重排选项并重映射 `answer_index`；
  服务默认开启，测试可传 `shuffle_options=False` 以固定下标作答。
- **每日一次正式检测**：用 `level_evidence.count_on(evidence_date)` 判断，
  而不是 `reading_quizzes.article_date`——文章被“换一篇”替换时旧检测会级联删除，
  只有证据表能记住“今天已经正式测过”。练习模式不写 profile/证据。
- **每周复盘**：`WeeklyReviewService.get_or_generate()` 按周一缓存；
  AI 建议经 `_clamp_suggestion()` 限制为最多一档、置信度 < 0.6 时保持；
  `apply_suggestion()` 才写 profile 并标记 `applied_*`。
- **导出**：JSON/CSV 新增 `level_evidence`、`weekly_reviews` 两张表；
  `reading_quizzes` 增加 productions/后验/练习模式/阶段列。

## 7. M1 详细任务（已全部完成）

> M1 目标：跑通「测评定级 → 生成今日文章 → 阅读划词 → 生词入本」，
> 复习留到 M2。每个任务完成即补测试，不要攒到最后。
>
> **状态（2026-11-18）：M1-1 ~ M1-7 全部完成**，验证见 5.2。
> 实际实现与原计划的差异（以实际代码为准）：
>
> 1. 新增 `services/app_context.py`（AppContext 门面）与 `services/ai_protocol.py`
>    （`TextCompleter` 协议），页面/服务只依赖这两个抽象；
> 2. `AnswerSheet` 用选项下标元组（`comprehension` / `vocabulary`），未引入题目 id；
> 3. 查词返回 `LookupResult(word, cached)` 而不是裸 `WordDefinition`，
>    因为加入高亮需要 `word.id`；`WordDefinition.from_word()` 仍保留；
> 4. `AIClient.complete_json()` 在服务端疑似不支持 `response_format` 时
>    自动去掉该参数重试一次，业务层再叠加本地三档 JSON 解析；
> 5. 测评默认固定 B1 套（`DEFAULT_SET_KEY = "b"`），A2/B2 套已写好但 UI 暂不选择；
> 6. “今日待复习”按 M1 约定显示未掌握词数（`WordRepo.count_unmastered`），
>    `StatsRepo.count_due` 已实现并测试，M2 首页直接切；
> 7. 阅读页把“生成/重试”做成标题栏按钮；进入页面且当天无文章时自动生成。

### M1-1 存储层（建议先做）

新增：

```
src/english_learner/models/__init__.py
src/english_learner/models/entities.py      # 领域 dataclass + from_row
src/english_learner/storage/__init__.py
src/english_learner/storage/db.py           # 连接、建表、迁移
src/english_learner/storage/repositories.py # 纯 SQL 仓库
```

- DB 路径：`config_dir() / "data.db"`，构造函数允许传入临时路径供测试。
- 连接：`row_factory=sqlite3.Row`、`PRAGMA foreign_keys=ON`、
  `PRAGMA journal_mode=WAL`。
- 迁移：`PRAGMA user_version`，当前 `SCHEMA_VERSION = 1`；
  初始化可重复执行（`CREATE TABLE IF NOT EXISTS`）。

建议 schema（M1 一次建全，M2 不再迁移）：

```sql
CREATE TABLE IF NOT EXISTS profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cefr_level      TEXT    NOT NULL,
    vocab_estimate  INTEGER NOT NULL DEFAULT 0,
    strengths_json  TEXT    NOT NULL DEFAULT '[]',
    weaknesses_json TEXT    NOT NULL DEFAULT '[]',
    comment         TEXT    NOT NULL DEFAULT '',
    assessed_at     TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    article_date     TEXT    NOT NULL UNIQUE,   -- YYYY-MM-DD，一天一篇
    title            TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    level            TEXT    NOT NULL,
    topic            TEXT    NOT NULL DEFAULT '',
    word_count       INTEGER NOT NULL DEFAULT 0,
    target_words_json TEXT   NOT NULL DEFAULT '[]',
    read_at          TEXT,
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    lemma           TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    display_form    TEXT    NOT NULL DEFAULT '',
    phonetic        TEXT    NOT NULL DEFAULT '',
    pos             TEXT    NOT NULL DEFAULT '',
    meaning_cn      TEXT    NOT NULL DEFAULT '',
    meaning_en      TEXT    NOT NULL DEFAULT '',
    example_en      TEXT    NOT NULL DEFAULT '',
    example_cn      TEXT    NOT NULL DEFAULT '',
    context_sentence TEXT   NOT NULL DEFAULT '',
    article_id      INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    status          TEXT    NOT NULL DEFAULT 'new',  -- new/learning/mastered
    ease            REAL    NOT NULL DEFAULT 2.5,
    interval_days   INTEGER NOT NULL DEFAULT 0,
    repetitions     INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS highlights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    word_id      INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL,
    end_offset   INTEGER NOT NULL,
    sentence     TEXT    NOT NULL DEFAULT '',
    UNIQUE (article_id, start_offset, end_offset)
);

CREATE TABLE IF NOT EXISTS review_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    rating        INTEGER NOT NULL,          -- 1/2/3/4
    prev_interval INTEGER NOT NULL,
    next_interval INTEGER NOT NULL,
    reviewed_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    stat_date    TEXT PRIMARY KEY,           -- YYYY-MM-DD
    article_read INTEGER NOT NULL DEFAULT 0,
    words_added  INTEGER NOT NULL DEFAULT 0,
    reviews_done INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_words_due ON words(due_date);
CREATE INDEX IF NOT EXISTS idx_highlights_article ON highlights(article_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_word ON review_logs(word_id);
```

仓库方法（建议最小集）：

- `ProfileRepo.get() / save(profile)`
- `ArticleRepo.get_by_date(date) / insert(article) / delete_by_date(date) / list_recent(limit)`
- `WordRepo.get_by_lemma(lemma) / upsert(word) / add_highlight(...) / list_highlights(article_id) / list_all(status=None) / count()`
- `StatsRepo.bump(date, article_read=0, words_added=0, reviews_done=0) / get(date) / count_due(today)`

验收：临时 DB 连续初始化两次无异常；CRUD 往返一致；同一天重复插文章触发唯一约束；
同一个 lemma 大小写不同视为同一词。

### M1-2 领域模型

`models/entities.py` 用 frozen dataclass：

- `Profile(cefr_level, vocab_estimate, strengths, weaknesses, comment, assessed_at)`
- `Article(id, article_date, title, content, level, topic, word_count, target_words, read_at, created_at)`
- `Word(...)` 对应 words 表
- `Highlight(article_id, word_id, start_offset, end_offset, sentence)`
- `WordDefinition(lemma, display_form, phonetic, pos, meaning_cn, meaning_en, example_en, example_cn)`
- `AssessmentResult(cefr, vocab_estimate, strengths, weaknesses, comment)`

每个类提供 `from_row(row)`；写 `to_dict()` 只在需要时加，别过度设计。

### M1-3 摸底测评

新增：

```
src/english_learner/services/assessment_bank.py   # 3 套固定题
src/english_learner/services/assessment_service.py
src/english_learner/services/json_utils.py        # parse_json_object 兜底
src/english_learner/gui/assessment_dialog.py
```

题库结构（纯 Python dataclass，便于类型检查和测试）：

```python
@dataclass(frozen=True)
class Question:
    prompt: str
    options: tuple[str, ...]
    answer_index: int

@dataclass(frozen=True)
class AssessmentSet:
    key: str                 # "a" / "b" / "c"
    level_hint: str          # "A2" / "B1" / "B2"
    passage: str             # 150~180 词
    comprehension: tuple[Question, ...]   # 5 题
    vocabulary: tuple[Question, ...]      # 5 题
    translation_prompt: str               # 1 段中译英或英译中
```

流程：

1. 首次测评固定用 B1 套（`level_hint="B1"`），不要一开始就自适应，先保证可复现；
2. 用户作答（选择题 + 翻译文本）；
3. 本地先算出客观题得分，把题目、标准答案、用户答案、翻译一起交给 AI，
   要求只输出评分 JSON（AI 不重新出题）；
4. 结果落库 `profile`；
5. AI 评分失败时，界面允许手动从 A2/B1/B2/C1 选一个等级，**不能卡死流程**。

评分 JSON 契约：

```json
{
  "cefr": "B1",
  "vocab_estimate": 3800,
  "strengths": ["阅读理解", "基础语法"],
  "weaknesses": ["学术词汇", "长难句"],
  "comment": "整体 B1 偏上，建议先扩大阅读量。"
}
```

解析要点：`parse_json_object` 先原样 `json.loads`，失败则去掉 ```json 围栏，
再失败则截取第一个 `{` 到最后一个 `}`。字段缺失时给默认值，`cefr` 必须在
A2/B1/B2/C1 中，否则回退到 `level_hint`。

验收：mock AI 返回合法 JSON → profile 入库；返回带围栏的 JSON → 仍能解析；
返回垃圾 → 抛 AIError，界面出现手动选级入口。

### M1-4 每日文章生成

新增：

```
src/english_learner/services/prompts.py          # 所有 prompt 集中在这里，带版本号
src/english_learner/services/article_service.py
```

接口建议：

```python
def get_or_create_today(profile: Profile, due_words: list[str]) -> Article
def generate_article(level: str, target_words: list[str], topic: str) -> ArticleDraft
def regenerate_today() -> Article
```

生成契约（JSON）：

```json
{
  "title": "The Quiet Power of Small Habits",
  "level": "B1",
  "topic": "psychology",
  "paragraphs": ["...", "...", "..."],
  "target_words": ["resilient", "consistent"],
  "glossary": [{"word": "resilient", "meaning_cn": "有韧性的"}]
}
```

本地校验（不合格就重试一次，仍失败抛 AIError）：

- `title` 非空，`level` 等于请求等级（允许大小写差异）；
- `paragraphs` ≥ 3，合并后词数在 200~400（目标 250~350）；
- `target_words` 里的每个词都出现在正文中（忽略大小写）；
- 段落不能是空串；去掉首尾空白后重新拼接 `content = "\n\n".join(paragraphs)`；
- `word_count = len(content.split())`。

缓存策略：`articles.article_date` 唯一；当天已有则直接返回。
“换一篇” = 删除当天记录后重新生成（UI 需二次确认，避免误删已标生词的高亮）。
注意：删除文章会级联删除 highlights，但 words 保留（生词本不依赖某篇文章）。

UI 触发：进入「今日」或「阅读」页时，若今天没有文章，后台生成并显示
“正在生成今日文章…”；失败显示重试按钮。生成中禁用“换一篇”。

Prompt 要点：

- 明确 CEFR 等级、词数、段落数、主题；
- 要求“自然复现 target_words，每个最多出现 2 次，不要堆砌”；
- 要求只输出 JSON，不加解释；
- 把 prompt 模板和版本号写进 `prompts.py`，便于以后对比效果。

### M1-5 阅读器与划词释义

新增 / 修改：

```
src/english_learner/gui/pages/reader.py          # 替换占位
src/english_learner/gui/widgets/__init__.py
src/english_learner/gui/widgets/word_panel.py    # 释义卡（M3.6 起为悬浮卡）
src/english_learner/gui/widgets/article_view.py  # QTextEdit 子类（双击取词）
src/english_learner/services/dictionary_service.py
```

阅读器实现建议：

- 用只读 `QTextEdit` + `setPlainText(article.content)`；
- 双击：`cursorForPosition(event.pos())` → `QTextCursor.WordUnderCursor`
  → 取选中文本、`selectionStart/End`、所在句子；
- 高亮：`QTextEdit.ExtraSelection` + `QTextCharFormat.setBackground(...)`，
  从 `highlights` 表读取偏移量；加入生词后立即重绘；
- 字号 / 行距用 `QTextBlockFormat` 设置（M1 可先固定 17px / 1.8 倍）；
- 句子边界：以 `.!?` 为界，向前后各找最近的分隔符，限制长度 300 字符。

查词服务：

```python
def lookup(word: str, sentence: str, article_id: int, level: str) -> WordDefinition
```

1. 规范化 lemma：`strip().lower()`，去掉首尾标点；
2. 先查 `words` 表，命中直接返回（不花 token）；
3. 未命中调用 AI，要求“结合上下文给出语境义”，JSON 契约：

```json
{
  "lemma": "resilient",
  "phonetic": "/rɪˈzɪliənt/",
  "pos": "adj.",
  "meaning_cn": "有韧性的；能迅速恢复的",
  "meaning_en": "able to recover quickly from difficulties",
  "example_en": "Children are often more resilient than adults expect.",
  "example_cn": "孩子往往比大人想象的更有韧性。"
}
```

4. 结果写 `words`，`status='new'`，`due_date=today`（M2 复习从今天开始）。

加入生词本：

- `WordRepo.upsert()` 以 lemma 唯一；
- 插入 `highlights(article_id, word_id, start_offset, end_offset, sentence)`；
- `daily_stats.words_added += 1`；
- 重复添加同一位置要幂等（UNIQUE 约束 + `INSERT OR IGNORE`）。

验收（mock AI + 临时 DB）：

- 双击单词 → 加载中 → 释义卡出现；
- 加入生词本 → 正文高亮，重启应用后高亮仍在；
- 同一个词第二次查不调用 AI；
- 标点、数字、超长选中不崩溃（不查或提示“请选择一个英文单词”）。

### M1-6 首页接真实数据

- `TodayPage` 注入 ProfileRepo / ArticleRepo / StatsRepo（或一个 `AppState` 门面）；
- 连续天数：从 `daily_stats` 倒推连续日期，断一天归零；
- 待复习：M1 先显示 `words` 中 `status != 'mastered'` 的数量，M2 改为 `due_date <= today`；
- 累计生词：`WordRepo.count()`；
- 今日文章：存在则显示标题 + “继续阅读”，不存在则显示生成按钮。

### M1-7 集成与测试

新增测试建议：

- `tests/test_english_learner_storage.py`：建表幂等、CRUD、唯一约束、级联删除；
- `tests/test_english_learner_json_utils.py`：围栏 JSON、前后垃圾字符、缺失字段；
- `tests/test_english_learner_assessment.py`：mock AI 评分、解析失败回退、手动等级；
- `tests/test_english_learner_article.py`：合法/非法生成、重试、当天缓存；
- `tests/test_english_learner_reader.py`：offscreen 构造阅读器、双击取词（可只测纯函数）；
- GUI 冒烟：`MainWindow` 带临时 DB 能构造。

手工验收（用真实 Key）：

1. 删除配置 → 首次启动 → 配置 → 连接失败一次（故意填错 Key）→ 修改 → 成功；
2. 完成测评 → 首页显示等级与词汇量；
3. 今日页生成文章 → 进阅读器 → 双击 3 个单词 → 加入生词本；
4. 关闭应用重开 → 文章仍在、高亮仍在、生词本有 3 个词、没有重复调用生成；
5. 断网后打开应用 → 能看缓存文章与生词本，不崩溃。

### M1-8 阅读反馈调难度（M1.5 追加需求，已完成）

用户反馈“第一篇文章生词太多，打击积极性”，因此增加：

- 阅读页「换一篇」右侧新增「调整难度」；
- 弹窗展示当前等级、今日文章、今日生词；提供 4 个快捷选项 + 自由文本；
- 提交后在 TaskWorker 里执行 `LevelFeedbackService.adjust()`：
  1. AI 结合反馈与今日生词重新定级（太难至少降一档）；
  2. 按新等级 + 用户反馈重写文章，今日生词软约束复用；
  3. 原子替换当天文章（旧高亮清除、生词保留），保存新 profile；
- 失败时旧文章与旧画像保持不变，弹窗内显示原因，可重试或取消。

对应测试：`tests/test_english_learner_feedback.py`（12 个用例）+
存储层 `replace_by_date / list_highlighted_words / list_created_on`
+ 文章软约束与“换一篇失败保留旧文”+ GUI 冒烟。

## 8. M2 任务（已完成，记录实际交付）

> 状态（2026-11-19）：全部完成，验证见 5.3。实际实现与原始预告的差异如下。

- `services/srs.py`：简化 SM-2；四档 rating 1=忘记 / 2=模糊 / 3=认识 / 4=太简单；
  忘记时 `repetitions=0, interval=1, ease=max(1.3, ease-0.2)`；
  其余前两次固定 1 / 6 天，之后按易度增长；`interval >= 30` 标记 `mastered`；
  另加 `MAX_INTERVAL_DAYS=365` 上限，避免长期不复习后算出十年间隔；
- `storage/repositories.py`：新增 `ReviewRepo`（log / list_for_word / list_recent /
  count_for_date），`WordRepo` 新增 `save_schedule / update_meaning / delete`；
- `gui/pages/review.py`：概览 → 卡片 → 会话统计三态；空格/回车翻面、1~4 评分；
  一轮最多 200 词，复习记录与 `daily_stats.reviews_done` 同步落库；
- `gui/pages/vocabulary.py`：表格 + 筛选（全部/未复习/学习中/已掌握）+ 搜索 +
  右侧详情编辑中英文释义 + 删除（二次确认，级联清理高亮与复习历史）；
- `gui/pages/today.py`：「今日待复习」改用 `StatsRepo.count_due`；
  新增「开始复习」按钮，通过 `review_requested` 信号跳到复习页；
- 删除 `gui/pages/placeholders.py` 与 `common.PlaceholderPage`（占位页已全部替换）。

## 9. M3 任务（已完成核心项，记录实际交付）

> 状态（2026-11-19）：核心项 1~6 全部完成，验证见 5.4；可选项未做。

1. **数据导出** ✅：`services/export_service.py` + 设置页“数据导出”卡片，
   JSON 快照 / CSV 分表（M3.5 起为七表，M3.8 起为八表，含 `reading_quizzes`）；JSON 原子写入，
   CSV 用 `utf-8-sig`；
   仓库新增 `ArticleRepo.list_all`、`WordRepo.list_all_highlights`、
   `ReviewRepo.list_all`、`StatsRepo.list_all`；
2. **API Key 加密** ✅：`services/secret_store.py` 用 ctypes 调 Windows DPAPI，
   密文前缀 `dpapi:`；旧明文兼容，保存时自动升级；非 Windows / 加密失败回退明文；
3. **请求可取消** ✅：`TaskWorker` 新增 `cancelled` 信号、`cancel()`、`stop()`，
   页面统一 `shutdown()`，主窗口关闭时兜底 `wait()`；
4. **错误兜底与离线提示** ✅：`AIError.kind / retryable / is_offline`，
   网络/超时/429/5xx 可重试；今日页与复习页文案明确“复习离线可用”；
5. **生词本增强** ✅：排序（最新/最早/字母/到期日）、批量删除、
   未保存释义的脏检查（切页 / 筛选 / 搜索 / 排序 / 关闭窗口）；
6. **性能** ✅：生词本重建表格时暂停重绘，300 条词回归测试；
   复习队列上限 200 有测试；导出放后台线程；设置页加滚动区；
7. **未做（可选 M4）**：深色模式、安装器 / 自动更新。
   打包本身已在 M1 完成，`DailyEnglish.spec` 可直接复用。

## 10. M4 可选任务（未排期）

- **深色模式**：把 `gui/theme.py` 的颜色抽成两套 palette，`config.json` 的
  `ui.theme` 存偏好；QSS 模板用占位符替换颜色，字体链不变；
- **安装器 / 自动更新**：安装器可用 Inno Setup 包 onefile exe；自动更新需要
  稳定的发布源（当前网络环境下 GitHub 不通，需先确定分发方式）；
- **真实环境调优**：根据实际使用调整 `srs.py` 系数、文章校验区间、复习每日上限。

## 11. 已知问题 / 技术债

| 项 | 说明 | 计划 |
|---|---|---|
| API Key 加密边界 | Windows 用 DPAPI；非 Windows 或加密失败会回退明文（日志有告警） | 保持；如支持更多平台再引入 keyring |
| 取消是协作式 | `cancel()` 不会打断底层 HTTP，只让结果作废；关窗最多等一次 30s 超时 | 保持；如体验差再调短 AIClient 超时 |
| SQLite 跨线程 | 连接 `check_same_thread=False` + 一把 RLock；所有仓库调用都走锁 | 保持；不要绕过 Database 直接执行 SQL |
| 旧词无法区分来源 | M3.7 之前 `lookup()` 即写 `words`，升级时无法判断旧词是“查过”还是“加入过”，统一保留为已加入 | 保留优先；不需要的可在生词本批量删除 |
| 测评题库固定 B1 | A2/B2 套已写好，UI 暂不提供选择 | 以后做自适应或设置项 |
| 文章重试成本 | 校验失败会重试一次，失败的那次也计费 | 可接受；如频繁失败再调 prompt / 放宽校验 |
| `json_mode` 兼容性 | 已实现 `complete_json()` 降级 + 本地三档解析；真实网关报错文案覆盖度待实测 | 实测后补充识别关键字 |
| PySide6 体积大 | 完整 `PySide6` 会拉 WebEngine/Addons | M4 可选：换 `PySide6-Essentials` 后重打包验证 |
| 单温度 | 全局一个 temperature，业务层按任务覆盖 | 够用 |
| 配置校验重复 | `ConfigStore.load()` 调了两次 `validate()` | 顺手清理即可 |
| 导航索引耦合 | 页面顺序与按钮索引硬绑定（`main_window.py` 的 `PAGE_*` 常量） | 加页面时同步改两处 |
| 反馈调级成本 | 一次调整 = 两次 AI 调用（定级 + 写文章），文章失败还会再重试一次 | 可接受；如太慢可换小模型或合并为一次调用 |
| 调级依赖模型判断 | 是否降档取决于模型是否遵守 prompt；极端情况下可能维持原级 | 已验证有兜底；必要时后续加“手动指定等级重写” |
| 重写清空高亮 | 按设计，新文章无法沿用旧偏移，旧高亮随文章级联删除（生词保留） | 可接受 |
| 打包状态 | 已有 `DailyEnglish.spec`（onefile），产物 `dist/DailyEnglish.exe` | 安装器 / 自动更新留到 M4（可选） |
| 复习队列无每日上限 | 一轮最多 200 词，但一天可以开多轮；积压很多时体验偏长 | 视实际使用决定是否加每日上限 |
| 复习快捷键 | 数字键 1~4 在中文输入法下未实测；必要时改成 Alt+1~4 或可配置 | 真实桌面确认后调整 |
| 复习落库非原子 | `review()` 依次写词条、`review_logs`、`daily_stats`，未包同一个事务 | 概率极低；M4 视需要包进 `Database.transaction()` |
| 生词本全量刷新 | 仍是全量查询 + 重建 QTableWidget；几百到几千条可接受 | 上万条再上分页 / 模型视图 |
| CSV 无版本字段 | JSON 有 `version`，CSV 没有；跨版本迁移优先用 JSON | 可接受 |
| 原形依赖 AI | went→go 等由模型判断，错判会写错 lemma；目前无手动改原形入口 | 视实际错判率决定是否加编辑/重新解析 |
| 旧词不回填原形 | 升级前入库的词 lemma 仍是当时词形（如 went），不会自动改成 go | 新查词才走原形；需要整理时删旧词重查 |
| 记忆方法质量 | 联想/谐音由模型生成，可能牵强或格式不统一 | 收集实际效果后迭代 prompt |
| 复习求助成本 | 每次点“忘记/模糊”多一次 AI 调用 | 可加“本次不再提示”或按等级开关 |
| 悬浮卡随滚动收起 | 滚动正文会隐藏释义卡，避免卡片与单词错位 | 可接受；如需跟随需重算锚点 |
| 阅读检测出题质量 | 干扰项合理性、答案唯一性、解析准确性依赖模型；JSON 通过校验不代表题好 | 真实使用后收集差题，必要时在 prompt 增加“禁止模糊选项” |
| 升级节奏 | 连续两次向上才升级，正常；但降级立即生效，疲劳答题可能误降 | 实测后考虑“降级也需两次”或加入“本次状态不好”开关 |
| 短答评分主观 | AI 给 0/1，边界答案（关键词堆砌/语法差但达意）可能不稳定 | 收集样本后细化 0/1 rubric，或改为 0/1/2 三档 |
| 本地证据阈值 | 0.45/0.35/0.6 是经验值，未用真实数据校准 | Phase 2 用历史检测 + 复习数据回归/校准 |
| 一篇文章只能测一次 | 已完成的检测不重复判分，避免反复刷等级 | 如需重测可先“换一篇”，或后续加“仅重做不调级”选项 |
| 检测成本 | 一次完整检测 = 出题 + 讲评两次 AI 调用；出题结果已缓存 | 可接受；如太贵可减少题数或复用文章生成时顺带出题 |
| 外部验收依赖网关 | 验收模型通过 `--settings` 覆盖网关；模型/网关变化会影响复现 | 记录在 VERIFICATION_M3.9.md，换网关时重新跑 |
| 后验阈值经验值 | 斜率 1.1、先验 0.4、置信度 0.6、历史衰减 0.75 均未用真实用户数据校准 | Phase 2 已把观测落库；积累真实数据后可回归校准 |
| 题目审核成本 | 每次出题多一次 AI 调用；审核失败保留原题 | 可接受；如太贵可只在第一轮开审核或合并进讲评 |
| 自适应第二轮成本 | 一次完整检测最多 4 次 AI 调用（两轮出题 + 盲评 + 讲评），另加审核 | 这是边界探测的代价；真实网关慢时可考虑合并审核 |
| 每周复盘缓存粒度 | 同一周只生成一次；周一生成后，后续几天的数据不会更新该周复盘 | 符合“每周一次”；如需刷新可加“重新生成”按钮 |
| 旧词无词档 | 升级前入库的词 `cefr_band` 为空，不参与复习词档证据 | 新查词才带词档；需要时删旧词重查 |
| 题目历史正确率口径 | 每篇文章的题都是 AI 现出的，同一道题不会重复出现；本地统计按 difficulty/skill 聚合 | 逐题对错仍在 `reading_quizzes` 的 questions/answers/productions 中，可离线回放 |
| 每周复盘建议依赖模型 | `suggested_level` 仍由 AI 给出，本地只做合法性/跳档/置信度钳制 | 用户必须确认才生效；不满意可保持当前 |
| 产出题评分缺失 | 模型完全省略 `production_scores` 时按 0 分容错（2 道产出题不会重试） | 若真实网关频繁出现，改为抛格式错误让用户重试 |
| 检测结果三写非原子 | `profile` → `reading_quizzes` → `level_evidence` 依次写入，未包同一事务 | 与复习落库同类；概率极低，M4 视需要包 `Database.transaction()` |
| 外部验收动态执行受限 | 本机外部 CLI 在 4 轮验收中均无法执行 pytest/ruff（权限层），只能静态审查 | 动态检查以开发会话自检结果归档；换环境/授权后可复跑 |

## 12. 下次会话开工指令

把下面这段直接发给 Claude：

```text
继续 py-learn 的 english_learner。先读 CLAUDE.md、docs/english_learner/PRODUCT.md
和 docs/english_learner/PROGRESS.md，确认 M3.11（Phase 2 本地后验 + Phase 3 自适应/
盲评/每周复盘）已完成，当前基线应为 273 passed。下一步优先做真实环境调优：
混合难度与自适应出题质量、置信度阈值校准、每周复盘文案；可选 M4（深色模式、
安装器 / 自动更新）继续保留。每完成一块补测试并更新状态与验证记录。
不要引入 ORM，不要跳过 TaskWorker 直接调 AI，services/storage 不得 import PySide6。
```

开工前先自检：

```bash
uv sync
uv run pytest -q
uv run ruff check src/english_learner tests
```

## 13. M3.9 后续优化阶段（Phase 2 / Phase 3，已完成 → M3.11）

> 第一阶段（M3.9）已经把“AI 直接给等级”改成“AI 出证据 + 本地连续策略”。
> 下面两阶段用于把“单次检测”升级成“长期、可置信的水平估计”。
> **状态（2026-11-19）：Phase 2 / Phase 3 均已完成，实现细节见 1. 当前状态 M3.11
> 与 6.22；外部验收见 `VERIFICATION_M3.11.md`。** 下面保留原始任务书作为对照。

### Phase 2：本地证据模型 + 置信度（✅ 已完成）

目标：让等级变化基于多次观测的后验，而不是两次二值信号。

1. **数据结构**
   - 新增 `level_evidence` 表（或扩展 `reading_quizzes`）：
     `source`（assessment/feedback/quiz/review）、`observed_at`、
     `quiz_id`、`choice_correct/total`、`short_score`、
     `weighted_points`、`evidence_ratio`、`error_type_summary_json`、
     `posterior_json`、`confidence`。
   - `profile` 增加 `level_confidence REAL`、`level_updated_reason TEXT`；
     `pending_level/pending_count` 保留，作为后验未达阈值时的过渡状态。
2. **本地估计**
   - 用简单的 Rasch/逻辑斯蒂模型：
     `P(correct | L, d) = 1 / (1 + exp(k * (index(d) - index(L))))`，
     对 A2/B1/B2/C1 四个候选等级计算似然，再乘当前先验得到后验。
   - 题目难度由 AI 标注（已有 `difficulty`），本地只做算术；
     短答按一道同档/高档题计入。
   - 等级只在后验 MAP 与当前不同且 `confidence >= 0.6` 时改变；
     升级仍可要求连续两次，降级可一次。
3. **历史与长期信号**
   - 讲评 prompt 附带最近 5~10 次检测摘要（文章等级、得分、难度分布、错因），
     让 AI 做趋势判断；本地仍保留最终决定权。
   - 查词/生词本增加 `cefr_band` 或频率档（AI 查词时顺带标注）；
     统计用户查得最多、复习最稳的词档，作为弱证据。
   - 复习评分（`review_logs.rating`）按词档聚合，纳入证据模型。
4. **UI**
   - 结果页显示「B1（置信度 0.62）/ 建议 B2（观察中）」；
   - 今日页可显示等级趋势（最近 4 周），不做复杂图表，先文字/小条形。
5. **验收标准**
   - 纯函数估计器单测：全对/全错/混合难度/缺失难度/边界等级；
   - 两个用户场景回放：连续简单题不应快速升级、连续高难度题应升级；
   - 旧库 v5 → v6 原地迁移，旧检测结果不回填、不阻塞。

### Phase 3：自适应出题与更强诊断（✅ 已完成）

1. **两阶段自适应**
   - 先出 3 道当前等级题；根据正确率再出 2 道高一档或低一档题，
     用“边界探测”替代固定配比；代价是多一次出题调用。
2. **题目质量校准**
   - AI 出题后增加一次“题目审核”pass：检查答案唯一、干扰项合理、
     是否必须读文章才能答；不合格题丢弃/重出。
   - 记录每题历史正确率（本地统计），逐步形成文章题库的难度校准。
3. **更强产出信号**
   - 短答从 1 题扩展到 1 题短答 + 1 题句子改写/翻译（按等级可选）；
   - AI 评分 rubric 固定为“内容 / 语法 / 词汇”三项，减少主观漂移。
4. **盲评与反作弊**
   - 等级评估与讲评分两次调用：讲评模型看到当前等级；
     等级评估模型不看当前等级（blind），减少锚定偏差。
   - 选项随机排序、同一天只允许一次正式检测；重做只练手不调级。
5. **长期复盘**
   - 每周一次 AI 复盘：汇总检测、生词、复习数据，输出“本周水平判断 +
     下周建议”；用户确认后才改等级。
6. **验收标准**
   - 自适应题目的难度选择有单测（mock AI）；
   - 盲评分数与当前等级的相关性可用历史数据回放；
   - 任何 AI 失败都不改画像，用户可跳过/重试。
