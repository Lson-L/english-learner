# Daily English 开发进度与交接

- 最后更新：2026-11-19
- 当前阶段：**M2 已完成（生词本、SM-2 复习、首页真实数据）**
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
| M3 | 数据导出、Key 加密、请求取消、性能与安装包 | ⬜ 未开始 |

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

## 4. 文件地图（M2 完成后的现状）

```
src/english_learner/
├── __init__.py                 版本号、产品说明、分层约定
├── __main__.py                 支持 python -m english_learner
├── app.py                      入口：日志、配置、Database、主窗口、首次测评
├── assets/
│   └── fonts/                  内置开源字体 Inter + Literata（OFL 1.1，见 OFL-*.txt）
├── models/
│   └── entities.py             Profile / Article / Word / Highlight / DailyStats 等
│                               frozen dataclass + from_row / to_params + 时间与 JSON 辅助
├── storage/
│   ├── db.py                   SQLite 连接、PRAGMA、建表迁移、事务、仓库入口
│   └── repositories.py         ProfileRepo / ArticleRepo / WordRepo / StatsRepo（纯 SQL）
├── services/
│   ├── ai_client.py            OpenAI 兼容客户端；test_connection / complete / complete_json
│   ├── ai_protocol.py          TextCompleter 协议（业务层只依赖一个方法）
│   ├── app_context.py          AppContext：配置 + DB + 各 Service 的组装点
│   ├── config_store.py         AIConfig、JSON 配置读写、URL 规范化、Key 脱敏
│   ├── provider_presets.py     服务商预设
│   ├── json_utils.py           parse_json_object 三档兜底 + 字段类型转换
│   ├── prompts.py              全部提示词 + PROMPT_VERSION = "m1-2"
│   ├── assessment_bank.py      3 套固定题（A2/B1/B2），默认 B1
│   ├── assessment_service.py   本地计分、AI 评分、CEFR 回退、手动选级、落库
│   ├── article_service.py      文章生成、JSON 校验、重试一次、当天缓存、换一篇
│   ├── dictionary_service.py   lemma 规范化、句子提取、查词缓存、加入生词本
│   ├── level_feedback_service.py 阅读反馈调级：AI 重新定级 + 软约束重写今日文章
│   └── srs.py                  简化 SM-2 调度 + ReviewService（纯本地，不依赖 AI）
└── gui/
    ├── fonts.py                内置字体注册 + 全局英文字体（默认 Lexend，可选 Literata/Georgia/Inter）
    ├── theme.py                全局 QSS（含阅读器/释义卡/测评短文样式）
    ├── workers.py              TaskWorker(QThread)：AI 调用必须走这里
    ├── onboarding.py           3 步配置向导
    ├── assessment_dialog.py     摸底测评弹窗（答题 → 评分 → 结果/手动选级）
    ├── level_feedback_dialog.py 阅读感受弹窗（快捷选项 + 文本 → 调级并换文章）
    ├── main_window.py          导航 + 页面栈 + AppContext + 首次测评触发
    ├── widgets/
    │   ├── article_view.py     只读 QTextEdit：双击取词、高亮、17px/1.8 行距
    │   └── word_panel.py       右侧释义卡 + 加入生词本按钮
    └── pages/
        ├── common.py           Card / PageHeader / StatCard(可刷新) / set_status
        ├── today.py            今日页：连续天数/到期复习/累计生词/文章状态/测评入口
        ├── reader.py           阅读页：生成文章、查词、加生词、换一篇、调整难度
        ├── vocabulary.py       生词本页：筛选、搜索、编辑释义、删除（M2）
        ├── review.py           复习页：卡片翻面、1~4 评分、会话统计（M2）
        └── settings.py         配置展示、测试连接、打开配置目录、重新配置入口

tests/（共 122 个用例，全部 mock AI、无网络）
├── test_english_learner_config.py        配置读写/校验/脱敏
├── test_english_learner_ai_client.py     连接测试与错误翻译
├── test_english_learner_storage.py       建表幂等/CRUD/唯一约束/级联/统计/文章替换
├── test_english_learner_json_utils.py    围栏 JSON/噪声/类型转换
├── test_english_learner_assessment.py    计分/评分/容错/手动选级/落库
├── test_english_learner_article.py       生成校验/软约束/重试/缓存/换一篇
├── test_english_learner_feedback.py      反馈调级/软约束重写/失败不改旧数据
├── test_english_learner_reader.py        lemma/句子提取/查词缓存/高亮/阅读页
├── test_english_learner_srs.py           SM-2 四档调度/易度边界/间隔上限/复习服务落库
├── test_english_learner_review_page.py   复习页状态机/翻面/评分/会话统计/快捷键
├── test_english_learner_vocabulary_page.py 生词本筛选/搜索/编辑释义/删除
├── test_english_learner_fonts.py         字体文件/字体链/Qt 注册/偏好读写/设置页切换
└── test_english_learner_gui_smoke.py     offscreen 冒烟（主窗口/今日页/测评/反馈弹窗）
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

## 6. 关键实现说明（避免下次踩坑）

### 6.1 为什么连接测试用 chat.completions 而不是 /models

不少兼容网关没有实现 `/models`。发一句极短的 chat 请求最接近真实使用场景，
同时能验证模型权限。**不要加 `max_tokens` / `temperature` 参数**：
推理模型可能把 token 全花在思考上，或拒绝非默认温度，导致误报失败。

### 6.2 线程模型

- `services/*` 全是同步阻塞代码，**禁止**在 UI 线程调用；
- 所有调用包进 `gui.workers.TaskWorker`，通过 `succeeded(object)` /
  `failed(str)` 回主线程；
- 调用方必须持有 worker 引用（`self._worker`），否则 QThread 被 GC 会丢信号；
- M0 的请求不可取消，靠 30s 超时兜底；M1 如果需要取消，再在 worker 里加标志位。

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
- 已知体验债：编辑释义后直接切筛选/页面会丢草稿（没有“未保存”提示），
  留到 M3 决定是否加脏检查。

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
src/english_learner/gui/widgets/word_panel.py    # 右侧释义卡
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

## 9. M3 任务预告（下一步做这个）

按优先级排列，每完成一块补测试并更新本文档：

1. **数据导出**：生词本 + 复习历史 + 文章导出 JSON / CSV，便于备份与迁移；
2. **API Key 加密**：Windows DPAPI（`ctypes` 调 CryptProtectData）或 keyring，
   保持 `config.json` 向后兼容；
3. **请求可取消**：`TaskWorker` 加取消标志；生成文章 / 查词进行中关闭窗口时
   等待线程结束，消除 QThread 警告；
4. **错误兜底与离线提示**：区分网络错误 / 认证错误 / JSON 契约错误，
   离线时复习页与生词本必须完全可用（现在已满足，补 UI 提示即可）；
5. **生词本增强**：批量删除、按加入时间排序、未保存释义的脏检查提示；
6. **性能**：词库上千条时分页或虚拟列表；复习页大队列的加载速度；
7. **可选**：深色模式、安装器 / 自动更新（打包已在 M1 完成，
   `DailyEnglish.spec` 可直接复用）。

## 10. 已知问题 / 技术债

| 项 | 说明 | 计划 |
|---|---|---|
| API Key 明文 | `config.json` 明文保存 | M3 换 Windows DPAPI 或 keyring |
| 请求不可取消 | worker 只能等 30s 超时；生成/查词进行中直接关主窗口，Qt 可能打印 QThread 警告 | M2 加取消标志与等待 |
| SQLite 跨线程 | 连接 `check_same_thread=False` + 一把 RLock；所有仓库调用都走锁 | 保持；不要绕过 Database 直接执行 SQL |
| 查词即入词库 | 按 M1 设计，`lookup()` 把结果写入 `words`（兼作缓存）。M2 已确认**不改数据结构**：查过的词也会出现在生词本里，用户可手动删除 | M3 再决定是否加 `in_vocabulary` 标志列 |
| 测评题库固定 B1 | A2/B2 套已写好，UI 暂不提供选择 | 以后做自适应或设置项 |
| 文章重试成本 | 校验失败会重试一次，失败的那次也计费 | 可接受；如频繁失败再调 prompt / 放宽校验 |
| `json_mode` 兼容性 | 已实现 `complete_json()` 降级 + 本地三档解析；真实网关报错文案覆盖度待实测 | 实测后补充识别关键字 |
| PySide6 体积大 | 完整 `PySide6` 会拉 WebEngine/Addons | M3 评估换 `PySide6-Essentials` |
| 单温度 | 全局一个 temperature，业务层按任务覆盖 | 够用 |
| 配置校验重复 | `ConfigStore.load()` 调了两次 `validate()` | 顺手清理即可 |
| 导航索引耦合 | 页面顺序与按钮索引硬绑定（`main_window.py` 的 `PAGE_*` 常量） | 加页面时同步改两处 |
| 反馈调级成本 | 一次调整 = 两次 AI 调用（定级 + 写文章），文章失败还会再重试一次 | 可接受；如太慢可换小模型或合并为一次调用 |
| 调级依赖模型判断 | 是否降档取决于模型是否遵守 prompt；极端情况下可能维持原级 | 已验证有兜底；必要时后续加“手动指定等级重写” |
| 重写清空高亮 | 按设计，新文章无法沿用旧偏移，旧高亮随文章级联删除（生词保留） | 可接受 |
| 打包状态 | 已有 `DailyEnglish.spec`（onefile），产物 `dist/DailyEnglish.exe` | 安装器 / 自动更新留到 M3 |
| 释义草稿会丢 | 生词本编辑释义后直接切筛选/页面，未保存的修改被丢弃（无脏检查） | M3 加“未保存”提示 |
| 复习队列无每日上限 | 一轮最多 200 词，但一天可以开多轮；积压很多时体验偏长 | M3 视实际使用决定是否加每日上限 |
| 复习快捷键 | 数字键 1~4 在中文输入法下未实测；必要时改成 Alt+1~4 或可配置 | 真实桌面确认后调整 |
| 复习落库非原子 | `review()` 依次写词条、`review_logs`、`daily_stats`，未包同一个事务 | 概率极低；M3 视需要把三步包进 `Database.transaction()` |

## 11. 下次会话开工指令

把下面这段直接发给 Claude：

```text
继续 py-learn 的 english_learner。先读 CLAUDE.md、docs/english_learner/PRODUCT.md
和 docs/english_learner/PROGRESS.md，确认当前是 M2 完成、准备做 M3。
按 PROGRESS.md 第 9 节的顺序推进：先做数据导出 + 单测，再做 API Key 加密、
TaskWorker 取消标志。动手前先跑 pytest/ruff 确认基线。每完成一块补测试并更新
PROGRESS.md 的状态与验证记录。不要引入 ORM，不要跳过 TaskWorker 直接调 AI，
services/storage 不得 import PySide6。
```

开工前先自检：

```bash
uv sync
uv run pytest -q
uv run ruff check src/english_learner tests
```
