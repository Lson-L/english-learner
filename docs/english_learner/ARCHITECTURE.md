# Daily English 架构与关键实现

> **按需阅读**：改 AI 客户端 / 存储 / 阅读检测 / 导出等模块前，先看对应小节。
> 当前状态见 [PROGRESS.md](PROGRESS.md)，历史记录见 [HISTORY.md](HISTORY.md)，
> 已知问题见 [TECH_DEBT.md](TECH_DEBT.md)。章节编号沿用拆分前的 PROGRESS。

## 4. 文件地图（M3.15 完成后的现状）

```
src/english_learner/
├── __init__.py                 版本号、产品说明、分层约定
├── __main__.py                 支持 python -m english_learner
├── app.py                      入口：日志、配置、Database、主窗口、首次测评
├── assets/
│   └── fonts/                  内置开源字体 Inter + Literata（OFL 1.1，见 OFL-*.txt）
├── models/
│   └── entities.py             Profile / Article / Word / Highlight / DailyStats /
│                               ReadingQuiz / AICall / AIUsageSummary 等 frozen dataclass
├── storage/
│   ├── db.py                   SQLite 连接、PRAGMA、建表迁移（schema v7）、事务、仓库入口
│   └── repositories.py         Profile / Article / Word / Review / Stats / Quiz / Evidence /
│                               WeeklyReview / AICall 仓库（纯 SQL）
├── services/
│   ├── ai_client.py            OpenAI 兼容客户端；test_connection / complete / complete_json
│   │                           支持 AICallRecorder 注入（token/耗时/错误落 ai_calls）
│   ├── ai_protocol.py          TextCompleter 协议（业务层只依赖一个方法）
│   ├── app_context.py          AppContext：配置 + DB + 各 Service 的组装点
│   ├── config_store.py         AIConfig、JSON 配置读写、URL 规范化、Key 脱敏
│   ├── provider_presets.py     服务商预设
│   ├── json_utils.py           parse_json_object 三档兜底 + 字段类型转换
│   ├── prompts.py              全部提示词 + PROMPT_VERSION = "m3-14"
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
│   ├── log_files.py            日志尾部读取 / 轮转文件排序 / 关键字筛选（纯 Python）
│   ├── export_service.py       JSON / CSV 数据导出（十一张表，含 AI 调用记录）
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
    ├── ai_usage_dialog.py      AI 调用记录弹窗（范围汇总 / 按任务 / 明细 / 清空）
    ├── log_viewer_dialog.py    运行日志查看器（轮转文件 / 关键字筛选 / 自动刷新）
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
        └── settings.py         配置、调用记录摘要与入口、字体、数据位置、JSON/CSV 导出

tests/（共 313 个用例，全部 mock AI、无网络）
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
  `ai_calls`（可选，默认 None），旧测试/调用方不传也能工作。
