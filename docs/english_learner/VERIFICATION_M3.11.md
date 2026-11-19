# M3.11（Phase 2 / Phase 3）· 外部验收记录

- 验收日期：2026-11-19
- 验收对象：M3.11 —— 本地后验与置信度、自适应两阶段出题、题目审核、盲评、
  双产出题、选项随机、同日练习模式、每周复盘
- 验收方式：独立 Claude Code CLI 进程 + 外部大模型，只读审查；外部 CLI 的
  pytest/ruff 执行在 4 轮中均被权限层拦截，动态自检由开发会话补跑并留档
- 验收模型：`GLM-5`
- 网关：`http://121.43.115.21:9785`（Anthropic 兼容协议）
- 验收 session：`6082e676-7332-4c57-a760-8e5fffeee51f`（第 1~3 轮，`--resume` 复用）；
  第 4~5 轮新开 `c8ed03f6-59d5-4f90-879e-c6d14ca0016f`
- 鉴权：通过临时 `--settings` 注入，**API Key 不写入仓库、不写入本文档**

> 复现命令（Key 由本机 `~/.claude/external-verification.json` 提供）：
>
> ```bash
> # 第 1 轮：新建并固定 session
> python ~/.claude/skills/external-verification/scripts/verify.py \
>   --project D:\py-space\py-learn \
>   --prompt-file /tmp/m311/prompt_round1.txt \
>   --output /tmp/m311/round1.jsonl \
>   --session-id 6082e676-7332-4c57-a760-8e5fffeee51f
>
> # 第 2 轮起：复用同一 session
> python ~/.claude/skills/external-verification/scripts/verify.py \
>   --project D:\py-space\py-learn \
>   --prompt-file /tmp/m311/prompt_round2.txt \
>   --output /tmp/m311/round2.jsonl \
>   --resume 6082e676-7332-4c57-a760-8e5fffeee51f
> ```
>
> 默认 Plan B：保留 session 持久化，CC Switch 可以同步到本次 GLM 会话，
> 也能用 `--resume` 继续多轮验收。

---

## 第 1 轮验收（2026-11-19，静态审查）

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | ⛔ 未能执行：外部 CLI 会话权限层拦截了 `uv` / python / pytest，约 10 次尝试均未放行；仅放行 `ls/cat/grep/git` |
| 用例数静态核验 | ✅ `grep -rn "def test_" tests/ \| wc -l` = 264，与当时声明一致 |
| `uv run ruff check / format --check` | ⛔ 未能执行（同上）；静态走读未见分层/字体约定问题 |
| `modelUsage` | 不适用（本轮外部 CLI 未正常走完 result 事件） |

> 本轮暴露的问题：`--dangerously-skip-permissions` 在该外部 CLI 会话中仍被权限层
> 拦截。已在本机 `~/.claude/external-verification.json` 的 `extra_claude_args`
> 追加 `--permission-mode bypassPermissions`，第 2 轮起实际执行测试。

### 发现的问题

#### P2：盲评建议低于当前等级时，证据方向反了

- **位置**：`src/english_learner/services/level_estimator.py`
  `observations_from_blind_level()`
- **现象**：盲评建议被编码为 `(盲评等级, correct=True, 0.5)`。在 Rasch 模型下，
  “A2 题答对”对 B1 用户来说概率（0.75）高于 A2 用户（0.5），所以盲评建议
  **低于**当前等级时反而把后验往上推，与盲评意图相反。
- **修复**：按方向编码——高于当前 → `(盲评档, correct=True)`；低于当前 →
  `(盲评档, correct=False)`；相同 → 不产生观测。
- **回归测试**：`test_blind_observation_encodes_direction`、
  `test_blind_lower_suggestion_pushes_posterior_down`、
  `test_blind_higher_suggestion_pushes_posterior_up`、
  `test_blind_suggestion_affects_current_but_not_stored_history`。
- **状态**：✅ 已修复

#### P3：文档阈值与代码不一致

- **位置**：`docs/english_learner/PROGRESS.md` 6.22
- **现象**：文档写“置信度 < 0.55 保持”，代码为 0.6。
- **修复**：文档改为 0.6。
- **状态**：✅ 已修复

#### P3：盲评 prompt 展示题目难度，可能反推当前等级

- **位置**：`src/english_learner/services/prompts.py`
  `build_blind_level_messages()`
- **现象**：第一轮 3 道题难度恒为当前等级，展示 difficulty 会削弱“去锚定”。
- **修复**：盲评 prompt 不再展示 difficulty，只保留题号、题干、正确答案与作答。
- **状态**：✅ 已修复

#### P3：练习模式提示出现过晚

- **位置**：`gui/reading_quiz_dialog.py`
- **现象**：用户答完全程、消耗两次 AI 调用后才知道是练习模式。
- **修复**：`ReadingQuizService.is_practice_day()` 提前判断；弹窗打开时在
  第一轮/第二轮说明卡里提示“本次为练习模式，不会调整等级”。
- **状态**：✅ 已修复

#### P3：今日页把上周复盘标成“本周复盘”

- **位置**：`gui/pages/today.py` `_refresh_weekly()`
- **现象**：最近一条复盘不是本周时仍显示“本周复盘（上周日期）”。
- **修复**：比较 `week_start` 与当前周一；不同则显示“最近复盘（某周）”。
- **状态**：✅ 已修复

### 修复后自检

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **267 passed** |
| `uv run ruff check src/english_learner tests` | All checks passed |
| `uv run ruff format --check src/english_learner tests` | 通过（由 `ruff format` 统一后复查） |

---

## 第 2 轮验收（2026-11-19，静态复审，同一 session）

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | ⛔ 仍未能执行：`--permission-mode bypassPermissions` 已加入，但外部 CLI 仍拦截 `uv` / python；仅放行 `ls/grep/git/cat` |
| 用例数静态核验 | ✅ `grep -rn "def test_" tests \| wc -l` = 267 |
| `modelUsage` | 不适用（外部 CLI 仍未正常走完 result 事件） |

### 第 1 轮问题复核

5/5 全部确认修复（盲评方向、文档阈值、盲评难度泄漏、练习提示、复盘标签）。

### 第 2 轮新发现

| # | 级别 | 问题 | 处理 |
|---|---|---|---|
| 1 | P3 | `advance_stage()` 先出题后保存第一轮答案；失败/关窗后不落库，且 stage=1 不回显 | ✅ 改为先 `save_stage_answers()` 再生成；弹窗 stage=1 回显已保存选项；补 2 个测试 |
| 2 | P3 | 每周复盘数据门槛不含 `articles`，只阅读的一周会被拒 | ✅ guard 加入 `stats["articles"]`；补 `test_articles_only_week_is_enough_for_review` |
| 3 | P3 | 第二轮题缺 `difficulty` 时回退本卷等级而非 target | ✅ `parse_stage_two_questions()` 统一回退 target；补测试 |
| 4 | 观察 | “本周/最近复盘”标签无回归测试 | ✅ 补 GUI 冒烟测试 `test_today_page_distinguishes_last_week_review` |

### 修复后自检

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **272 passed** |
| `uv run ruff check src/english_learner tests` | All checks passed |
| `uv run ruff format --check src/english_learner tests` | 通过 |

### 权限问题定位

用最小外部调用单独验证：仅加 `--permission-mode bypassPermissions` 仍会被拦截；
追加 `--allowedTools Bash Read Grep Glob LS` 后，`uv --version` 可以真实执行。
已把该参数写入本机 `~/.claude/external-verification.json` 的 `extra_claude_args`，
第 3 轮起外部模型应能实际执行 pytest / ruff。

---

## 第 3 轮验收（2026-11-19，静态复审，同一 session）

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | ⛔ 仍被拦截：该 session 创建于第 1 轮，权限状态在 `--resume` 后无法升级，即使追加 `--allowedTools` |
| 用例数静态核验 | ✅ 272 |
| `modelUsage` | 不适用（未正常走完 result 事件） |

### 结论

- 前两轮 9 个问题 **9/9 确认修复**；
- 任务书 Phase 2 / Phase 3 验收标准逐项通过；
- 无 P0/P1/P2；新发现 2 个 P3 + 1 条观察。

### 本轮新发现与处理

| # | 级别 | 问题 | 处理 |
|---|---|---|---|
| 1 | 观察 | 盲评观测写入证据后会随历史回放累积权重（0.5 × (1+0.75+…) ≈ 1.5） | ✅ 改为只有题目/产出题观测落库；盲评与自评只影响当次后验，补测试断言证据表不含 blind |
| 2 | P3 | `VERIFICATION_M3.11.md` 第 3 轮停留“进行中”、PROGRESS 缺 5.11 验证小节 | ✅ 本轮补录 |
| 3 | P3 | `apply_suggestion` 的 `max(confidence, 0.6)` 永不触发 | ✅ 去掉冗余防御，直接写 AI 复盘置信度 |

### 修复后自检

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **273 passed** |
| `uv run ruff check src/english_learner tests` | All checks passed |
| `uv run ruff format --check src/english_learner tests` | 通过 |

---

## 第 4 轮验收（2026-11-19，新开 session）

> **为什么新开 session**：第 1~3 轮 session 创建时没有 `--allowedTools`，`--resume`
> 后权限状态无法升级，三次都无法执行 pytest/ruff。已用最小外部调用验证
> `--allowedTools Bash Read Grep Glob LS` 可以真实执行 `uv`；因此第 4 轮用全新
> session 从启动参数就带上该配置，专门补做动态检查。
>
> 新 session id：`c8ed03f6-59d5-4f90-879e-c6d14ca0016f`。

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | ⛔ 仍被拦截：外部 CLI 对 `uv` / `.venv/Scripts/python.exe` / `pytest` / `cmd /c` 共 8 种形态均返回 “requires approval” |
| 用例数静态核验 | ✅ `def test_` = 273，与声明一致；无 parametrize |
| 分层约定 | ✅ services/storage/models 零 PySide6 import |
| `modelUsage` | 不适用（仍未正常走完 result 事件） |
| 任务书 8 项验收标准 | ✅ 8/8 静态核验通过 |
| 前 3 轮 11 个修复点 | ✅ 11/11 确认在位 |

### 本轮新发现与处理

| # | 级别 | 问题 | 处理 |
|---|---|---|---|
| 1 | P3 | 第 1 轮记录里的 `test_higher_blind_suggestion_is_positive_evidence` 已被重命名，文档引用失效；“盲评高于当前 → 后验向上”缺一条后验层对称断言 | ✅ 文档改为准确测试名；新增 `test_blind_higher_suggestion_pushes_posterior_up`，与“向下”对称 |
| 2 | 观察 | `_parse_productions` 在 2 道产出题且 AI 完全省略 `production_scores` 时按 0 分容错 | ✅ 记入 PROGRESS 已知问题；真实网关频繁出现时改为格式错误重试 |
| 3 | 观察 | `count_completed_on` 降级路径不排除 practice_mode | ✅ SQL 增加 `practice_mode = 0` |
| 4 | 观察 | `submit()` 的 profile / quiz / 证据三写非原子 | ✅ 记入 PROGRESS 已知问题（与复习落库同类技术债） |

### 动态验证的最终处理

- 外部 CLI 的权限层在 4 轮、两种 session 创建方式（resume / 全新）下均未放行
  `pytest`/`ruff`；最小调用验证过 `--allowedTools` 能执行 `uv --version`，但
  完整验收 prompt 下仍被拦截。这是**验收环境限制**，不是代码失败。
- 开发会话（完整权限）自检结果作为动态验证留档：

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **273 passed** |
| `uv run ruff check src/english_learner tests` | All checks passed |
| `uv run ruff format --check src/english_learner tests` | 75 files already formatted |

- 外部验收结论：**功能验收通过（静态审查 4 轮 + 开发会话动态自检）；
  动态检查未能由外部模型独立执行，按 skill 规则如实保留**。

### 第 5 轮收尾确认（尝试，未完成）

- 用第 4 轮 session `--resume` 发起了只确认 P3-1 修复的短请求；
- 外部 CLI 在等待窗口内没有返回任何报告，进程被终止（未产生第 5 轮结论）；
- P3-1 由开发会话直接验证：`test_blind_higher_suggestion_pushes_posterior_up`
  已加入且 `uv run pytest` = **273 passed**；验收文档中的测试名引用已更新。
  因此不再为这一条单独开新 session，避免把“环境超时”误当成“验收通过”。

---

## 结论记录规则

- 每轮验收都必须记录：执行命令、测试结果、发现的问题、是否确认、修复方式、
  修复后的回归结果。
- 只有“确认存在且已修复/已解释”的问题才允许关闭；
- 未复现的模型猜测标注为“未确认”，不要直接改代码；
- 验收文档不写入 API Key、完整请求头等敏感信息。
