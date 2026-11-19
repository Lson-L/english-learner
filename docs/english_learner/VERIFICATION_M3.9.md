# M3.9 阅读检测第一阶段 · 外部验收记录

- 验收日期：2026-11-19
- 验收对象：阅读检测第一阶段（混合难度、英文短答、错因归因、连续证据调级）
- 验收方式：**独立 Claude Code CLI 进程 + 外部大模型**，只读审查 + 实际执行测试
- 验收模型：`GLM-5`
- 网关：`http://121.43.115.21:9785`（Anthropic 兼容协议）
- 鉴权：通过临时 `--settings` JSON 注入，**API Key 不写入仓库、不写入本文档**

> 复现命令（Key 用环境或临时 settings 提供）：
>
> ```bash
> claude --bare --settings /tmp/glm_settings.json \
>   -p "$(cat /tmp/verify_prompt_m39.txt)" \
>   --model GLM-5 --dangerously-skip-permissions \
>   --output-format text --no-session-persistence
> ```
>
> 注意：用户级 `~/.claude/settings.json` 里有 `ANTHROPIC_BASE_URL=https://opencode.ai/...`，
> 会覆盖进程环境变量；验收时必须用 `--settings` 显式指向验收网关，否则会被路由到
> OpenCode Go 并报 `Model GLM-5 isn't included in OpenCode Go`。

---

## 第 1 轮验收（2026-11-19）

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | 209 passed（当轮基线） |
| `uv run ruff check src/english_learner tests` | All checks passed |
| `uv run ruff format --check src/english_learner tests` | 68 files already formatted |

### 发现的问题

#### P1：证据评分“答错”分支的难度权重方向反了

- **位置**：`src/english_learner/services/reading_quiz_service.py:308`（选择题）、
  `:317`（短答题）
- **现象**：代码为
  `item_points = (1.0 + 0.5 * delta) if correct else (-1.0 - 0.5 * delta)`。
  答对时高难度加分更多（正确）；但答错时高难度反而扣得更多、低难度扣得更少，
  与 docstring 和需求“答错低难度扣分更多”相反。
- **影响**：
  - 强学习者错两道高难度探顶题时，本地证据被算成“中性”，拿不到向上信号；
  - 只答对低难度题、其余全错时，本地证据被算成强负值，可能压过 AI 立即降级；
  - 直接改变 `decide_recommended_level` / `apply_level_policy` 的结果。
- **复现**：B1 用户、标准混合卷 A2/B1/B1/B2/C1 + 短答 B1：
  - 只错低一档 A2：该题 `-0.5`（应 `-1.5`）；
  - 只错高两档 C1：该题 `-2.0`（应 `0.0`）。
- **根因**：错误分支的符号写反，应为 `(-1.0 + 0.5 * delta)`。
- **修复**：
  - 两处改为 `(-1.0 + 0.5 * delta)`；
  - 新增回归测试 `test_single_wrong_question_weights_by_difficulty`，
    分别断言只错低一档 `-1.5`、同档 `-1.0`、高两档 `0.0`，并断言单调关系。
- **状态**：✅ 已修复

#### P2：PROGRESS.md 引用的验收文件不存在

- **位置**：`docs/english_learner/PROGRESS.md` 三处引用
  `VERIFICATION_M3.9.md`
- **现象**：仓库中没有该文件。
- **修复**：创建本文件，并保留 PROGRESS.md 的引用。
- **状态**：✅ 已修复

#### P2：db.py 模块注释的 schema 版本过期

- **位置**：`src/english_learner/storage/db.py:14`
- **现象**：注释写“当前 SCHEMA_VERSION = 3”，实际为 5。
- **修复**：注释更新为 5。
- **状态**：✅ 已修复

### 验收中额外发现并修复的问题

#### 错题被 AI 回填 `error_type=none` 时没有归为 unknown

- **位置**：`src/english_learner/services/reading_quiz_service.py`
  `_parse_error_types()`
- **现象**：AI 对一道答错的题返回 `"none"` 时，结果页不会显示错因，
  等于“错了但不知道为什么”。答对题强制 `none` 是对的，但错题应归为 `unknown`。
- **修复**：答错且值为 `""` 或 `"none"` 时统一记为 `unknown`；
  新增 `test_error_types_missing_entries_fall_back_by_correctness`。
- **状态**：✅ 已修复

### 补充的回归测试

1. `test_single_wrong_question_weights_by_difficulty`：单题错误权重的隔离断言，
   防止 P1 回归。
2. `test_error_types_missing_entries_fall_back_by_correctness`：AI 少给 / 给 none
   时的错因兜底。
3. `test_migrate_v4_database_adds_m39_columns`：真实构造 v4 库（旧 profile /
   articles / reading_quizzes + 一条已完成旧检测），验证 v4→v5 原地加列、
   旧题目与结果不丢、新字段默认值正确。

### 修复后自检

| 检查 | 结果 |
|---|---|
| `uv run pytest` | **212 passed** |
| `uv run ruff check` | All checks passed |
| `uv run ruff format --check` | 68 files already formatted |

---

## 第 2 轮验收（2026-11-19）

### 执行结果

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | **212 passed**（无 fail/error/skip） |
| `uv run ruff check src/english_learner tests` | All checks passed |
| `uv run ruff format --check src/english_learner tests` | 68 files already formatted |

### 结论

**通过。** 第一轮发现的 4 项问题全部确认已正确修复，未发现新引入的明显 bug、
数据不一致、迁移失败、UI 流程卡死、等级策略错误或解析错误。

### 逐项复查

1. **P1 权重方向**：两处均为 `(-1.0 + 0.5 * delta)`；真实代码实测
   B1 用户、A2/B1/B2/C1/B1 + 短答 B1：
   - 只错 A2 → `details[0] = -1.5`
   - 只错 B1 → `details[1] = -1.0`
   - 只错 C1 → `details[3] = 0.0`
   - 答对方向 A2=+0.5 / B1=+1.0 / B2=+1.5 / C1=+2.0 单调递增；
   - 回归测试 `test_single_wrong_question_weights_by_difficulty` 通过。
2. **P2 验收文档**：本文件存在，PROGRESS.md 三处引用有效。
3. **P2 db.py 注释**：`db.py:14` 已为 `SCHEMA_VERSION = 5`，与常量一致。
4. **error_type=none 兜底**：答错且值为 `""` / `"none"` / 非法值时统一
   `unknown`；答对强制 `none`；新增测试通过。
5. **v4→v5 迁移**：`test_migrate_v4_database_adds_m39_columns` 通过；
   额外手工验证“只出题未作答”的 v4 记录迁移后 `completed=False`、
   `total=5`、`evidence_ratio=0`，可正常加载。

### 额外抽查（均通过）

- 证据阈值：全错 `ratio=-0.833/down`；自评“轻松 vs 吃力”差值 0.5；
  up/keep/down 分界与注释一致。
- 等级策略：最多一档、强证据（`|ratio| >= 0.6`）压过 AI、C1 上限、
  A2 下限、两次向上才升级、降级立即生效、pending 清空与落库回读。
- 解析容错：A1→A2、非法难度回退、`answer:"B"` / `correct_index` /
  竖线分隔选项、两道短答取第一道、explanations 截断/补空、短答反馈回填。
- 提交流程：本地判分、空短答强制 0、讲评失败不改画像可重试、
  已完成检测拒绝重交、并发插入 DuplicateError 回读。
- GUI：缓存命中跳过 AI、已完成直接进结果页、未答完/短答为空阻止提交、
  进行中拒绝关闭、worker 结束恢复按钮、旧 v4 记录不崩溃。
- 存储：schema 幂等、`save_result` 只更新结果列、文章替换级联删 quiz、
  新旧字段还原。

### 仍未覆盖的测试盲区（建议，非 bug）

1. 非常规难度分布（AI 全出同档或全出高两档）下的证据阈值行为；
2. `get_or_create` 并发插入 `DuplicateError` 回读分支；
3. `submit()` 画像与 quiz 两次写入的中间态；
4. 弹窗 `_on_quiz_failed` / `_on_grading_failed` 回调链的 GUI 单测；
5. 真实 AI 网关的出题/讲评格式兼容回归；
6. v4 “只出题未作答”记录迁移的固化测试；
7. A2 用户 `_quiz_difficulty_plan` 夹逼分支；
8. 非法 `self_report` 字符串路径。

> 上述均为“测试盲区 / 建议”，不是已确认缺陷；后续 Phase 2/3 或下一次
> 迭代可以逐步补。第二轮为纯只读复验，未修改文件、未提交 git。

---

## 结论记录规则

- 每轮验收都必须记录：执行命令、测试结果、发现的问题、是否确认、修复方式、
  修复后的回归结果。
- 只有“确认存在且已修复/已解释”的问题才允许关闭；
- 未复现的模型猜测标注为“未确认”，不要直接改代码；
- 验收文档不写入 API Key、完整请求头等敏感信息。
