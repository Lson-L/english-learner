# Daily English 开发进度与交接

- 最后更新：2026-11-18
- 当前阶段：**M0 已完成**
- 产品方向：[PRODUCT.md](PRODUCT.md)

> 本文档是跨会话交接的唯一事实来源。每完成一个里程碑，更新「当前状态」「验证记录」
> 和「下一步」，不要只依赖聊天记录。

---

## 1. 当前状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 工程骨架、依赖分组、配置向导、真实连接测试、主窗口导航 | ✅ 完成 |
| M1 | SQLite、摸底测评、每日文章、阅读器、划词释义 | ⬜ 待开始 |
| M2 | 生词本、SM-2 复习、高亮持久化、首页真实数据 | ⬜ 未开始 |
| M3 | 打包、数据导出、主题、错误兜底 | ⬜ 未开始 |

M0 结束时，应用可以：启动 → 强制配置 AI → 真实请求测试 → 保存配置 →
进入带 5 个页面的主窗口。文章、测评、生词、复习都还是占位页。

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
```

配置文件位置（Windows）：`%APPDATA%\EnglishLearner\config.json`
日志：`%APPDATA%\EnglishLearner\logs\app.log`
想重演首次启动：删除上面的 `config.json`，或加 `--reconfigure`。

## 4. M0 文件地图

```
src/english_learner/
├── __init__.py                 版本号、产品说明、分层约定
├── __main__.py                 支持 python -m english_learner
├── app.py                      入口：日志、QApplication、首次配置判断、--reconfigure
├── services/
│   ├── ai_client.py            OpenAI 兼容客户端；连接测试；complete()；错误中文化
│   ├── config_store.py         AIConfig、JSON 配置读写、URL 规范化、Key 脱敏
│   └── provider_presets.py     DeepSeek/Kimi/通义/智谱/硅基流动/OpenRouter/Ollama 预设
└── gui/
    ├── theme.py                全局 QSS（浅色 + 深色侧边栏）
    ├── workers.py              TaskWorker(QThread)：AI 调用必须走这里
    ├── onboarding.py           3 步向导：欢迎 → 配置/测试 → 完成
    ├── main_window.py          侧边导航 + QStackedWidget（页面顺序与导航索引绑定）
    └── pages/
        ├── common.py           Card / PageHeader / StatCard / PlaceholderPage / set_status
        ├── today.py            今日页（M0 静态数字 + 说明）
        ├── placeholders.py     阅读 / 生词本 / 复习占位页
        └── settings.py         配置展示、测试连接、打开配置目录、重新配置入口

tests/
├── test_english_learner_config.py     配置读写/校验/脱敏 9 个用例
├── test_english_learner_ai_client.py  连接测试与错误翻译 7 个用例（全部 mock，无网络）
└── test_english_learner_gui_smoke.py  offscreen GUI 冒烟 2 个用例
```

工程配置改动：

- `pyproject.toml`
  - PySide6 / openai 写入默认 `dependencies`（项目只剩 GUI 应用，不再需要可选分组）
  - 新增入口 `english-learner = "english_learner.app:main"`
  - wheel 打包包含 `src/english_learner`
  - coverage 同时统计两个包
- `uv.lock` 已更新
- `README.md` 增加英语学习应用章节
- `docs/english_learner/`：本文件 + 产品方向

## 5. M0 验证记录

已执行（2026-11-18）：

| 检查 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **18 passed** |
| `ruff check src/english_learner tests` | All checks passed |
| `ruff format` | 已格式化 |
| offscreen 渲染 4 个界面（欢迎、配置、今日、设置） | 布局正常 |

未验证 / 需要人工确认：

1. **真实模型连接**：需要你填自己的 Key 跑一次 `uv run english-learner`。
   代码路径已用 fake client 覆盖，但服务商真实响应未测。
2. **真实窗口字体**：offscreen 模式下 Qt 字体库为空（`QFontDatabase.families()` 返回 0），
   截图里中文全是方框，这是**离屏环境限制**，不是应用 bug；真实 Windows 桌面会正常加载
   微软雅黑。若真实窗口也出现方框，再检查 `theme.py` 的 font-family。
3. `response_format={"type": "json_object"}` 在各家兼容网关的支持情况（M1 用）。
   M1 必须实现“服务端不支持/返回带 ```json 围栏”时的本地兜底解析。

## 6. M0 关键实现说明（避免下次踩坑）

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
今日 0、阅读 1、生词本 2、复习 3、设置 4。加页面时必须两处一起改。

## 7. M1 详细任务（按顺序做）

> M1 目标：跑通「测评定级 → 生成今日文章 → 阅读划词 → 生词入本」，
> 复习留到 M2。每个任务完成即补测试，不要攒到最后。

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

## 8. M2 预告（先别做）

- `services/srs.py`：简化 SM-2；四档 rating 1=忘记 / 2=模糊 / 3=认识 / 4=太简单；
  忘记时 `repetitions=0, interval=1, ease=max(1.3, ease-0.2)`；
  其余按 SM-2 更新 interval，`due_date = today + interval`；
- `gui/pages/review.py`：卡片翻面、键盘 1~4、会话结束统计；
- `review_logs` 落库，`daily_stats.reviews_done` 累加；
- 生词本页面接真实数据 + 编辑释义；
- 首页待复习改为按 `due_date` 统计。

## 9. 已知问题 / 技术债

| 项 | 说明 | 计划 |
|---|---|---|
| API Key 明文 | `config.json` 明文保存 | M1 后换 Windows DPAPI 或 keyring |
| 请求不可取消 | worker 只能等 30s 超时；设置页测试中直接关主窗口，Qt 可能打印 QThread 警告 | M1 统一处理（等待 / 取消标志） |
| PySide6 体积大 | 完整 `PySide6` 会拉 WebEngine/Addons | M3 评估换 `PySide6-Essentials` |
| 无重试 | `AIClient` 故意 `max_retries=0` | 业务层按任务重试（文章 1 次） |
| `json_mode` 兼容性 | 部分网关不支持 `response_format` | 必须有本地兜底解析 |
| 单温度 | 全局一个 temperature | 需要时 `complete(temperature=...)` 覆盖 |
| 配置校验重复 | `ConfigStore.load()` 调了两次 `validate()` | 顺手清理即可 |
| 导航索引耦合 | 页面顺序与按钮索引硬绑定 | 加页面时同步改两处 |
| 未打包 | 只能 `uv run` | M3 用 PyInstaller / Nuitka |

## 10. 下次会话开工指令

把下面这段直接发给 Claude：

```text
继续 py-learn 的 english_learner。先读 CLAUDE.md、docs/english_learner/PRODUCT.md
和 docs/english_learner/PROGRESS.md，确认当前是 M0 完成、准备做 M1。
从 M1-1 存储层开始，按 PROGRESS.md 第 7 节的顺序推进；每完成一个任务补测试并更新
PROGRESS.md 的状态与验证记录。不要引入 ORM，不要跳过 TaskWorker 直接调 AI。
```

开工前先自检：

```bash
uv sync
uv run pytest -q
uv run ruff check src/english_learner tests
```
