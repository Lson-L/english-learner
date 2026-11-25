# M7：真实文章自动供给（重点排期，执行依据）

- 状态：**M7.0 ~ M7.4 已完成（2026-11-25）**；下一步 M7.6 内容包导入 / M7.7 在线源
- 基线：SQLite schema **v9**；全量测试 **748 passed**（2026-11-25）
- 关联：[AI_TASKS.md](AI_TASKS.md) 第 11 节、[PROGRESS.md](PROGRESS.md)、
  [ARCHITECTURE.md](ARCHITECTURE.md)、[PRODUCT.md](PRODUCT.md)
- 执行方式：**一次只做一个里程碑**，完成 → 跑测试 → 更新文档 → 停下等确认

---

## 0. 一句话目标

普通用户不需要自己找文章、判断难度、点「抓取」。App 每次打开时，根据最近的
阅读/检测状态和上一篇真实文章的反馈，**自动选一篇作为今天的阅读**：
优先用 SQLite 缓存；缓存不足时有网则从内置 URL 源补充（M7.7）；全部失败则提供
手动 URL / 粘贴入口，再不行回退 AI 文章。读完的「轻松 / 刚好 / 吃力」决定下一篇的
难度和间隔。内容与进度都存 SQLite，靠现有「整库快照」在两台电脑之间搬运。

---

## 1. 产品决策（冻结，后续不得静默修改）

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | **离线优先，在线补充。** SQLite 缓存是主来源；有网时从内置 URL 源后台抓取并缓存；全部失败时回退 AI 文章，并提供手动 URL / 粘贴入口 | 国内网络不稳定；不能把联网抓取作为唯一来源，否则首次启动 / 断网就没有真实文章 |
| D2 | **自动供给，不要求用户点按钮。** 每次启动决策一次，每天最多一篇真实文章；阅读页提供「换回 AI 文章」覆盖 | 用户明确要求自动；但必须留逃生口，不能困住用户 |
| D3 | **真实文章检测固定练习模式。** 不计等级证据、不改 `profile`、不改 SRS | 文章难度是预标注/估算的，不能污染 M6 的等级闭环 |
| D4 | **内置离线文章：预置题目 + 本地判分，0 AI；在线抓取 / 手动 URL：题目按需生成（1 次 AI，练习模式）** | 离线与成本可控；任意 URL 无法预置题目 |
| D5 | **SQLite 是运行期唯一数据源**；`corpus.json` 只是构建期种子；两台机器用整库快照导出/导入 | 用户有两台真实电脑；现有 `database_transfer` 就是整库覆盖 |
| D6 | **反馈驱动。** 完成且感觉良好 → 保持/缩短间隔；吃力或低分 → 降一档 + 拉长间隔；弃读 → 冷却 + 降一档 | 用户明确要求「读完感觉良好再安排新的」 |
| D7 | 真实文章**不参与** M6.4 / M6.5 的等级进度与难度 hint 聚合；只参与阅读剂量与自身反馈 | 防止真实文本的生词率干扰「档内把握」口径 |
| D8 | **明确不做**：把联网抓取作为唯一来源；无上限后台抓取；绕过付费墙 / 登录；把真实文章计入等级证据；两台机器合并进度；把正文放进对外分享的导出包 | 见第 14 节 |
| D9 | **运行期离线优先。** 真实文章路径不依赖任何外网；文章库为空、种子失败或供给失败时自动回退 AI 文章，不阻塞启动 | 国内网络 / 断网 / 外网不可达是常态，不能因为内容问题让用户打不开 app |
| D10 | **构建期内容获取优先用户提供。** 不假设 Gutenberg / Wikipedia / VOA 可达；打不开就换源或由用户粘贴正文，并记录实际来源 | 国内访问境外站点不稳定；内容是一次性构建工作，不该卡住开发 |
| D11 | **内容包手动导入。** 无外网环境可把 `corpus.json` 拷到另一台机器导入；不做运行时自动下载 | 同时解决网络受限与两台机器文章库同步 |
| D12 | **内置 URL 只用稳定、许可明确的源**（Gutenberg 纯文本、Wikipedia API、Lit2Go 等），优先纯文本 / API，不把新闻站 HTML 当主源 | URL 会腐烂、改版、上付费墙；纯文本 / API 解析稳定 |
| D13 | **抓取内容必须缓存进 SQLite**；离线只用缓存，不回源；抓取失败不覆盖已有好内容 | 断网 / 两台机器可用；失败不能破坏已读内容 |
| D14 | **抓取文章的题目按需生成**（AI 1 次，练习模式）；内容 hash 命中预置版本时用预置题目（0 AI） | 任意 URL 无法预置题目；固定题只适用于稳定文本 |

> **对「自动抓取」的统一解释**：本计划里的「自动」= 自动选题、自动上架；
> 不是自动联网下载。真正的联网抓取不在 M7 范围。

---

## 2. 术语与状态机

| 术语 | 含义 |
|---|---|
| 文章库 | `real_texts` 表，构建期由 `corpus.json` 种子写入 |
| 供给 | 从文章库选一篇未读、难度合适的文章，写成当天的 `articles` 行 |
| 反馈 | 用户完成检测后的分数 + 自评（easy / ok / hard） |
| 弃读 | 已供给但 7 天内没有完成检测 |

`real_text_history.status` 状态机：

```
served ──完成检测──> completed
   │
   ├──用户点「换回 AI 文章」──> overridden
   │
   └──7 天未完成──> abandoned
```

- `served`：已写成当天文章，还没完成检测。
- `completed`：完成检测，记录 `score / self_report / outcome`。
- `overridden`：用户主动换回 AI 文章，算一次「跳过」，不惩罚。
- `abandoned`：供给后 7 天仍未完成，算「弃读」，下次降难度。

---

## 3. 数据模型（schema v9）

### 3.1 新表 `real_texts`

```sql
CREATE TABLE IF NOT EXISTS real_texts (
    id              TEXT PRIMARY KEY,           -- 稳定 id，如 gutenberg-the-gift-of-the-magi
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,              -- 段落用 \n\n 连接，与 articles.content 一致
    level           TEXT NOT NULL,              -- A2 / B1 / B2 / C1
    level_source    TEXT NOT NULL DEFAULT '',   -- publisher / estimated / manual
    source          TEXT NOT NULL DEFAULT '',
    source_url      TEXT NOT NULL DEFAULT '',
    license         TEXT NOT NULL DEFAULT '',
    license_url     TEXT NOT NULL DEFAULT '',
    published       TEXT NOT NULL DEFAULT '',
    topics_json     TEXT NOT NULL DEFAULT '[]',
    word_count      INTEGER NOT NULL DEFAULT 0,
    excerpt         INTEGER NOT NULL DEFAULT 0,
    attribution     TEXT NOT NULL DEFAULT '',
    questions_json  TEXT NOT NULL DEFAULT '[]', -- QuizQuestion JSON 数组
    -- M7.7 在线源：抓取时间、内容指纹、题目来源（bundled / ai）
    fetched_at      TEXT NOT NULL DEFAULT '',
    content_hash    TEXT NOT NULL DEFAULT '',
    questions_source TEXT NOT NULL DEFAULT 'bundled',
    corpus_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
```

### 3.2 新表 `real_text_history`

```sql
CREATE TABLE IF NOT EXISTS real_text_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text_id       TEXT    NOT NULL REFERENCES real_texts(id) ON DELETE CASCADE,
    served_on     TEXT    NOT NULL,             -- YYYY-MM-DD
    article_id    INTEGER,
    level         TEXT    NOT NULL DEFAULT '',
    word_count    INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'served',  -- served/completed/overridden/abandoned
    score         REAL    NOT NULL DEFAULT 0,         -- 0..1，选择题正确率
    self_report   TEXT    NOT NULL DEFAULT '',        -- easy/ok/hard
    outcome       TEXT    NOT NULL DEFAULT '',        -- success/neutral/struggle/skip/abandon
    completed_at  TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_real_text_history_text ON real_text_history(text_id);
```

> 用自增 `id` 而不是 `text_id` 主键：以后基准复测同一篇时允许出现多行；
> 「未读」判断用 `text_id NOT IN (SELECT text_id FROM real_text_history)`。

### 3.3 `articles` 增三列（幂等 ALTER）

```sql
ALTER TABLE articles ADD COLUMN source     TEXT NOT NULL DEFAULT 'generated';
ALTER TABLE articles ADD COLUMN source_url TEXT NOT NULL DEFAULT '';
ALTER TABLE articles ADD COLUMN source_ref TEXT NOT NULL DEFAULT '';
```

- `source`：`generated` / `real` /（以后）`imported`；
- `source_ref`：真实文章 id；生成文章为空串；
- 旧库升级后所有旧文章自动为 `generated`，行为不变。

### 3.4 `review_logs` 增一列（M7.4 用）

```sql
ALTER TABLE review_logs ADD COLUMN mode TEXT NOT NULL DEFAULT '';  -- spelling/context/recall
```

### 3.5 `config.json` 增 `real_text` 段

```json
"real_text": {
  "auto_enabled": true,
  "last_decision_on": ""
}
```

- `auto_enabled`：设置页可关；关闭后永不自动供给，阅读页仍可手动换回 AI。
- `last_decision_on`：保证每天只决策一次。

### 3.6 种子策略（`services/real_text_library.py`）

```python
def seed_real_texts(db, corpus: RealTextCorpus) -> int:
    """把 corpus.json 幂等写入 real_texts；只升不降。"""
    stored = db.query_one("SELECT COALESCE(MAX(corpus_version), 0) AS v FROM real_texts")["v"]
    if corpus.version <= stored:
        return 0
    for text in corpus.texts:
        db.execute("""
            INSERT INTO real_texts (id, title, content, level, ..., corpus_version, created_at)
            VALUES (:id, :title, :content, :level, ..., :corpus_version, :created_at)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, content=excluded.content, level=excluded.level,
                questions_json=excluded.questions_json, ..., corpus_version=excluded.corpus_version
        """, {...})
    return len(corpus.texts)
```

规则：

- **幂等**：同版本重复启动直接返回；
- **只升不降**：导入一个 corpus 版本更高的库，不会被当前 app 的旧内容覆盖；
- **旧库自愈**：v8 库没有 `real_texts`，迁移建表 + 种子补齐；
- **可预期覆盖**：app 升级带更高 `corpus_version` 时会覆盖同 id 内容；V1 不支持用户改文章；
- **在线源**：启动时同时把 `sources.json` 种子写入 `content_sources`（只增不删，
  `enabled` 由内置列表控制），抓取状态写回该表。

### 3.7 导出同步

| 通道 | 处理 |
|---|---|
| 整库快照 `database_transfer` | 文件复制，自动包含新表；`REQUIRED_TABLES` **保持** `{profile, articles, words}`，不要加 `real_texts`（否则合法 v8 库会被拒绝导入） |
| JSON / CSV `export_service` | 加 `real_texts`、`real_text_history`、`content_sources`；`TABLE_LABELS`、`SUMMARY_ORDER`、`CSV_COLUMNS` 同步；文件头「十二张表」改「十五张表」 |
| 许可 | 快照/JSON 含正文，个人备份可以；不要公开分发 |

### 3.8 运行期降级（断网 / 外网不可达 / 文章库为空）

真实文章路径**不发起任何网络请求**。以下情况都自动回退，不阻塞启动：

| 情况 | 行为 |
|---|---|
| `corpus.json` 缺失 / 损坏 | 记日志，跳过种子；真实文章供给关闭，AI 文章照常 |
| `real_texts` 为空 | `decide_today` 返回 `skip("no_library")`；AI 文章照常 |
| 种子失败（磁盘 / 权限 / SQL） | 记日志，继续启动；本次不供给 |
| `serve_real_text` 失败 | 回滚当天刚写入的文章，回退 AI 生成 |
| 预置题目种子失败 | 同上回滚；**不允许留下「有文章没题目」的半截状态** |
| 断网 / 外网不可达 | 正文、预置题、本地判分全部可用；查词 / 预取走现有失败降级 |
| 导入的旧库没有 `real_texts` | 启动迁移建表 + 种子补齐；失败则回退 AI |

**原子性要求**：`serve_real_text` 要么「文章 + 题目 + 历史」全部成功，要么全部不写。
推荐在 `ArticleService.serve_real_text` 内部 try/except：失败时 `delete_by_date(today)`
并返回 `None`；供给层看到 `None` 就不设置任何状态，阅读页自然走 AI 生成。

### 3.9 在线源表 `content_sources`（M7.7）

```sql
CREATE TABLE IF NOT EXISTS content_sources (
    id             TEXT PRIMARY KEY,          -- 稳定 id，如 gutenberg-...
    title          TEXT NOT NULL DEFAULT '',
    url            TEXT NOT NULL,
    level          TEXT NOT NULL DEFAULT '',
    license        TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_attempt_at TEXT NOT NULL DEFAULT '',
    last_error     TEXT NOT NULL DEFAULT '',
    failure_count  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
```

- 内置 URL 列表放在 `assets/real_texts/sources.json`，启动时种子写入本表；
- 抓取状态（失败次数、上次错误）写回本表，用来做 24 小时退避；
- 抓取成功的正文写入 `real_texts`（`source='fetched'`、`source_url`、`content_hash`），
  **不是**写回 `content_sources`。

---

## 4. 内容库 `corpus.json`

### 4.0 这个文件是什么、在哪

`corpus.json` 是**构建期种子文件**，不是运行时数据：

- 内容：文章正文、等级、来源、许可、署名、词数、每篇 4 道题；
- 作用：构建 / 启动时 `seed_real_texts()` 写入 SQLite 的 `real_texts` 表；
  **运行期 app 只读 SQLite，不读这个 JSON**；
- 位置（开发）：`src/english_learner/assets/real_texts/corpus.json`（同目录还有 `NOTICE.md`）；
- 位置（打包）：exe 内 `english_learner/assets/real_texts/corpus.json`；
  `DailyEnglish.spec:129` 已把整个 `assets` 目录原样打包，**不需要改 spec**；
- 用户数据目录 `%APPDATA%\EnglishLearner\` 里**没有**这个文件；那里是 `data.db`；
- 它同时是 M7.6「内容包手动导入」使用的文件格式；
- M7.7 还会增加 `assets/real_texts/sources.json`：只存内置在线 URL 和元数据，**不存正文**；
  抓取成功后的正文写入 `real_texts`。`corpus.json` 与 `sources.json` 都只是构建期资源，
  运行期 app 只读 SQLite。

数据流：

```
开发期: assets/real_texts/corpus.json   （可读、可 review、可 pytest 校验）
   ↓ 启动时 seed_real_texts()（幂等，corpus_version 只升不降）
运行期: SQLite real_texts              （app 只读这里）
   ↓
选题 / 阅读 / 本地判分
```

为什么不用 SQLite 直接当唯一来源：新装机器需要一份可读、可 review、可测试的内容源；
把 8~16 篇长文直接写进 SQL 难维护、难校验、难带许可字段。SQLite 是运行期存储，
JSON 是构建期源，两者职责不同。

### 4.1 来源白名单

| 来源 | 许可 | 适合档 | 用法 |
|---|---|---|---|
| Project Gutenberg | 公有领域（美国：1930 年前出版） | B2 / C1 | 短篇/散文，截 600~900 词，去掉头尾 |
| Simple English Wikipedia | CC BY-SA | A2 / B1 | 非虚构科普、地理、历史；署名 + 链接 |
| VOA Learning English | 美国政府作品（多数 PD） | A2 / B1 | 分级新闻；逐篇核对第三方内容 |
| Lit2Go（FCIT） | 公有领域 | A2~C1 | 分级短文 |
| Our World in Data / NASA / NOAA | CC BY / PD | B2 / C1 | 选纯文字段落，避开图表 |

**不收**：Guardian / BBC / NYT / Economist（版权）、MLK《I Have a Dream》（仍在版权期）、来源不明转载。

### 4.2 选文标准

- 250~900 词；长文可截取，但开头完整、结尾收得住，标 `excerpt: true`；
- 自包含；专有名词 ≤8%；生僻文化指涉避开或加一句注释；
- A2/B1 短句、具体；B2/C1 可叙事/论证；
- 不含表格、图片说明、代码；
- 每篇 1~2 个主题标签（science / culture / history / tech / nature / everyday）。

### 4.3 数量

V1 最低 **8 篇（每档 2 篇）**，目标 **16 篇（每档 4 篇）**；每篇 **4 道选择题**。
8 篇按 1~3 周一篇也能撑 2~4 个月，先跑通再扩到 32 篇。

### 4.4 文件结构

```
src/english_learner/assets/real_texts/
  corpus.json
  NOTICE.md
```

`corpus.json`：

```json
{
  "version": 1,
  "texts": [
    {
      "id": "gutenberg-the-gift-of-the-magi",
      "title": "The Gift of the Magi",
      "author": "O. Henry",
      "source": "Project Gutenberg",
      "source_url": "https://www.gutenberg.org/ebooks/...",
      "license": "Public Domain",
      "license_url": "https://www.gutenberg.org/policy/license",
      "published": "1905",
      "level": "B2",
      "level_source": "manual",
      "topics": ["fiction", "short story"],
      "word_count": 780,
      "excerpt": true,
      "attribution": "O. Henry, public domain.",
      "paragraphs": ["...", "..."],
      "questions": [
        {
          "kind": "choice",
          "prompt": "...",
          "options": ["...", "...", "...", "..."],
          "answer_index": 0,
          "explanation": "...",
          "skill": "comprehension",
          "difficulty": "B2"
        }
      ]
    }
  ]
}
```

题目复用 `models/entities.py` 的 `QuizQuestion` 字段；**只出选择题**，不出口语/写作题。

### 4.5 校验（`tests/test_real_text_corpus.py`）

- `version` / `texts` 存在，`id` 唯一；
- `license` 在白名单（Public Domain / CC0 / CC BY / CC BY-SA / Personal use；
  `Personal use` 只给 warning，不失败，但禁止公开分发）；
- `level` ∈ {A2,B1,B2,C1}，每档至少 **2** 篇（低于 4 篇给 warning，不失败）；
- `word_count` 与 `paragraphs` 实际词数一致，150~2000（与 M7.7 抓取口径一致；
  250~900 是选文建议，不是硬校验）；
- 不含 HTML 标签、Gutenberg 头尾标记（`*** START OF` / `*** END OF`）；
- 每题 4 个选项、`answer_index` 在 0~3、`explanation` 非空；
- 每篇恰好 4 道选择题。

### 4.6 构建流程

1. AI 按白名单起草候选（需要具体 URL 时由用户提供；**不要依赖搜索**）；
2. 规范化：去头尾、统一换行、按空行切段、截取、标 `excerpt`；
3. AI 起草 4 道理解题 + 解析；
4. **用户人工过一遍题目**（基准效度靠这一步）；
5. 写 `corpus.json` + `NOTICE.md`；
6. 跑校验测试；
7. 确认 `assets/real_texts/` 在 `src/english_learner/assets/` 下；
   `DailyEnglish.spec:129` 已整目录打包 `assets`，无需改 spec。

### 4.7 构建期内容获取（国内网络不可达的备用路径）

**不假设** Gutenberg / Simple English Wikipedia / VOA 在当前网络可达。

优先级：

1. **用户提供的正文或 URL**（最可靠）：用户把文章贴进 `corpus.json`，或给一个本机可打开的 URL；
2. **本机可访问的镜像 / 替代源**：Gutenberg 镜像、Lit2Go、Our World in Data、NASA 等，逐个试，失败就换；
3. **用户本地已有的英文书 / 文章**（个人使用；记录来源与许可）。

规则：

- 单个源超时 / 403 / 被墙 → 跳过，不无限重试；
- 每篇必须记录 `source` / `source_url` / `license` / `attribution`；
  用户提供但无法确认许可的，不进库；
- 不依赖 WebSearch（当前环境不可用）；WebFetch 只打用户给的具体 URL；
- 内容不够 8 篇就先上 4~6 篇，校验按「每档 ≥2」放行，后续补；
- 构建是一次性工作，**不要把网络可达性变成运行时依赖**。

### 4.8 内容包手动导入（M7.6，网络受限时的兜底）

- 设置 → 数据 → **导入内容包**，选择一个 `corpus.json`；
- 复用 4.5 的校验 + `seed_real_texts`（只升不降）；
- 适合无外网的机器：在能上网的机器生成/下载内容包，拷过去导入；
- 也适合两台机器同步文章库（不必整库搬运）；
- **不做运行时自动下载**；导入失败保留原文章库。

### 4.9 内容准备：你需要提供什么

**不需要写 JSON。** 任选一种：

1. **直接粘贴正文**（最稳）：附标题 / 来源 / 许可（不确定就写「不确定」）；
2. **给一个你能打开的 URL**：AI 用 WebFetch 抓；打不开（被墙 / 付费墙 / 登录 / 反爬）
   就改回方式 1；
3. **给本地文件路径**：`.txt` / `.md` / `.html` 均可。

AI 负责：清洗（去广告 / 导航 / Gutenberg 头尾）、分段、数字数、标 CEFR、
写 4 道理解题 + 解析、记录来源与许可、写进 `corpus.json`、跑校验。
你负责：过一遍题目和等级（基准效度靠这一步）。

建议先来 **1~2 篇**（最好一易一难）跑通流程，再补到 8 篇（每档 2 篇）。
许可优先公有领域 / CC；个人使用的版权文章也可以，标 `Personal use`，
校验给 warning 不失败，但**不要公开分发**内容包或数据库。

### 4.10 在线内容源与手动 URL（M7.7，推荐）

**目标**：不要求用户自己准备 8 篇。App 内置一组稳定 URL，有网时后台抓取并缓存；
全部失败时，用户可在弹窗里粘贴 URL 或正文，走同一套入库流程。

**四层来源（从快到慢）：**

| 层 | 来源 | 需要网络 | 失败后 |
|---|---|---|---|
| L1 | SQLite 已缓存文章（`real_texts`） | 否 | 下一层 |
| L2 | 内置 `sources.json` 的稳定 URL | 是 | 下一层 |
| L3 | 用户手动 URL / 粘贴正文 | 是 / 否 | 下一层 |
| L4 | AI 生成文章（现有） | 是 | 离线时只用缓存 / 复习 |

**内置 URL 的选择：**

- 只用稳定、许可明确的源：Gutenberg 纯文本（`.txt`）、Wikipedia API（`explaintext`）、
  Lit2Go 等；
- **不把新闻站 HTML 当主源**：URL 会腐烂、改版、上付费墙；
- 每条源记录 `id/title/url/level/license/source/enabled`，存 `sources.json`，
  启动种子写入 `content_sources`。

**抓取管线（`services/article_fetcher.py`）：**

1. 只允许 `http/https`；拒绝 `file://`、`localhost`、私有 IP；
2. 超时 10s、最多 2 次重试、最大 5MB、最多 5 次重定向；
3. `text/plain` 直接用；`text/html` 用轻量正文提取：
   去掉 `script/style/nav/header/footer/aside`，优先 `<article>` / `<main>`，
   再收集 `<p>` 文本；
4. 校验：英文占比、150~2000 词、至少 1 段、不是登录 / 付费墙页面
   （关键词 + 文本量双重判断）；
5. **先预览再入库**：弹窗显示提取后的标题 / 词数 / 前几段，用户确认或改为粘贴；
6. 入库：`real_texts`（`source='fetched'`、`source_url`、`license`、`fetched_at`、
   `content_hash`）；同一 URL + hash 不重复抓；
7. 失败：写 `content_sources.failure_count / last_error`，同一 URL 24 小时内不重试；
   **不覆盖已有缓存**。

**手动 URL / 粘贴：**

- 今日页卡片 / 设置页入口：「内置源不可用？粘贴链接或正文」；
- URL 抓取失败 → 显示原因（超时 / 被墙 / 付费墙 / 正文太少）→ 切换为粘贴；
- 粘贴正文同样走第 4~6 步：清洗、校验、预览、入库；
- 许可标 `Personal use`，不公开分发。

**题目：**

- 抓取 / 手动内容默认 `questions_source='ai'`：复用现有出题能力，只生成 3~4 道
  当前等级选择题，`practice_mode=True`，**不生成第二轮、不跑盲评 / 后验**；
- 若 `content_hash` 命中预置版本（`questions_source='bundled'`），用预置题，0 AI；
- 出题失败（AI 不可用 / 格式不合格）→ 不阻断阅读：先读正文，题目稍后重试，
  或直接回退 AI 文章。

**实现约束：**

- HTTP 用 `httpx2`（openai 已依赖；若直接用，在 `pyproject.toml` 显式声明）
  或标准库 `urllib.request`，不引入新的网络栈；
- 正文提取**不引入 lxml / trafilatura**：先用标准库 `html.parser` 的轻量实现
  + 预览确认；提取质量差就提示用户粘贴，不硬猜；
- 测试全部 mock HTTP，**不跑真实网络**，避免用例受墙 / 超时影响。

**安全与隐私：**

- 只发 `User-Agent: DailyEnglish/1.0`，不带用户信息；
- 不绕过登录 / 付费墙；401 / 402 / 403 直接失败；
- 不抓 `localhost` / 内网地址；限制响应大小，防止恶意大文件；
- 抓取在 `TaskWorker` 里执行，UI 不阻塞；失败只记元数据，不把正文写日志。

**与自动供给的关系：**

- `decide_today` 先看 L1 缓存；缓存足够就不联网；
- 缓存不足时，后台按 L2 顺序补 1 篇，不阻塞当天阅读；
- L2 全失败才展示 L3 入口，**每天最多提示一次**，可关闭；
- 任何一层失败都回到 L4，绝不让「抓不到文章」变成「今天没得读」。

**测试：**

- 纯文本 / HTML / 重定向 / 超时 / 403 / 正文太少 / 非英文 / 超大响应；
- `file://`、localhost 被拒；响应上限生效；
- 预览确认后才入库；失败不覆盖缓存；
- 24 小时退避；手动 URL 成功 / 失败；粘贴正文成功；
- 抓取文章题目 1 次 AI、练习模式、不改等级；
- 断网时只用缓存，AI 文章照常。

---

## 5. 自动供给引擎（`services/real_text_supply.py`）

### 5.1 输入（决策本身不调 AI；抓取见 M7.7）

- `profile`：当前等级、`level_confidence`、`pending`；
- `daily_stats` 近 30 天；
- `articles` 近 30 天（`source`、`metrics_json` 生词率、`read_at`）；
- `reading_quizzes` 最近 3 次**非练习**完成（分数、是否降级）；
- `real_text_history` 最近一条（outcome / served_on）；
- `config.real_text`；
- `real_texts` 未读候选。

### 5.2 硬门槛（全部满足才供给）

| 编号 | 门槛 | 规则（起始值，冻结） | 不满足 |
|---|---|---|---|
| G0 | 开关 | `auto_enabled = true` | 不供给 |
| G1 | 今天无文章 | `articles.get_by_date(today) is None` | 不覆盖 |
| G2 | 每天一次 | `last_decision_on != today` | 直接返回 |
| G3 | 活跃度 | 近 30 天 ≥7 个活跃日（阅读/复习/加词任一） | 新用户，不供给 |
| G4 | 未降级 | 近 7 天无 `result_level < previous_level` 的正式检测 | 先稳住 |
| G5 | 近期状态 | 近 3 次正式检测均分 ≥0.6（无检测则跳过本项） | 不供给 |
| G6 | 生词率 | 近 3 篇**生成文章**生词率均值 ≤8% | 不供给 |
| G7 | 回归保护 | 连续 7 天没活动后，回来头 2 天不供给 | 别一回来上难度 |
| G8 | 调级保护 | 最近一次升降级在 7 天内不供给 | 先适应新档 |
| G9 | 反馈间隔 | 见 5.3 | 等待 |
| G10 | 候选存在 | 未读、等级 ±1、预估生词率 3%~7% | 不供给 |

### 5.3 反馈间隔（G9）

| 上一篇 outcome | 最小间隔 | 下一篇等级窗口 |
|---|---|---|
| `success` | 3 天 | 当前档 ±1，优先同档 |
| `neutral` | 7 天 | 当前档 ±1 |
| `struggle` | 14 天 | **当前档 -1**（不低于 A2） |
| `abandoned` | 10 天 | **当前档 -1** |
| `skip`（用户换回 AI） | 7 天 | 当前档 ±1 |
| 无历史 | 7 天 | 当前档 ±1 |

### 5.4 候选排序

```text
score = 0.4 * min(距上次真实文章天数 / 14, 1)   # 越久越该读
      + 0.3 * 近三次正式检测均分                 # 状态越好越适合
      + 0.2 * (1 - |预估生词率 - 0.05| / 0.05)   # 越接近 5% 越合适
      + 0.1 * 题材是否与上次不同                  # 避免连续同题材
```

同分用 `hash(text_id + today)` 确定性打散。预估生词率用现有
`services/readability.py` 按**该用户认识的词**计算，不新增词表逻辑。

### 5.5 决策伪代码

```python
def decide_today(...) -> RealTextDecision:
    if not config.auto_enabled: return RealTextDecision.skip("disabled")
    if articles.get_by_date(today): return RealTextDecision.skip("article_exists")
    if config.last_decision_on == today: return RealTextDecision.skip("already_decided")

    if active_days_30 < 7: return skip("new_user")
    if downgraded_within(7): return skip("downgraded_recently")
    if avg_last3_quiz < 0.6: return skip("struggling")
    if mean_unknown_ratio_last3_generated > 0.08: return skip("too_hard")
    if returning_after_break and active_days_since_return < 2: return skip("welcome_back")
    if level_changed_within(7): return skip("level_changing")

    spacing, level_window = spacing_from_last_outcome(history[-1])
    if days_since_last_real < spacing: return skip("too_soon")

    candidates = library.unread().within(level_window)
    scored = [(t, estimate_unknown_ratio(t, profile)) for t in candidates]
    eligible = [(t, r, score(t, r)) for t, r in scored if 0.03 <= r <= 0.07]
    if not eligible: return skip("no_candidate")

    best = max(eligible, key=lambda x: (x[2], x[0].id))
    return RealTextDecision.serve(best.text, best.score, reason_copy(...))
```

### 5.6 启动顺序（关键）

现有启动：`app.py:203` `db = Database()` → `MainWindow(...)`；
`MainWindow.__init__` 创建 `AppContext` 后再创建页面，`_go(PAGE_TODAY)`。

插入点：`MainWindow.__init__` 里 **`AppContext` 创建之后、页面创建之前**调用

```python
self._context.prepare_startup()   # 种子 → 弃读结算 → 今日内容决策
```

`prepare_startup()` 内部：

1. `seed_real_texts(db, load_corpus())`（幂等）；
2. `expire_abandoned(today)`（served 超 7 天 → abandoned）；
3. `decide_today()`；命中则 `ArticleService.serve_real_text(...)`；
4. 任何异常只记日志，**绝不能阻塞启动**。

这样阅读页 `reader.py:174` 在无文章时自动生成 AI 文章之前，真实文章已经写好；
不会出现「先花 token 生成、再被真实文章替换」。

### 5.7 理由文案（示例）

- 供给：`你已经连续 12 天完成阅读，最近 3 次检测平均 82%，这篇约 B2、预计生词 5%。`
- 不供给不展示理由，避免打扰；只写日志 `real_text decision=skip reason=...`。

---

## 6. 阅读接入与本地练习

### 6.1 写入当天文章

`ArticleService.serve_real_text(text: RealText, *, today=None) -> Article`：

1. 取 `today`；若当天已有文章，抛错（自动供给不会走到这里）；
2. 用现有可读性入口算 `metrics`（与生成文章同一套）；
3. 构造 `ArticleDraft`（扩展 `source/source_url/source_ref` 字段）：
   - `level=text.level`、`topic=text.topics[0]`、`target_words=()`、`glossary=()`；
4. `replace_today(draft)`（复用 `article_service.py:554`，原子替换 + 级联清旧检测）；
5. `ReadingQuizService.seed_bundled_quiz(article, text)`；
6. `RealTextRepo.mark_served(text.id, article, today)`；
7. 日志 `real text served id=%s level=%s words=%s`。

**原子性**：文章写入、题目种子、历史记录任一步失败，都回滚当天文章并返回 `None`；
供给层不得留下「有文章没题目」的状态。回滚后阅读页按无文章处理，走 AI 生成。

### 6.2 题目来源（预置 / 按需生成）

**内置离线文章**：`ReadingQuizService.seed_bundled_quiz(article, text) -> ReadingQuiz`

- `get_by_article(article.id)` 命中则直接返回；
- 构造 `ReadingQuiz(article_id, article_date, level=article.level,
  questions=text.questions, explanations=每题的 explanation,
  practice_mode=True, stage=2, created_at=now_iso())`；
- `QuizRepo.insert()`；`DuplicateError` 时返回已有行；
- **不调用 AI**。

**在线抓取 / 手动 URL 文章**：`generate_practice_quiz(article, level) -> ReadingQuiz`

- 复用现有出题 prompt 的能力，只生成 3~4 道当前等级选择题；
- **只调用 1 次出题**：跳过第二轮、盲评、后验与题目审核；
- 同样以 `practice_mode=True, stage=2` 落库，解析写入 `explanations`；
- 失败不阻断阅读：可先读正文，稍后重试出题，或回退 AI 文章。

`reading_quiz_dialog.py:269` 的 `get_or_create` 命中缓存后直接展示，不会重新出题。

### 6.3 本地判分（提交阶段 0 AI）

在 `ReadingQuizService.submit()` 校验完答案、算出 `practice_mode` 后，加分支：

```python
if _is_real_text_practice(article, quiz):
    return self._submit_practice(article, quiz, normalized, profile, self_report)
```

> 内置文章与抓取文章都走这条本地判分分支；区别只在题目是「预置」还是
> 「出题时调了 1 次 AI」。**提交阶段不再调 AI。**

`_submit_practice()`：

1. 本地算 `choice_correct` / `score`；
2. `explanations` 取每题预置 `explanation`；
3. 用 `QuizRepo.save_result(...)` 落库：
   - `practice_mode=True`、`posterior` 为空、`recommended_level/result_level/previous_level`
     都等于 `profile.cefr_level`、`pending_*` 保持原值；
   - `feedback="真实文章练习：本地判分，不调整等级"`；
4. **不写 `level_evidence`、不改 profile、不跑盲评/后验**；
5. `RealTextRepo.record_completion(text_id, score_ratio, self_report)`（见第 7 节）；
6. 返回 `QuizOutcome(quiz=..., profile=profile, result=AssessmentResult(...),
   previous_level=profile.cefr_level, practice_mode=True)`。

`AssessmentResult` 按实际字段构造（等级不变、comment 标明练习）；不要伪造等级变化。

### 6.4 练习模式判定

`ReadingQuizService.is_practice_day()` 首行加：

```python
if (article.source or "") == "real":
    return True
```

保证即使今天没有正式检测，真实文章也走练习提示与不调级路径。

### 6.5 阅读页展示

`gui/pages/reader.py::_load_article`：

- `article.source == "real"` 时，meta 行追加 `真实文章 · 约 {level}（预估）`；
- 顶部细横幅：`为你安排的真实文章 · 预计生词 X%　[换回 AI 文章] [来源]`；
- `[换回 AI 文章]`：
  - 二次确认「今天这篇真实文章会被替换，之后不会再出现。」
  - 调现有「换一篇」生成流程；
  - 成功后 `RealTextRepo.mark_overridden(text_id)`；失败时保留真实文章。
- `[来源]` 打开 `gui/dialogs/content_sources.py`，展示标题、作者、来源、许可、原文链接。

### 6.6 检测弹窗文案

`gui/reading_quiz_dialog.py::_show_quiz`：

- `article.source == "real"` 时：
  - 标题改为 `真实文章练习 · {N} 道理解题`；
  - 说明改为 `本地判分、逐题讲评，不调整等级；读起来轻松/吃力会用来安排下一篇。`；
  - `_subtitle` 改为 `共 {N} 道理解题；本地判分，不调整等级。`
    （原逻辑会拼出「0 道产出题」「AI 会讲评」，对真实文章不适用）；
  - 不再显示「第 2 轮 / 第一轮答案已锁定」；
- 自评下拉沿用现有 `轻松 / 刚好 / 吃力`，tooltip 改为
  `用于安排下一篇真实文章，不参与等级调整`。

**预置题与现有弹窗的兼容点（已验证）**：种子题目用 `stage=2`；`_show_quiz` 的
`locked` 依赖 `_stage_one_answers`，首次打开时它为空，所以 4 道选择题仍可作答；
`_submit_answers` 看到 `quiz.stage >= 2` 直接走最终提交，不会触发第二轮出题。
不要改成 `stage=1`，否则会调用 `advance_stage` 触发 AI。

结果页沿用现有 `practice_mode` 文案（`reading_quiz_dialog.py:743` / `960`）。

---

## 7. 反馈闭环（`services/real_text_feedback.py`）

### 7.1 完成时记录

```python
@dataclass(frozen=True)
class RealTextOutcome:
    status: str        # completed
    score: float       # 0..1
    self_report: str   # easy/ok/hard
    outcome: str       # success/neutral/struggle
```

判定优先级：**struggle > success > neutral**。

| 条件 | outcome |
|---|---|
| `score < 0.5` 或 `self_report == "hard"` | `struggle` |
| `score >= 0.7` 且 `self_report in {"easy","ok"}` | `success` |
| 其余 | `neutral` |

写回 `real_text_history`：`status='completed'`、`score`、`self_report`、`outcome`、
`completed_at`、`updated_at`。

### 7.2 弃读结算

`expire_abandoned(today)`（启动时、供给前）：

- `status='served'` 且 `served_on <= today - 7 天` → `status='abandoned'`、
  `outcome='abandon'`；
- 不删行、不重新供给同一篇。

### 7.3 对下一次供给的影响

直接由 5.3 的间隔表消费；不在别处重复实现。

### 7.4 用户换回 AI

`mark_overridden(text_id)`：`status='overridden'`、`outcome='skip'`；
下一次按 7 天间隔，不降难度。

---

## 8. 阅读剂量（M7.3，0 schema）

- 口径：`articles.read_at IS NOT NULL` 的 `word_count` 之和；**包含真实文章**；
- UI 文案写「累计阅读（按打开文章计）」，不要写「读完」；
- `services/stats_overview.py` 增 `ReadingVolume`：
  `total_words / words_7d / words_30d / read_articles / est_books`（`total/90000`，1 位小数）；
- 统计页加数字块；**若新增图表，先走 dataviz skill，用 `theme.py` 的 `CHART_*` 成对颜色，不用 QtCharts**；
- 已知取舍：`replace_by_date` 会删旧文，先读生成文章再换真实文章会丢那次的 `read_at`，可接受。

---

## 9. 语境识别复习（M7.4）

### 9.1 纯函数 `services/context_review.py`

```python
@dataclass(frozen=True)
class ContextQuestion:
    masked_sentence: str
    options: tuple[str, ...]
    answer_index: int
    word_id: int

def build_context_question(word, distractor_pool, *, seed: int) -> ContextQuestion | None: ...
```

- 优先 `word.context_sentence`，其次 `word.example_en`；
- 用 `services/recall.py::mask_occurrences()` 挖空；
- 干扰项：同词性、相近词频档，排除出现在原句里的词；不足 3 个 → 返回 `None`；
- `random.Random(seed)` 保证可复现。

### 9.2 复习页规则（冻结）

- `repetitions == 0` → 优先语境识别；`>= 1` → 拼写；无例句/短语 → 回退「看词回忆」；
- 允许用户单卡切换；记录 `review_logs.mode`（`context` / `spelling` / `recall`）；
- 识别判分：选对建议 `KNOWN`，选错建议 `FORGOT`；**SRS 仍只看用户确认的 rating**。

### 9.3 测试

种子复现、干扰项不泄题（不出现在句中）、词池不足回退、短语回退、模式规则、0 AI。

---

## 10. 基准（M7.5，可选 P2）

- 同一批已入库文章 + 固定题目，每 8 周由用户主动重做一篇；
- `real_text_history` 已记录每次分数，统计页加「真实文章得分趋势」即可；
- 不新增 AI、不新增表；
- 建议 M7.0~M7.4 稳定运行 8 周后再做。

---

## 11. 文件改动清单

| 文件 | 新增/修改 | 职责 / 关键函数 |
|---|---|---|
| `assets/real_texts/corpus.json` | 新增 | 8~16 篇 + 每篇 4 题 + 许可元数据 |
| `assets/real_texts/sources.json` | 新增（M7.7） | 内置在线源列表（稳定 URL + 许可） |
| `assets/real_texts/NOTICE.md` | 新增 | 来源与许可 |
| `DailyEnglish.spec` | 无需修改 | 已整目录打包 `assets`；只需确认 `real_texts/` 在 assets 下 |
| `storage/db.py` | 修改 | `SCHEMA_VERSION=9`；三处 ALTER；两张新表 |
| `models/entities.py` | 修改 | `Article.source/source_url/source_ref`；`RealTextHistory` |
| `storage/repositories.py` | 修改 | `RealTextRepo`（seed/listing/mark_served/record/expire）；`ArticleRepo` 过滤 |
| `services/real_text_library.py` | 新增 | `RealTextCorpus/RealText`；加载校验；`seed_real_texts`；`pick_candidates` |
| `services/real_text_supply.py` | 新增 | 门槛、间隔、排序、`decide_today` |
| `services/real_text_feedback.py` | 新增 | `outcome_from_completion`、`expire_abandoned` |
| `services/article_fetcher.py` | 新增（M7.7） | HTTP 抓取、正文提取、校验、预览数据 |
| `services/source_catalog.py` | 新增（M7.7） | 内置源种子、24 小时退避、源选择 |
| `services/article_service.py` | 修改 | `ArticleDraft` 加 source 字段；`serve_real_text`；`replace_today` 透传 |
| `services/reading_quiz_service.py` | 修改 | `seed_bundled_quiz`；`is_practice_day` 真实文章短路；`submit` 分支 + `_submit_bundled_practice` |
| `services/level_progress.py` | 修改 | 只统计 `source='generated'` 的文章比例 |
| `services/config_store.py` | 修改 | `load_real_text` / `save_real_text` |
| `services/app_context.py` | 修改 | `prepare_startup()`；`real_text_*` 访问器 |
| `services/export_service.py` | 修改 | 两张新表进 JSON/CSV 与摘要 |
| `gui/pages/reader.py` | 修改 | 真实文章 meta / 横幅 / 换回 AI |
| `gui/pages/today.py` | 修改 | 真实文章标记 |
| `gui/reading_quiz_dialog.py` | 修改 | 真实文章标题/说明；本地判分分支已由 service 处理 |
| `gui/dialogs/content_sources.py` | 新增 | 来源与许可页 |
| `gui/dialogs/content_pack.py` | 新增 | 导入 `corpus.json`（M7.6） |
| `gui/dialogs/fetch_url.py` | 新增（M7.7） | 手动 URL / 粘贴正文 + 抓取预览 |
| `docs/english_learner/*` | 修改 | 本文件、AI_TASKS、PROGRESS、ARCHITECTURE、PRODUCT |
| `tests/*` | 新增 | 见第 12 节 |

---

## 12. 测试计划

### M7.0 内容库与存储

- corpus 校验 7 条；
- `seed_real_texts`：空库首次、同版本幂等、高版本覆盖、低版本不降级；
- v8→v9 迁移：三列存在、两张新表存在、旧数据 `source='generated'`；
- JSON/CSV 导出含新表；整库导出→导入后文章与历史都在。

### M7.1 自动供给与阅读

- `decide_today`：G0~G10 每个门槛一个用例；无候选；命中并按分数选最佳；
- 启动顺序：真实文章先于 AI 自动生成；异常不阻塞启动；
- `serve_real_text`：`source/source_ref` 正确、旧检测级联删除、题目预置；
- `seed_bundled_quiz`：不调 AI、stage=2、practice_mode=1；
- 本地判分：4 题全对/部分对/全错、分数、解析、`completed_at`；
- `is_practice_day` 对真实文章恒为 True；
- 无 AI 调用断言（用测试桩统计 `complete_json` 调用次数为 0）；
- offscreen 构造阅读页横幅与检测弹窗。

### M7.2 反馈闭环

- outcome 判定：struggle 优先、success 条件、neutral；
- 弃读：served 7 天→abandoned；未满 7 天不动；
- 间隔表：success 3 天、neutral 7 天、struggle 14 天、abandoned 10 天、skip 7 天；
- 降难度：struggle/abandoned 后候选等级为当前-1；
- 换回 AI：status=overridden、outcome=skip、下次间隔 7 天。

### M7.3 阅读剂量

- 空数据、只有未读、含真实文章、周窗口边界、`est_books` 换算。

### M7.4 语境复习

- 见 9.3；外加 `review_logs.mode` 落库与导出。

### M7.6 内容包导入

- 合法包导入成功、`corpus_version` 升级生效；
- 低版本不降级；
- 非法许可 / 结构 / 缺字段拒绝，原库不变；
- 导入后 `decide_today` 能选到新文章。

### M7.7 在线内容源与手动 URL

- 纯文本 / HTML / 重定向 / 超时 / 403 / 正文太少 / 非英文 / 超大响应；
- `file://`、localhost、私有 IP 被拒；
- 预览确认后才入库；失败不覆盖缓存；
- 同一 URL 24 小时退避；`failure_count` 累加；
- 手动 URL 成功 / 失败；粘贴正文成功；
- 抓取文章题目 1 次 AI、练习模式、不改等级；
- 断网时只用缓存，AI 文章照常。

### 离线与降级（必测）

- `corpus.json` 缺失 / 损坏 → 启动不抛异常，AI 文章照常；
- `real_texts` 为空 → `decide_today` 返回 `no_library`，AI 文章照常；
- 种子抛错 → 启动继续，真实文章供给关闭；
- `serve_real_text` 在题目种子阶段抛错 → 当天文章被删除，阅读页回退 AI；
- 真实文章路径 **0 网络请求**：用会抛错的 `AIClient` / `httpx` 桩，断言
  「打开 → 阅读 → 提交 → 结果」全程不触发；
- 断网模拟：正文、预置题、本地判分可用；查词失败走现有提示。

### 全局

- `uv run pytest -q` 全绿；`ruff check` / `ruff format --check` 通过；
- `services/` / `storage/` 不 import PySide6；
- 不新增 AI 调用类型；AI 元数据仍只由 `AIClient` 写 `ai_calls`。

---

## 13. 里程碑与排期

| 期 | 内容 | 估时 | 依赖 | 状态 |
|---|---|---|---|---|
| **M7.0** | 内容库 + SQLite 存储 + 种子 + 导出同步 | 1~1.5 天 | — | ✅ 基础设施 + 2 篇起步（A2/C1 待补） |
| **M7.1** | 自动供给 + 阅读接入 + 本地练习 + 换回 AI | 1.5~2 天 | M7.0 | ✅ 2026-11-25 |
| **M7.2** | 反馈闭环 + 弃读结算 + 间隔调整 | 0.5~1 天 | M7.1 | ✅ 2026-11-25 |
| M7.3 | 阅读剂量（累计词数） | 0.5 天 | — | ✅ 2026-11-25 |
| M7.4 | 语境识别复习 | 1.5 天 | M7.0（v9 的 mode 列） | ✅ 2026-11-25 |
| M7.5 | 真实文章基准趋势 | 0.5 天 | M7.0~M7.2 | ⬜（可选） |
| M7.6 | 内容包手动导入（无外网环境） | 0.5 天 | M7.0 | ⬜（可选，网络受限时优先） |
| M7.7 | 在线内容源 + 手动 URL / 粘贴（离线优先、在线补充） | 2~3 天 | M7.0、M7.1 | ⬜（可选，推荐） |

**顺序理由**：先有内容和表，再有供给；供给必须能落到阅读页并本地判分，反馈才有数据；
反馈闭环稳定后再做剂量与语境复习；基准最后做，避免过早优化展示。

**内容工作量**：8~16 篇 ×（选文 + 规范化 + 4 题 + 人工过题）是主要成本，AI 起草、
用户审题；代码量不大，别把估时都算在写代码上。

**M7.1 ~ M7.4 交付（2026-11-25）**：`real_text_supply.py`（G0~G10 门槛、
间隔表、候选打分、确定性打散）、`real_text_feedback.py`（struggle > success >
neutral）、`ArticleService.serve_real_text()`（文章 + 预置题 + 历史原子写入，
失败回滚）、`ReadingQuizService.seed_bundled_quiz()` / `_submit_real_practice()`
（0 AI 本地判分，不写等级证据）、阅读页真实文章横幅 / 来源弹窗 / 换回 AI、
设置页自动安排开关、`stats_overview.ReadingVolume` 与统计页阅读剂量卡、
`context_review.py` 语境识别题 + 复习页单卡切换 + `review_logs.mode` 落库与导出。
全量 **748 passed**，ruff 通过。

**M7.0 交付（2026-11-25）**：schema v9、`RealTextRepo` / `ContentSourceRepo`、
`real_text_library.py`（加载 / 校验 / 种子）、导出同步、`AppContext.prepare_startup()`
与 `MainWindow` 启动调用已落地；全量 **694 passed**，ruff 通过。
`corpus.json` 已入库 2 篇 China Daily（B1 277 词 / B2 445 词，`Personal use`），
结构校验通过。当前环境 Gutenberg 不可达（curl 000 / WebFetch 503），先用国内
可访问源起步；A2 / C1 仍缺，后续补到每档 2 篇。

---

## 14. 明确不做

| 不做 | 原因 |
|---|---|
| 把联网抓取作为唯一 / 主来源 | 首次启动 / 断网 / 被墙就没有真实文章；内置缓存必须保底 |
| 无上限后台抓取 / 绕过付费墙 | 违反站点条款、浪费流量；只做有节制的补充 |
| 运行时让 AI 生成真实文章题目 | 破坏离线与基准效度；题目应在构建期人工复核 |
| 把真实文章计入等级证据 | 文章难度是预标注的，会污染 M6 等级闭环 |
| 两台机器合并进度 | 现有设计是整库覆盖；合并需要服务端/CRDT |
| 把正文放进对外分享的导出包 | 版权；个人备份可以 |
| 首屏堆「真实文章推荐」卡片 | 自动供给已经够，别再加装饰 |
| 为供给新增 AI 评估调用 | 现有数据足够；违反成本约束 |
| 运行时自动下载 / 更新内容包 | 断网 / 被墙时必然失败；改为手动导入 |

---

## 15. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 内容难度标错 | 运行时按用户生词率 3%~7% 门控；用户可换回 AI |
| 自动供给打扰 | 每天最多一篇；`auto_enabled` 可关；横幅可换回 |
| 种子覆盖用户改动 | `corpus_version` 只升不降；文档写明 |
| 导入旧库后文章库变旧 | 下次启动种子按版本自愈 |
| 真实文章污染等级进度 | 练习模式 + `source='generated'` 过滤 + 单测 |
| 读库/写库失败阻塞启动 | `prepare_startup` 全捕获，只记日志 |
| Windows 文件占用 | 沿用现有 `database_transfer` 的重试与暂存机制，不另写 |

回滚：每个里程碑独立可回滚；关闭 `real_text.auto_enabled` 即可停止供给，
已写入的 `articles` 行保留，阅读不受影响。

---

## 16. 每期固定收尾

- `uv run pytest -q` 全绿（只增不减）；`ruff check` + `ruff format --check` 通过；
- schema 改动补旧库升级测试；`export_service` 同步；
- `services/` / `storage/` 不 import PySide6；AI 调用全走 `TaskWorker` 且带 `task` 标签；
- 更新 `PROGRESS.md` 看板（只放状态）；交付细节追加 `HISTORY.md`；
- `ARCHITECTURE.md` 补文件地图与「真实文章为何不计等级」；`PRODUCT.md` 记 D1~D8；
- 不执行耗时打包/构建，除非用户明确要求。

---

## 17. 给下一个 AI 的开工指令

> 读本文件与 `AI_TASKS.md` 第 11 节，从 **M7.0** 开始；一次只做一个里程碑。
> 先做内容库、表、种子与校验，跑测试；完成并汇报后停下等用户说「继续」。
> 先做 **M7.0~M7.2 的离线保底**；M7.7 在线源是可选补充，不是前置条件。
> 运行时抓取失败必须回退「缓存 → 手动 URL/粘贴 → AI 文章」，**不能阻塞启动**；
> 内置 URL 抓不到就用用户粘贴的正文，不要卡在抓取上。
> 任何与代码现状不符的地方先停下说明，不要静默改计划。
