# Daily English 架构与关键实现

> **按需阅读**：改 AI 客户端 / 存储 / 阅读检测 / 导出等模块前，先看对应小节。
> 当前状态见 [PROGRESS.md](PROGRESS.md)，历史记录见 [HISTORY.md](HISTORY.md)，
> 已知问题见 [TECH_DEBT.md](TECH_DEBT.md)。章节编号沿用拆分前的 PROGRESS。

## 4. 文件地图（M5.5 完成后的现状）

```
src/english_learner/
├── __init__.py                 版本号、产品说明、分层约定
├── __main__.py                 支持 python -m english_learner
├── app.py                      入口：日志、配置、Database、主窗口、首次测评
├── assets/
│   ├── fonts/                  内置开源字体 Inter + Literata（OFL 1.1，见 OFL-*.txt）
│   └── wordlists/              M5.3 静态词表 ai_frequency.json（3212 词，A1~C1）
├── models/
│   └── entities.py             Profile / Article / Word / Highlight / DailyStats /
│                               ReadingQuiz / WritingAttempt / AICall 等 frozen dataclass
├── storage/
│   ├── db.py                   SQLite 连接、PRAGMA、建表迁移（schema v8）、事务、仓库入口
│   └── repositories.py         Profile / Article / Word / Review / Stats / Quiz / Evidence /
│                               WeeklyReview / AICall / WritingAttempt 仓库（纯 SQL）
├── services/
│   ├── ai_client.py            OpenAI 兼容客户端；test_connection / complete / complete_json
│   │                           支持 AICallRecorder 注入（token/耗时/错误落 ai_calls）
│   ├── ai_protocol.py          TextCompleter 协议（业务层只依赖一个方法）
│   ├── app_context.py          AppContext：配置 + DB + 各 Service 的组装点
│   ├── config_store.py         AIConfig、JSON 配置读写、URL 规范化、Key 脱敏
│   ├── provider_presets.py     服务商预设
│   ├── json_utils.py           parse_json_object 三档兜底 + 字段类型转换
│   ├── prompts.py              全部提示词 + PROMPT_VERSION（当前 "m6-5a"）
│   ├── assessment_bank.py      3 套固定题（A2/B1/B2），默认 B1
│   ├── assessment_service.py   本地计分、AI 评分、CEFR 回退、手动选级、落库
│   ├── article_service.py      文章生成、JSON 校验、重试一次、当天缓存、换一篇
│   ├── dictionary_service.py   lemma 规范化、句子提取、查词缓存、加入生词本、后台补全
│   ├── level_feedback_service.py 阅读反馈调级：AI 重新定级 + 软约束重写今日文章
│   ├── reading_quiz_service.py 阅读检测：自适应两阶段、盲评、产出题、本地后验与调级
│   ├── level_estimator.py      纯函数后验估计器（逻辑斯蒂 + 先验 + 历史衰减）
│   ├── quiz_calibration.py     难度/技能正确率与逐题对错统计
│   ├── weekly_review_service.py 每周复盘：汇总数据 + AI 建议 + 用户确认
│   ├── srs.py                  简化 SM-2 调度 + ReviewService（纯本地，不依赖 AI）
│   ├── recall.py               拼写回忆判分与挖空题面（纯函数，不调 AI）
│   ├── review_plan.py          复习卡片自动编排：看词 / 拼写 / 双阶段（M3.18，纯函数）
│   ├── secret_store.py         API Key 的 Windows DPAPI 加解密（非 Windows 回退明文）
│   ├── log_files.py            日志尾部读取 / 轮转文件排序 / 关键字筛选（纯 Python）
│   ├── learner_memory.py       学习者记忆：本地派生摘要 + 按任务过滤（M5.2）
│   ├── readability.py          本地可读性指标：生词比例 / 句长 / 目标词覆盖（M5.3，纯函数）
│   ├── back_translation.py     回译：切句 / 抽句 / 译文与批改解析（M5.4，纯函数）
│   ├── back_translation_service.py 回译出题缓存 + 批量批改（M5.4，两次 AI 调用）
│   ├── metrics_service.py      学习效果度量：通过率 / 回炉率 / 计划完成 / 重试 / 趋势（M5.5，纯本地）
│   ├── export_service.py       JSON / CSV 数据导出（十二张表，含 AI 调用记录与回译）
│   ├── backup_service.py       启动时每日备份 data.db（SQLite backup API，保留 7 份）
│   ├── database_transfer.py    整库导出 / 导入（校验 + 暂存 + 启动时替换，导入前备份）
│   └── review_help_service.py  复习“忘记/模糊”求助：AI 建议 + 更新记忆方法
└── gui/
    ├── fonts.py                内置字体注册 + 全局英文字体（默认 Lexend，可选 Literata/Georgia/Inter）
    ├── theme.py                全局 QSS（含阅读器/释义卡/测评短文样式）
    ├── workers.py              TaskWorker(QThread)：AI 调用必须走这里；支持协作式取消
    ├── app_restart.py          重启应用（导入数据库后立即生效；开发环境用 -m，打包后拉起 exe）
    ├── onboarding.py           3 步配置向导
    ├── assessment_dialog.py     摸底测评弹窗（答题 → 评分 → 结果/手动选级）
    ├── level_feedback_dialog.py 阅读感受弹窗（快捷选项 + 文本 → 调级并换文章）
    ├── reading_quiz_dialog.py  阅读检测弹窗（两轮自适应 + 产出题 + 置信度结果）
    ├── back_translation_dialog.py 回译练习弹窗（中文 → 写英文 → 最小修改与原因）
    ├── review_help_dialog.py   复习求助弹窗（原因 → AI 建议 → 更新记忆方法）
    ├── weekly_review_dialog.py 每周复盘弹窗（AI 总结 → 用户确认后改级）
    ├── ai_usage_dialog.py      AI 调用记录弹窗（范围汇总 / 按任务 / 明细 / 清空）
    ├── log_viewer_dialog.py    运行日志查看器（轮转文件 / 关键字筛选 / 自动刷新）
    ├── main_window.py          导航 + 页面栈 + AppContext + 首次测评触发
    ├── window_state.py         窗口几何保存 / 恢复（saveGeometry ↔ config.json）
    ├── widgets/
    │   ├── article_view.py     只读 QTextEdit：双击取词、高亮、17px/1.8 行距
    │   └── word_panel.py       悬浮释义卡（可滚动/关闭）+ 加入生词本按钮
    └── pages/
        ├── common.py           Card / PageHeader / StatCard(可刷新) / set_status
        ├── today.py            今日页：连续天数/到期复习/累计生词/文章状态/测评入口
        ├── reader.py           阅读页：正文全宽 + 悬浮释义卡、换一篇、调整难度、阅读完成、回译练习
        ├── vocabulary.py       生词本页：筛选/搜索/排序/编辑/批量删除/脏检查/求助记录回显
        ├── review.py           复习页：自动编排卡片、每日目标、分批检查点、随时结束（M3.18/M3.19）
        └── settings.py         配置、学习效果、调用记录摘要与入口、字体、数据位置、JSON/CSV 导出

tests/（共 522 个用例，全部 mock AI、无网络）
├── test_english_learner_config.py        配置读写/校验/脱敏
├── test_english_learner_ai_client.py     连接测试与错误翻译
├── test_english_learner_ai_usage.py      AI 调用仓库汇总/筛选、客户端埋点与重试、AppContext 注入
├── test_english_learner_log_files.py     日志尾部截断/轮转排序/关键字筛选
├── test_english_learner_usage_dialogs.py 用量弹窗/日志查看器/设置页摘要（offscreen）
├── test_english_learner_storage.py       建表幂等/CRUD/唯一约束/级联/统计/文章替换
├── test_english_learner_json_utils.py    围栏 JSON/噪声/类型转换
├── test_english_learner_assessment.py    计分/评分/容错/手动选级/落库
├── test_english_learner_article.py       生成校验/软约束/重试/缓存/换一篇
├── test_english_learner_feedback.py      反馈调级/软约束重写/失败不改旧数据
├── test_english_learner_reading_quiz.py  混合难度/短答/证据评分/连续调级/失败兜底
├── test_english_learner_reading_quiz_dialog.py 弹窗：缓存/答题/短答/自评/结果/全屏
├── test_english_learner_level_estimator.py 后验估计：先验/证据/置信度/跳档
├── test_english_learner_weekly_review.py 复盘汇总/缓存/建议应用/无数据跳过
├── test_english_learner_weekly_review_dialog.py 复盘弹窗：展示/确认改级/取消
├── test_english_learner_reader.py        lemma/句子提取/查词缓存/高亮/阅读页
├── test_english_learner_srs.py           SM-2 四档调度/易度边界/间隔上限/复习服务落库
├── test_english_learner_review_help.py   复习求助：建议解析/记忆方法落库/失败兜底
├── test_english_learner_review_help_dialog.py 求助弹窗：快捷原因/AI 建议/跳过
├── test_english_learner_review_page.py   复习页状态机/翻面/评分/自动出卡/每日目标/检查点/提前结束
├── test_english_learner_review_plan.py   卡片编排：阶段判定/历史覆盖/穿插规则/不丢卡不重复
├── test_english_learner_recall.py        拼写归一化/编辑距离/判分与建议评分
├── test_english_learner_learner_memory.py 记忆汇总口径/任务过滤/截断/空数据 prompt 快照
├── test_english_learner_back_translation.py 回译切句/抽句/译文与批改解析/缓存/落库/级联
├── test_english_learner_back_translation_dialog.py 回译弹窗：缓存/校验/批改展示/失败重试
├── test_english_learner_metrics.py       学习效果口径：首次通过/回炉/计划完成/重试/趋势
├── test_english_learner_metrics_page.py  设置页学习效果卡片（offscreen）
├── test_english_learner_vocabulary_page.py 生词本筛选/搜索/排序/编辑/批量删除/脏检查/求助记录回显
├── test_english_learner_secret_store.py   DPAPI 往返/明文兼容/损坏密文
├── test_english_learner_export.py        JSON 快照/CSV 分表/空库/原子写入
├── test_english_learner_workers.py       TaskWorker 成功/失败/取消/关闭等待
├── test_english_learner_settings_export.py 设置页导出入口与状态提示
├── test_english_learner_fonts.py         字体文件/字体链/Qt 注册/偏好读写/设置页切换
├── test_english_learner_window_state.py  窗口几何快照读写/损坏兜底/关闭保存与重启恢复
└── test_english_learner_gui_smoke.py     offscreen 冒烟（主窗口/导航/测评/反馈/脏检查）
```

## 6. 关键实现说明（避免下次踩坑）

### 6.1 为什么连接测试用 chat.completions 而不是 /models

不少兼容网关没有实现 `/models`。发一句极短的 chat 请求最接近真实使用场景，
同时能验证模型权限。当前实现显式设置 `max_tokens=1024`：thinking 模型可能把
输出额度全花在推理上，额度太小会返回空内容；不传 temperature，沿用服务默认。

### 6.2 线程模型

- `services/*` 全是同步阻塞代码，**禁止**在 UI 线程调用；
- 所有调用包进 `gui.workers.TaskWorker`，通过 `succeeded(object)` /
  `failed(str)` / `cancelled()` 回主线程；
- 调用方必须持有 worker 引用（`self._worker`），否则 QThread 被 GC 会丢信号；
- M3 起 `cancel()` 是协作式取消：任务返回后若已取消就丢弃结果；
  长循环可调用 `workers.is_cancellation_requested()`；HTTP 请求本身无法中断，
  靠超时兜底（业务调用 `BUSINESS_TIMEOUT`：连接 10s / 读 300s，连接测试 30s），
  关闭窗口时页面 `shutdown()` 先等 3 秒，主窗口再兜底等待。
- 用户点「取消」时调 `workers.retire_worker(worker)`：把 worker 移出 UI 槽位、
  断开所有结果信号，并放进全局 orphan 列表；界面立刻恢复可操作，旧线程继续
  跑到请求返回后由 `wait_for_orphans()` 在退出时统一等待。

### 6.3 配置向导状态机

- 只有 `self._tested_config is not None` 时「下一步」才可点；
- 任意字段 `textChanged` / `valueChanged` 都会调用 `_invalidate_test()`；
- 测试成功后才 `ConfigStore.save()`，保存失败停在向导并弹窗；
- `reject()` / `closeEvent()` 在测试进行中会被拒绝，避免 QThread 被销毁。

### 6.4 openai 3.55 的差异

- 异常类（`AuthenticationError`、`APIConnectionError` 等）仍从 `openai` 顶层导入；
- SDK 内部用 `httpx2`，测试构造响应请 `import httpx2`，**不要 import httpx**；
- `OpenAI(..., timeout=30, max_retries=0)` 仍可用；`timeout` 也可传
  `httpx2.Timeout(connect=10, read=300, write=30, pool=10)`，业务调用走
  `ai_client.BUSINESS_TIMEOUT`，避免 thinking 模型生成长文时被 30s 误杀；
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
- **低调主题**（`ui.low_profile`，2026-11-21）：`theme.py` 的颜色已抽成
  `LIGHT_PALETTE` / `LOW_PROFILE_PALETTE`，QSS 模板只写 `__KEY__` 占位符。
  新增样式时**不要写死颜色**，否则深色主题会残留浅色块；测试会用
  “样式表不含 `__`”兜底。低调模式把全局字体换成 `font_stack(mono=True)` 的
  等宽链（Consolas → Cascadia → Courier New → 雅黑 → monospace），用户选的
  `english_font` 保留但不生效；正文仍需 `ArticleView.set_english_font(key,
  mono=True)`。窗口标题、`setApplicationDisplayName` 与侧边栏文案由
  `main_window._SIDEBAR_TEXTS` 两套常量驱动，`_apply_low_profile()` 负责实时
  切换，不重新读配置文件；导航按钮始终保留，只换文案。生词本页顶部的大标题
  由 `PageHeader.set_title_visible(False)` 隐藏（说明文字保留），主窗口在
  构造与切换主题时调用 `VocabularyPage.set_low_profile()`。
- **按钮英文**：`gui/i18n.py` 用中文原文作 key 翻译所有 `QAbstractButton`
  （QPushButton / QCheckBox / QRadioButton）。它 patch 了 `QAbstractButton.setText`
  （页面代码照旧写中文），并用应用级 Show 事件过滤器兜底 C++ 构造参数写入的
  初始文本；切换主题时 `refresh_buttons()` 重刷整棵控件树。
  **新增按钮文案要把中文原文补进 `BUTTON_EN`**（带数字的进 `BUTTON_EN_PATTERNS`），
  漏了只会保持中文，不会报错。QLabel/说明文字/输入框占位符不在范围内。
  快捷选项按钮（阅读反馈、复习求助）点击时读 `button.text()` 回填输入框，
  保证低调模式下输入框也是英文。
- **生词高亮**：正文高亮是 `QTextDocument` 的 ExtraSelection，也不归 QSS 管。
  颜色必须按主题成对给出：浅色 `#FDE68A / #111827`，暗色 `#5C4A22 / #FFE9A8`
  （`article_view.HIGHLIGHT_COLORS`）——只换背景不换前景，暗色主题下浅灰字
  会与浅黄底糊在一起。主题切换时主窗口要调 `ArticleView.set_low_profile()`
  重绘已有标注。

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
- **导出格式**：JSON 是 `{app, version, exported_at, counts, ...}`，`...` 为
  `export_service.TABLE_LABELS` 里的全部表（M3.15 起含 `ai_calls`，M5.1 起
  `review_logs` 含拼写证据列）；CSV 每表一个文件，编码
  `utf-8-sig`（Excel 中文），列表字段用 `"; "` 连接。导出服务只读仓库，
  新增的 `list_all*` 方法不要写业务判断。UI 侧 JSON 选文件、CSV 选目录后
  自动建 `daily-english-export-<时间戳>/` 子目录，避免覆盖旧导出。
- **取消的边界**：`cancelled` 信号不是“立刻停止”，只是“结果作废”。
  页面统一把 `cancelled` 接到自己的 `_on_worker_finished`，清理 busy 状态；
  不要在 `cancel()` 之后假设线程已经结束。界面层的「取消」按钮统一走
  `workers.retire_worker()`，不要只调 `worker.cancel()` 后继续等信号，
  否则用户仍要等到 HTTP 超时。
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
- **弹窗尺寸**：`ReviewHelpDialog` 的正文在 `QScrollArea` 里，`QScrollArea` 的
  sizeHint 不含内容最小宽度，用 `self.sizeHint()` 定宽会退回设计最小宽度、
  右侧被裁。现在按 `_scroll_body.minimumSizeHint().width()` 反推窗口宽度
  （含边距与垂直滚动条），并把结果设为 `minimumWidth`；量之前先
  `i18n.refresh_buttons()` 把中文按钮翻成更宽的英文，且
  `layout().invalidate()/activate()` 清掉 minimumSizeHint 缓存。横向滚动条
  始终关闭，`ensureWidgetVisible()` 之后要把 hvalue 归零，否则左侧正文会被
  横向偏移推出视口。
- **导出**：新增 `word_forms` 表和 `words.mnemonic`、`review_logs.note/advice`
  列；JSON 自动包含，CSV 列顺序在 `export_service.CSV_COLUMNS` 固定。
- **prompt 版本**：M3.5 时为 `PROMPT_VERSION = "m3-2"`（M3.8 起为 `m3-8`），新增
  `build_mnemonic_messages` / `build_review_help_messages`，查词契约新增
  `lemma 必须是原形` 与 `mnemonic` 字段。
- **词形说明**：查词 `meaning_cn` 只在 `display_form` 与 `lemma` 不同时补
  “（go 的过去式）”这类说明；两者相同时 prompt 明确禁止出现“原形”字样
  （卡片本来也只在不同形时显示“原形：X”）。旧缓存不受影响，见 HISTORY。`PROMPT_VERSION`
  随之到 `m6-5a`。
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

### 6.23 M3.15：AI 调用记录与日志查看

- **埋点位置**：只在 `AIClient._complete_once()` / `test_connection()` 里记录，
  业务服务不直接写 `ai_calls`。理由：所有真实 HTTP 请求都经过这里，
  重试、JSON 模式降级、截断/空内容才能被统一覆盖；
- **一次尝试一行**：`_complete_with_retry()` 把 `attempt` 传给 `_complete_once()`；
  429 重试会看到 attempt=1、2 两行。做“调用次数”口径时按行计数，
  这才是真实请求数与计费口径；不要按业务操作去重；
- **记录失败不阻断**：`AICallRecorder` 是 Protocol，`AppContext.client()` 注入
  `db.ai_calls`；`_record_call()` 吞掉所有异常并写 debug 日志。单测里的
  `BrokenRecorder` 保证这一点不会退化；
- **tokens 允许 NULL**：网关不返回 `usage` 时写 NULL，`AIUsageSummary` 用
  `COALESCE(SUM(...),0)` 汇总；界面显示 “—”，避免误导成 0 token；
- **任务名是自由字符串**：服务层传中文任务名（如“阅读检测·盲评”），
  界面筛选下拉框直接从 `SELECT DISTINCT task` 生成，不维护枚举；
  改任务名会让旧记录分裂成两类，重命名时需考虑历史数据；
- **时间过滤**：`called_at` 为本地 ISO 时间，`since` 传日期字符串，
  直接 `called_at >= since` 比较即可（字典序 = 时间序），不需要日期函数；
- **日志读取**：`read_log_tail()` 只 seek 到末尾 256 KB；从中间开始时丢掉第一行；
  `utf-8 errors="replace"` 容错。日志查看器按关键字逐行筛选，注释里已说明
  多行堆栈会丢上下文；
- **日志查看器不要设 font-family**：项目约定所有英文外观由 `gui/fonts.py` +
  全局 QSS 决定（CLAUDE.md 第 9 条），即使是日志窗口也不单独设等宽字体；
- **导出**：`ai_calls` 是第十一张表；`ExportService` 的构造参数新增
  `ai_calls`（可选，默认 None），旧测试/调用方不传也能工作。M5.4 起
  `writing_attempts` 是第十二张表，同样以可选构造参数注入。

### 6.24 M3.16：复习拼写回忆

- **分层**：判分与题面生成放在 `services/recall.py`，纯函数、不依赖 PySide6 / AI；
  `gui/pages/review.py` 只负责展示和交互。新增题型时先改这里，不要往 UI 里塞规则；
- **判分**：`normalize_spelling()` 统一大小写、空白、首尾标点与弯引号；
  `accepted_forms()` 返回 `lemma + display_form`，两者都算对；
  `edit_distance()` 是标准 Levenshtein，距离 1 且长度 ≥ 3 才算“差一点”；
  结果里的 `suggested_rating` 只用于界面提示，SM-2 仍由用户按 1~4 决定；
- **题面**：`build_spelling_prompt()` 优先中文释义 + `example_en`，例句没出现目标词时
  退回 `context_sentence`；两者都无法挖空就只给释义，宁可不给例句也不泄题。
  `mask_occurrences()` 用 `\b` 单词边界和大小写不敏感替换，避免把 go 挖进 good；
  释义里出现的英文原词也会被挖掉。含空格的短语、缺少释义的词返回 None，
  复习页自动回退到看词回忆；
- **UI 状态**：`_current_mode` 表示当前卡片的形态（`recognition` / `spelling` /
  `dual`），`_spell_input_active` 表示输入框是否可编辑。拼写正面隐藏
  `_word_label / _lemma_label / _phonetic_label / _pos_label / _context_label`，
  翻面后再显示。输入框只读后保留用户答案，提交按钮隐藏；
- **快捷键**：拆成 `_reveal_shortcuts`（空格/回车）与 `_rating_shortcuts`（1~4）。
  拼写正面两套都禁用；双阶段默写只禁用空格/回车（回车交给输入框），
  保留 1~4 让用户直接跳过默写；判分翻面后全部恢复。回车提交走
  `QLineEdit.returnPressed`，不走 Return 快捷键；
- **偏好**：M3.16 的 `ui.review_mode` 已在 M3.18 移除，卡片形态改由
  `services/review_plan.py` 自动编排（见 6.31）；设置页改字体仍用
  `dataclasses.replace` 只改 `english_font`；
- **不做的事**：不落新表、不调 AI、不自动改调度。拼写正确率统计已在 M5.1
  通过 `review_logs` 新增列落地（见 6.26）。

### 6.25 M3.17：阅读检测产出题改为英译中

- **产品约束**：阅读检测只练“读懂”，不练英语写作。第二轮产出题统一是
  「原文选句 → 翻译成中文」，不再出现英文短答 / 改写；
- **题型表示**：仍用 `kind=short`（产出题），`skill=translation` 区分类型；
  `QuizQuestion.is_translation` 的判断是「skill 为 translation/translate 且
  题面包含“中文”或“Chinese”」。UI、讲评、盲评、错因统计都复用这个属性，
  不要各自写一套字符串判断；
- **出题**：`build_quiz_stage_two_messages()` 要求 2 道英译中题，
  第 1 道选关键信息/主旨句，第 2 道选含指代/转折/因果的句子；
  直接引用原文，12~35 词，`reference_answer` 必须是中文；
  `PROMPT_VERSION` 已升到 `m3-17`；
- **解析校验**：`parse_stage_two_questions()` 把产出题 skill 强制写成
  `translation`，再用 `is_translation` 过滤；不足 2 道就抛
  `InvalidQuizError`，由服务层带原因重试。模型退回英文写作时不能放行；
- **评分**：讲评 prompt 按题型给不同口径。英译中按「理解准确 / 中文通顺」：
  准确传达原句意思给 1；关键词义、指代、否定、逻辑关系译错/漏译/译反给 0；
  中文生硬不扣分。旧缓存里的 writing/rewrite 题仍按「内容 / 语法 / 词汇」；
  `build_quiz_grading_messages()` 里的 `production_notes` 会作为
  【产出题评分口径】逐题写进 prompt；
- **兼容**：旧 `reading_quizzes.questions` JSON 不需要迁移；
  `is_translation` 为 False 的产出题继续按旧标签展示与旧 rubric 评分；
- **统计**：`question_outcomes()` 把英译中记到 `translation` 技能桶；
  `_weak_skills()` 用 `_PRODUCTION_SKILLS` 过滤 writing/rewrite/translation
  等产出题技能，避免选择题出题提示出现“优先覆盖 translation”这种无效要求；
- **错因**：`_parse_error_types()` 对英译中固定写 `translation`，
  旧产出题仍写 `short_answer`；结果页 `_ERROR_LABELS` 增加对应中文标签。
- **摸底测评同步**：`assessment_bank.SET_A` 原来那道中译英改成英译中；
  `build_assessment_messages()` 明确说明“重点看理解准确度，不要求英文写作”。
  B1/B2 套本来就是英译中，改完后全应用没有要求写英文的输入题。

### 6.26 M5.1：复习证据落库（schema v8）

- **表结构**：`review_logs` 增加 `answer_text / verdict / distance`，只加列，
  旧记录默认空串 / 0，不回填、不参与统计；旧库升级走 `_apply_schema()` 的
  `version < 8` 分支，`export_service.CSV_COLUMNS` 已同步三列；
- **verdict 只有一个来源**：四个值 `correct / near / wrong / skipped` 在
  `services/srs.py` 用 `VERDICT_*` 常量集中定义，`services/recall.py` 的
  `STATUS_*` 直接复用同一套字符串，新增判定时先改常量，不要散落字面量；
- **写入语义**：拼写模式真正提交过答案才写 `correct / near / wrong` 与用户输入、
  编辑距离；看词回忆、拼写跳过、短语回退都写 `skipped`；
- **校验顺序**：`ReviewService.review()` 先校验 verdict 合法性，再写调度和日志，
  非法值直接 `ValueError`，避免留下“词条已更新、日志没写”的半截状态；
- **边界**：证据只用于统计与后续学习者记忆（M5.2），**不参与 `schedule()`**；
  调度结果与改动前完全一致，有对照单测。

### 6.27 M5.2：学习者记忆与计划回流

- **派生而非存储**：`services/learner_memory.py` 的 `LearnerMemoryService.build()`
  每次从 `profile / level_evidence / review_logs / words / weekly_reviews`
  现算一份 `LearnerMemory`，不建表、不新增 AI 调用、不保存“压缩档案”；
  数据一改，下一次 prompt 立刻反映；
- **空数据必须无感**：`LearnerMemory.is_empty` 为真时
  `prompts.build_learner_memory_block()` 返回空串，各 prompt builder 只在非空时
  插入；有快照测试保证默认参数与空记忆的输出逐字节一致；
- **按任务过滤**：文章生成看等级 / 测评薄弱点 / 错因 / 反复忘记 / 拼写薄弱 / 积压
  （上周计划走独立的 `learner_plan`，不在记忆块里重复）；查词只看等级与错因；
  复习求助看等级 / 遗忘词 / 拼写薄弱 / 计划。把测评的 `profile.weaknesses`
  也纳入文章记忆，避免“刚测评完、还没有检测证据”时丢掉原有的个性化提示。
  阅读检测不渲染整块，而是把 `error_types` 并入 `_weak_skills()`（只接受
  `main_idea / detail / inference / vocabulary / grammar`，产出题技能与
  `careless` 等非技能值被过滤）；
- **注入点**：`AppContext` 构造 `LearnerMemoryService` 并把 `memory.build`
  作为 `memory_provider` 传给文章 / 查词 / 复习求助 / 阅读检测四个 service；
  记忆在后台线程的 service 方法里构建，UI 线程不查库；
- **失败降级**：`load_memory()` 吞掉 provider 异常并记 warning，退化为空记忆，
  不能让统计失败拖垮一次 AI 调用；
- **计划回流**：上周复盘的 `focus` 通过文章 prompt 的 `learner_plan`
  作为软约束；本次每周复盘 prompt 通过 `previous_focus` 要求模型交代
  “上周计划完成度”，等级变更仍只走用户确认；
- **预算**：记忆块字段都很短，`MAX_MEMORY_CHARS = 280` 兜底截断，约 150~200 token；
  不要往里面加文章原文或对话历史。

### 6.28 M5.3：本地可测 i+1（可读性指标）

- **静态词表**：`assets/wordlists/ai_frequency.json` 是开发期一次性生成的
  `{word: CEFR}` 映射（3212 词，A1~C1），运行时只读、结果可复现；
  PyInstaller 的 `datas` 整目录打包，不需要改 spec。生成脚本
  `scripts/generate_wordlist.py` 保留复现手段，但长输出会被网关
  `finish_reason=length` 截断，A1 档当时未走通，词表由 AI 单次生成后人工抽查；
- **纯函数**：`services/readability.py` 只依赖文本与词表。词表按路径缓存，
  手工改词表后需重启应用才生效；分词把缩写归一化
  （`don't → do`、`it's → it`），`_candidates()` 按常见后缀还原屈折
  （habits / repeated / running），宁可多认已知词，避免误报难文；
- **指标**：`ReadabilityMetrics` 记录词数、生词数、生词比例、平均 / 最长句长、
  目标词总数 / 命中数、词表规模。`wordlist_size = 0` 表示词表缺失，
  指标不可信，展示与告警都跳过；
- **已知词集合**：词表 + 用户已掌握的词。`WordRepo.list_known_lemmas(30)`
  返回 `in_vocabulary = 1 OR interval_days >= 30` 的原形（阈值由调用方传入，
  storage 不引用 services 常量），与学习者记忆的“掌握”口径一致；
- **schema**：`articles.metrics_json`（`''` 表示旧文章没有指标）。它属于 v8 的
  M5.3 增量，但 M5.1 已把 v8 发布出去了，所以**不能**只在 `version < 8`
  分支加列；`_apply_schema()` 用幂等的 `_add_column` 每次启动补齐，旧库升级
  不丢数据。`Article.metrics` 是 `dict | None`，导出 JSON 是嵌套对象，
  `articles.csv` 增加 `metrics` 列（JSON 字符串）；
- **生成流程**：`ArticleService.generate_article()` 在本地校验通过后调用
  `_analyze()` 计算指标并写日志；默认只记录（`retry_enabled = False`，先观察
  一周），开启后超阈值会带 `repair_note` 走本循环的第二次尝试——复用现有重试
  通道，最坏只多 1 次同类调用，不新增任务标签；
- **阈值可配**：`config.json` 的 `readability` 段（`enabled / retry_enabled /
  max_unknown_ratio / max_avg_sentence_len`），由
  `ConfigStore.load_readability()` 宽松解析，非法值回退默认；
  `ConfigStore.save()` 已改为保留未知段，重新配置 AI 不会冲掉 `readability`；
- **宽容规则**：词表缺失或小于 500 词时阈值放宽 1.5 倍；正文少于 40 词不告警；
  指标**不参与改等级**，也不改变 SRS 调度；
- **界面**：阅读页 meta 行展示 `生词 X% · 目标词 N 个`，超阈值追加 `偏难`；
  旧文章没有 `metrics` 时整段不显示，不影响阅读与检测。

### 6.29 M5.4：回译练习

- **分层**：切句、抽句与 JSON 解析在 `services/back_translation.py`（纯函数）；
  AI 编排在 `services/back_translation_service.py`；界面只负责三态展示。
  新增规则先改纯函数模块，不要往弹窗里塞；
- **两次调用，两张标签**：首次打开练习做一次 `回译出题`（把本地抽出的 3 句翻成
  中文并落库），提交后做一次 `回译批改`（3 句批量）。原任务卡写“一天最多 1 次”，
  但译文没有现成缓存，2026-11-20 决定按“学习效果优先”取消每天上限；缓存策略
  保证**出题每篇 1 次、批改每次提交 1 次**，重复打开不重复请求；
- **抽句**：`pick_sentences()` 先切句（处理 Dr. / U.S. / e.g. / 小数 / 引号），
  排除 <7 或 >40 词的句子，按“含目标词 +3 / 长度理想 +2”排序取前 3，
  再按原文顺序返回；选题在本地完成，模型只翻译，结果可复现；
- **批改契约**：逐句 `score` 只允许 0 / 2（模型给 1 也归一化为 0），`reason` 必填，
  `error_types` 收敛到固定词表（中文标签也映射）；解析失败带原因重试一次，
  仍失败抛 `AIError` 且**不写库**，用户输入保留在界面上可直接重试；
- **schema**：`writing_attempts` 是 v8 上的新表，由 `CREATE TABLE IF NOT EXISTS`
  在旧库启动时自动补齐，不升版本号。`verdict_json` 只存单句批改结果
  （edited / reason / error_types），与 `Article.metrics` 一样用 dict 字段，
  避免 models 反向依赖 services；`graded_at IS NULL` 表示尚未批改；
- **学习者记忆**：`LearnerMemoryService` 读取近 7 天已批改记录的错误类型 Top2，
  渲染为文章 prompt 的“英文写作薄弱”一行；没有回译数据时不出现该行，
  空记忆 prompt 仍逐字节一致；
- **界面**：阅读页「回译练习」只依赖当天文章，不需要先做测评；弹窗打开时
  同步读缓存（本地 SQL），无缓存才起 `TaskWorker` 出题；请求中拒绝关闭，
  已批改再次打开只读展示，不重复判分；
- **边界**：只作练习反馈，不写 `profile`、不写 `review_logs`、不触发等级或
  SRS 变化；失败 / 离线可直接关闭，不阻塞阅读；
- **导出**：`writing_attempts` 成为第十二张表，`verdict` 在 CSV 里是 JSON 字符串。

### 6.30 M5.5：学习效果度量

- **分层**：口径与计算全在 `services/metrics_service.py`（只读、无 AI）；
  仓库只提供聚合 SQL，界面只做格式化。改口径先改服务与 `AI_TASKS.md` 6.1，
  不要把判定散落到设置页；
- **首次通过率**：`ROW_NUMBER() OVER (PARTITION BY word_id ORDER BY
  reviewed_at, id)` 取每词第一条，再按首次复习日期过滤窗口；同一秒多条记录
  用 id 兜底。不要用 `MIN(reviewed_at)` 关联，时间相同会重复计数；
- **回炉率**：`COUNT(DISTINCT CASE WHEN rating IN (1,2) THEN word_id END)`，
  分母是窗口内复习过的词数，按词去重，不按次数；
- **计划完成率**：只看紧邻上周的 `weekly_reviews`，本周行为由
  `daily_stats / reading_quizzes / writing_attempts` 判定；focus 是自由文本，
  用固定关键词表映射（阅读 / 复习 / 生词 / 检测 / 写作），无法映射的条目不进
  分母，避免把“练习听力”这类本产品不做的条目算成失败；全部无法映射时返回
  `None`，不要返回 `planned=0` 的对象；
- **重试率**：`ai_calls` 每次 HTTP 尝试一行，`attempt > 1` 为重试；文章按精确
  任务 `每日文章`，检测按前缀 `阅读检测`（含第一轮 / 第二轮 / 审核 / 讲评 / 盲评）；
- **趋势**：非练习检测按文章等级分组，窗口与排序都按 `completed_at`
  （跨天提交算完成日），最近 3 次与更早 3 次不重叠；样本 < 4 次不判断方向，
  差值严格大于 ±3 个百分点才判 ↑ / ↓，等于边界算持平，避免小样本抖动；
- **窗口上下界**：所有聚合 SQL 都同时带 `>= 起始日` 与 `<= 今天`，
  未来时间戳（系统时钟异常 / 手工写库）不进入“近 30 天”；
- **展示**：设置页卡片在构造与 `on_activated` 时刷新；计算异常只写日志并显示
  “—”，不影响设置页其他功能。

### 6.31 M3.18：复习卡片自动编排（看词 / 拼写 / 双阶段）

- **分层**：编排规则全在 `services/review_plan.py`（纯函数、无数据库 / PySide6）；
  `gui/pages/review.py` 只按 `ReviewCard.mode` 展示。改规则先改这里与
  `test_english_learner_review_plan.py`，不要把阶段判断写回 UI；
- **判定顺序**：拼写题面不可用 → 看词；`repetitions == 0` → 看词；
  上次评分 ≤ 2 或上次 verdict 是 `near/wrong` → 双阶段；`repetitions == 1`
  → 双阶段；其余 → 拼写。顺序不能乱：`repetitions == 0` 必须优先于
  “上次没记牢”，否则评过“忘记”的词会被要求直接拼写；
- **题面可用性**：除 `build_spelling_prompt()` 返回 None 外，还要检查
  `_meaning_has_hint()`：释义本身是目标词时会被整体挖成 `____`，这种没有
  线索的题面自动回退看词；数字、中文、英文都算有效线索；
- **历史查询**：`ReviewRepo.last_reviews(word_ids)` 用 `MAX(id)` 分组，
  一次取完整会话的 `(rating, verdict)`，避免 N+1；没有历史的词不返回；
- **穿插**：按卡面拆成看词类 / 拼写类两个队列，每次优先取剩余更多的一类，
  数量相同按首次出现顺序；同类连续不超过 2 张（除非另一类已用完）。
  不要用随机打乱：单测需要确定性，用户也需要可预期；
- **双阶段卡**：仍是一张卡。正面 = 看词回忆；翻面后进入默写阶段，隐藏
  原词 / 音标 / 上下文 / 答案区，只留挖空释义与输入框；提交后恢复完整答案。
  空格/回车禁用、1~4 保留，用户可以直接评分跳过默写；
- **求助弹窗与快捷键**：弹窗打开前记录每个 shortcut 的 `isEnabled()` 快照，
  关闭后按快照恢复，不能无条件全部启用——双阶段默写阶段本来就禁用空格/回车，
  无条件恢复会让页面级 Return 截获回车，`QLineEdit.returnPressed` 不再触发；
- **调度不变**：双阶段只调用一次 `ReviewService.review()`，`schedule()` 的输入
  仍是用户确认的 1~4；默写结果只进 `review_logs` 的
  `answer_text / verdict / distance`，与 M5.1 口径一致；
- **配置清理**：`ui.review_mode` 与 `UiConfig.review_mode` 已删除，
  旧 `config.json` 里的该键被忽略，不做迁移；复习页不再有模式下拉框。

### 6.32 M3.19：复习节奏（每日目标 / 分批检查点 / 拼写开关）

- **复习偏好**：`config.json` 新增 `review` 段，由 `ReviewConfig` 读写：
  `daily_limit` 默认 20（夹取到 5~200），`spelling_enabled` 默认 `False`。
  `ConfigStore.save_review()` 只改这一段，与 `save_ui/save_readability` 一样
  保留 ai / ui / readability；设置页先设值再连信号，构造期间不会误保存；
- **每日目标**：开始一轮只取
  `min(到期数, max(0, daily_limit - 今日 reviews_done))`；今日已完成达到目标后
  再点按钮属于「加练」，取 `min(到期数, daily_limit, SESSION_LIMIT)`。
  未做完的到期词留在积压里，不丢数据。今日完成数统一读
  `daily_stats.reviews_done`，跨多次会话累加；
- **精确到期数**：`ReviewService.count_due()` 优先走 `StatsRepo.count_due()` 的
  `COUNT(*)`；`due_words()` 默认 `limit=200`，直接用 `len()` 会把积压数截断；
- **分批检查点**：`ROUND_SIZE = 10`。`_rate()` **先判断是否最后一张，再判断是否
  到达轮次**，最后一张直接进总结，不能多弹一次「还剩 0 个」的空检查点；
  检查点只暂停展示，`_session/_index` 不重新查库，继续时从 `_index` 接着展示；
- **提前结束**：卡片页常驻「结束本轮」，已评分卡片此前已逐张
  `ReviewService.review()` 落库；`_reviewed == 0` 时只回概览，不产生空总结。
  `refresh()` 会清空 `_session/_index/_at_checkpoint`，避免「回到概览」后残留
  检查点状态；`on_activated()` 在活动会话或检查点时直接返回，切页不打断；
- **拼写开关**：`build_session_plan(..., spelling_enabled=...)` 关闭时全部
  `CARD_RECOGNITION` 且跳过穿插，开启时保持 6.31 的全部规则；复习页每次开始
  会话都重新 `load_review()`，因此设置页修改后下一次开始生效；
- **新词首次到期日**：`DictionaryService.add_to_vocabulary()` 把第一次入本的
  `due_date` 设为「今天 + 1 天」；`WordRepo.mark_in_vocabulary(first_due_date=...)`
  用 `get_by_id()` 先看当前 `in_vocabulary`，只在 0 → 1 时写 `due_date`，
  重复加入不重置已有 SRS 排期。`lookup()` 缓存词仍写当天，缓存放几天后
  再加入也不会一入本即逾期；`schedule()` 本身不读旧 `due_date`，排期不受影响；

### 6.33 M6.1：文章难词缓存与阅读后台预取（查词 0 等待）

- **问题**：查词是一个未命中就发一次完整 AI 请求（`max_tokens=1500`、读超时 300s），
  而且阅读页同一时刻只允许一个 `_worker`——上一个词在查时点下一个词会被直接忽略。
  生词一多，等待时间线性累加；
- **第一层：文章自带 glossary**。`build_article_messages()` 要求模型在同一个
  JSON 里顺带返回 8~15 个“对当前等级可能陌生、且真实出现在正文里”的词及语境义。
  文章落库后 `ArticleService._seed_glossary()` 用 `WordRepo.seed_cache()` 写入
  `words`（`in_vocabulary=0`，不占生词本），并 `remember_form()` 记住正文词形。
  `_parse_glossary()` 会丢弃正文里没出现过的词、多词短语和非法词形，**解析失败
  绝不触发文章重试**；glossary 只要求核心释义（不含例句/记忆法），
  `ARTICLE_MAX_TOKENS` 从 4000 提到 5000，后因 deepseek-v4.1-flash 的
  reasoning token 也计入输出额度、偶发把 5000 吃满（`finish_reason=length`），
  再提到 15000；
- **第二层：打开文章后后台批量预取**。`ReaderPage._load_article()` 成功后启动
  **独立的** `_prefetch_worker`（不占查词/生成的 `_worker` 槽位），调用
  `DictionaryService.prefetch()`：`readability.select_lookup_words()` 用内置词表
  在本地筛候选词——用户已掌握（`list_known_lemmas`）或已有缓存
  （`WordRepo.cached_forms()`）的词排除，词表里不存在或词档高于用户等级的优先，
  句中大写视为专有名词跳过；最多 10 个词合并成一次请求（`task="查词预取"`），
  返回后同样走 `seed_cache()`，并带上词所在句作为 `context_sentence`；
- **失败即静默**：预取超时/网络/解析/落库失败都只记日志、返回 0，不影响正文展示；
  请求显式传 `timeout=35s`（`AIClient.complete_json(..., timeout=...)`），
  且略大于 30s 的自动重试阈值，保证超时后不再自动重试，关闭窗口最多等 35s；
- **缓存语义**：`seed_cache` 是 `ON CONFLICT(lemma) DO NOTHING`，只补空缺：
  已有词条的完整释义、SRS 进度与 `in_vocabulary=1` 一律不被预生成内容覆盖。
  预取词双击时命中缓存并显示 `context_sentence`；如果用户在某一句里看到它、
  而预取记录的是另一句，仍会提示「按本句重查」；
- **开关与生命周期**：`config.json` 的 `ui.prefetch_enabled` 默认开启，设置页
  「阅读」卡片可关；每篇文章只预取一次（按 `article.id` 去重），换文章后
  `_on_prefetch_finished()` 会为当前文章补一次；`shutdown()` 同时等待
  `_worker` 与 `_prefetch_worker`；
- **测试注意**：阅读器测试模块用 autouse fixture 停用 `_start_prefetch`（不联网），
  预取接线与失败路径在 `test_english_learner_prefetch.py` 里用假 service 验证。

### 6.34 M6.2：阅读检测选择题「不知道 + 补充说明」

- **复用测评的作答常量**：`assessment_bank.UNKNOWN_ANSWER = -2` 表示用户主动
  放弃作答，`-1` 仍保留给“未作答”。`ReadingQuizService` 直接导入并 re-export
  这两个常量，避免两套流程对 -2/-1 的语义漂移；
- **判分口径**：`_normalize_answers()` 放行 -2，其余越界值仍归并为 -1；
  `submit()` / `advance_stage()` 只拦 -1。正确率、`score_quiz_evidence()` 与
  `observations_from_quiz()` 都按 `chosen == answer_index` 判断，因此 -2 自然
  按答错计入等级证据——**不要**为「不知道」单独加权或跳过观测；
- **说明的归一化**：`_normalize_choice_notes()` 生成与选择题等长的元组，
  只保留选中 -2 的题的说明（截断 200 字），其它位置强制空串，
  避免用户改了选项后残留的文本进入 prompt；
- **存储**：`reading_quizzes.choice_notes_json`（`TEXT NOT NULL DEFAULT '[]'`）。
  新库在 `SCHEMA_SQL` 建表时直接带列；旧库在 `_apply_schema()` 里用幂等
  `_add_column` 补列，**不升 schema 版本**（与 `articles.metrics_json` 同一模式）。
  第一轮作答通过 `save_stage_answers(quiz_id, answers, choice_notes)` 一起落库，
  最终结果通过 `save_result(..., choice_notes=...)` 写入；导出 CSV 新增
  `choice_notes` 列；
- **prompt**：讲评与盲评统一把 -2 渲染成「不知道（主动表示不会）」，
  再附一行「补充说明：…」；讲评规则明确要求按答错处理并结合说明归因
  （词汇不认识 → vocabulary、句子结构 → grammar、没找到依据 → detail/inference），
  不能当成跳过。`PROMPT_VERSION` 随之为 `m6-2`；
- **界面**：`_build_choice_card()` 在选项后追加 `E. 不知道`（字母按选项数顺延），
  用 `QButtonGroup.idToggled` 控制补充说明 `QLineEdit` 的显隐；第一轮锁定
  （`locked=True`）时单选框和输入框一起禁用。结果页对 -2 显示
  「主动表示不会」+「你的答案：不知道」+ 说明；
- **测试注意**：弹窗测试的 `StubQuizService.advance_stage/submit` 必须接受
  `choice_notes` 关键字；offscreen 下判断输入框显隐用 `isHidden()`
  （父窗口未 show 时 `isVisible()` 恒为 False）。

### 6.35 M6.3：加入生词本后后台补全简释

- **问题**：文章 glossary 与后台预取只写“短释义”（无例句、无记忆方法），
  而真正双击查词会得到完整词条。用户把简释词加入生词本后，生词本里会长期
  只有一行短释义，复习与回译都缺素材；手动再查一次又要等 AI；
- **触发**：`ReaderPage._on_add_requested()` 在加入生词本成功后调用
  `_start_enrich(word.id, sentence)`，**不阻塞界面**：不设置 `_set_busy()`、
  不占用查词/生成的 `_worker`，用户仍可继续双击查下一个词；
- **独立槽位与队列**：`_enrich_worker` + `_enrich_queue`（FIFO，按 word_id
  去重），同一时刻只跑一个后台补全，`finished` 后自动取下一个。加入多个词时
  不会并发打满 AI 请求；
- **是否值得补**：`DictionaryService.needs_definition()` 只看“例句与记忆方法
  是否都为空”——完整查词的 prompt 必然要求这两个字段，都为空说明该条来自
  glossary / 预取。检查放在**子线程执行时**（`enrich_definition()` 内部读库），
  完整词条直接返回 `None`，不花 token，也天然避免重复补全；
- **与双击一致**：`lookup()` 与 `enrich_definition()` 共用
  `_request_definition()`（同一份 `build_dictionary_messages`、同一套字段），
  补全请求用 `task="查词补全"`、`timeout=45s`（大于 30s 自动重试阈值，
  超时直接放弃，关窗口最多等 45s）；
- **落库语义**：`WordRepo.update_definition(word_id, ...)` 只更新释义类字段
  （音标 / 词性 / 释义 / 例句 / 记忆法 / 词档 / 上下文），**不改** lemma、
  display_form、in_vocabulary、article_id 与 SRS 排期；记忆方法只在原本为空时
  写入，避免覆盖用户手动生成的内容。AI 如果给出不同原形也不新建词条
  （高亮与 `word_forms` 仍指向原 id）；
- **界面更新**：完成时只有当卡片仍停在同一个词（`_pending` 非空且
  `_current_word_id` 相同）才调 `WordPanel.update_definition()` 原地重绘，
  只换字段、不动按钮与状态；页面状态改为「已加入生词本，释义已在后台补全」。
  用户已经收起卡片 / 点了别的词时只更新库里的词条，不打扰阅读；
- **失败静默**：`worker.failed` 只记日志，保留原简释，不影响已加入生词本与高亮；
  `shutdown()` 与预取一样等待 `_enrich_worker` 退出（请求自带 45s 上限）；
- **测试**：`test_english_learner_enrich.py` 覆盖简释判定、补全字段与 SRS/
  词条身份不变、prompt 与双击逐字节一致、完整词条跳过不调 AI、AI 原形不一致
  不新建词条、AI 失败保留简释，以及阅读页加入生词本后自动起后台补全的接线。

### 6.36 M6.4 / M6.5：档内进度与档内难度（2026-11-20）

- **分层**：口径与计算全在 `services/level_progress.py`（纯函数，只依赖
  `models/entities.py`，不碰数据库 / PySide6 / 网络）；`AppContext` 负责取数，
  今日页只做格式化。新增规则先改纯函数，不要写进页面。
- **进度公式**：`progress = P(next) / (P(current) + P(next))`，取最近一次
  **正式**检测的 posterior；旧数据没有 posterior 时，只有 `pending_level`
  恰为下一档才回退 `pending_count / 2`。`progress_source` 记录来源，
  界面 tooltip 必须按来源切换，不能一律写“后验计算”。
- **正式检测不能先取 N 条再过滤**：练习模式行会挤占最近窗口，
  而“正式检测 → 换一篇 → 练习”会让旧正式行被级联删除。用
  `QuizRepo.list_recent_formal_completed()` 在 SQL 层过滤 `practice_mode = 0`。
- **`practice_only` 必须单独看“最近一次已完成检测”**：进度只用正式检测，
  但“今天是不是只做了练习”要看含练习的最新一条。`AppContext` 因此把
  `list_recent_completed_with_titles(limit=1)` 追加进传入列表：
  `_latest_formal` 负责过滤进度来源，`_latest_completed` 负责识别练习状态。
  只传正式列表会让 `practice_only` 恒为 False（第 2 轮验收抓到的回归）。
- **指标可信度**：生词比例只在 `word_total >= 40` 且 `wordlist_size >= 500` 时
  参与趋势；趋势窗口是 `today-6..today` 与 `today-9..today-7`，相邻不重叠，
  ±2 个百分点内为持平。
- **hint 优先级**：`low` > `high` > `mid`。low 看最近两篇有效文章生词比例
  或近 7 天正式检测降级；high 看上一档后验 ≥0.45 或 pending 指向下一档，
  且最近一篇生词比例未超阈值；C1 固定 mid。
- **prompt 兼容**：`within_level` 为空串或 `mid` 时不插入任何文本，
  必须保持与旧版逐字节一致（有快照测试）。high 引用的是**高一档**
  （B1 → B2），不是低一档；C1 没有更高档，只要求句式与论证更复杂。
- **重试例外**：可读性重试的 `repair_note` 本身就是“换更简单的词”，
  第二次必须清空 `effective_within`，否则 prompt 同时要求“偏难”和“更简单”。
  metrics 记录的是**实际发给模型**的 hint，不是调用方传入值。
- **metrics 扩展**：`within_level / hint_reason` 追加进
  `articles.metrics_json`；readability 关闭时也写。`ReadabilityMetrics.from_dict`
  只读已知键，附加键对旧读者 / 导出安全。阈值派生用
  `dataclasses.replace`，只影响本次重试判断，不改配置默认值。
- **UI**：进度条样式 `QProgressBar#levelProgress` 走全局 QSS（不单独设
  `font-family`）；无检测、C1、练习模式都有空状态；未评分产出题不进
  “答对 X/Y”的分母。
- **测试**：`test_english_learner_level_progress.py`（纯函数与边界）、
  `test_english_learner_article.py`（prompt / metrics / 重试）、
  `test_english_learner_feedback.py`（反馈优先）、
  `test_english_learner_gui_smoke.py`（空状态与 AppContext 接线）。

### 6.37 窗口状态记忆（2026-11-21）

- **存什么**：`config.json` 的 `window` 段只有一个 `geometry` 字段，内容是
  `QMainWindow.saveGeometry()` 的 base64 文本；尺寸、位置、最大化 / 全屏状态、
  所在屏幕信息都在快照里。`services/config_store.py` 的 `WindowState` 把它当成
  不透明字符串，services 层仍然不依赖 PySide6；
- **为什么不用 width/x/maximized**：Qt 的窗口几何带框架（标题栏、边框），
  而 `setGeometry()` / `move()` 用的是客户区坐标。自己存框架坐标再
  `setGeometry()` 会**每次启动偏移一个标题栏高度**（offscreen 测试里是 2px，
  真实 Windows 上是 30px 左右）。`saveGeometry()/restoreGeometry()` 内部保存了
  框架边距并负责换算，还自带多屏兜底：旧位置不在任何现有屏幕上时会挪回可见区域；
- **时机**：`MainWindow.closeEvent` 在页面 `can_deactivate()` 通过后、等待后台
  线程之前保存（写盘失败只记 warning）；`app.main()` 在 `app.exec()` 返回后
  再调一次，覆盖「导入数据库 → 立即重启」用 `QApplication.quit()` 退出、
  closeEvent 不触发的路径。重复写同一份数据没有副作用；
- **兜底**：快照为空、base64 非法或 `restoreGeometry()` 返回 False 时，
  `gui/window_state.restore_window_state()` 显式 `resize(1240, 800)`，
  不会停在 Qt 的默认小窗口；首次启动与旧配置走的都是这条路径；
- **测试注意**：offscreen 平台只有 800x800 的虚拟屏幕，`restoreGeometry()`
  会把超宽窗口夹到屏幕内（`MainWindow` 再被 1080 最小宽度兜底）。所以
  `test_english_learner_window_state.py` 用不受最小尺寸限制的 `ModelWindow`
  验证精确往返，并比较 `frameGeometry()`（客户区 `pos()` 会差框架边距）。
