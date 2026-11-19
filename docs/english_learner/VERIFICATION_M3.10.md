# M3.10 生词本回显复习求助 · 外部验收记录

- 验收日期：2026-11-19
- 验收对象：M3.10 生词本详情回显复习求助记录（用户输入 + AI 帮记）
- 验收方式：独立 Claude Code CLI 进程 + 外部大模型，只读审查（本轮环境限制见下）
- 验收模型：`GLM-5`
- 网关：`http://121.43.115.21:9785`（Key 由本机 `~/.claude/external-verification.json` 提供）
- 验收 session：`369c47d6-2727-43c0-b886-e54275fd9361`
  - 第 1 轮 `--session-id` 新建；第 2 轮 `--resume` **复用同一 session**
- 鉴权：通过临时 `--settings` 注入，**API Key 不写入仓库、不写入本文档**

> 本轮起外部验收脚本支持 session 复用：
> 第 1 轮固定 `--session-id <UUID>`，后续轮次统一 `--resume <UUID>`，
> 验收模型可保留上一轮的结论、问题清单与工具执行历史。
> 原始 JSONL 与 `.session` 文件只放临时目录，不入库。

---

## 第 1 轮验收（2026-11-19）

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | 外部进程被环境审批拦截，**未独立执行** |
| `uv run ruff check` / `format --check` | 外部进程被环境审批拦截，**未独立执行** |
| `modelUsage` | CLI 未输出 `result` 事件，未能解析；实际请求模型为配置的 `GLM-5` |

主会话自检（外部验收前）：`218 passed`（修复后），ruff 全部通过。

### 发现的问题

第 1 轮静态审查结论：13 项功能验收点全部通过，未发现 P0/P1/P2；
提出 2 个 P3：

#### P3-1：文档状态未同步到 M3.10

- **位置**：`README.md`、`docs/english_learner/PRODUCT.md`、`CLAUDE.md`
- **现象**：仍写 M3.9，功能表/里程碑表没有 M3.10。
- **修复**：README 当前进度与功能表、PRODUCT 里程碑表、CLAUDE 当前状态
  全部补上 M3.10 与“求助记录回显”。
- **状态**：✅ 已修复

#### P3-2：超过 50 条复习记录时省略计数可能偏小

- **位置**：`gui/pages/vocabulary.py` 原 `_help_logs()`（先取 50 条再内存过滤）
- **现象**：若最近 50 条都是「认识 / 太简单」，更早的求助记录会被挤出窗口；
  “更早的 N 次”也会低估。
- **修复**：`storage/repositories.py` 新增 `ReviewRepo.list_help_for_word()` 与
  `count_help_for_word()`，在 SQL 中用
  `TRIM(note) <> '' OR TRIM(advice) <> ''` 过滤后直接 `LIMIT 5`，
  省略计数使用完整 `COUNT`；页面改为调用新方法。
- **状态**：✅ 已修复

### 补充的回归测试

1. `test_detail_help_record_with_only_note_or_advice`：单边记录只显示存在的一行；
2. `test_help_records_not_missed_behind_many_plain_reviews`：60 条普通评分
   不能把更早的 6 条求助挤出窗口；
3. `test_review_repo_help_logs_filter_order_and_limit`：仓库层过滤、倒序、
   并列时间戳 `id DESC`、`limit` 边界与空结果。

### 修复后自检

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | **218 passed** |
| `uv run ruff check src/english_learner tests` | All checks passed |
| `uv run ruff format --check src/english_learner tests` | 68 files already formatted |

---

## 第 2 轮验收（2026-11-19，`--resume` 同一 session）

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | 外部进程尝试 8 种执行方式，均被权限策略拒绝，**未独立执行** |
| `uv run ruff check` / `format --check` | 同上，**未独立执行** |
| `modelUsage` | CLI 未输出 `result` 事件；实际请求模型为配置的 `GLM-5` |

### 发现的问题

- **未发现 P0/P1/P2**；
- P3-1、P3-2 均确认已修复；
- 结论为“有条件通过”：功能与测试资产静态审查全部达标，
  但外部进程无法实际复跑测试，需在有执行权限的环境确认基线。

### 观察项（非缺陷）

1. 仓库层 `list_help_for_word / count_help_for_word` 缺少直接单测
   → 已补 `test_review_repo_help_logs_filter_order_and_limit`；
2. SQLite `TRIM` 只去空格，纯 `\t` 的 note 理论上可渲染空行；
   应用内写入经 Python `strip()`，实际不可达；
3. 建议后续验收前 `pytest --cache-clear`，避免陈旧 `lastfailed` 干扰取证；
4. 360px 面板长建议换行滚动、未完成评分不落库的真实桌面体验仍需人工确认。

---

## 结论

- 两轮外部验收均在同一 session 内完成，第 2 轮能看到第 1 轮的问题与修复；
- 未确认的 P0/P1/P2 为 0；
- 主会话独立复跑：**218 passed**，ruff check / format 通过；
- 外部进程未能实际执行测试，属于验收环境权限限制，不代表测试失败；
  该限制已在两轮报告中如实披露。

### 仍需人工确认

1. 真实 Windows 桌面下，右侧 360px 详情面板中长 AI 建议的换行与滚动；
2. 用户生成 AI 建议后直接关窗取消评分时，该条建议不落库（符合现有评分语义）。
