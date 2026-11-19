# Daily English 开发进度（当前看板）

- 最后更新：2026-11-19
- 当前阶段：**M3.15 完成**（AI 调用记录与日志查看）；M0 ~ M3.14 全部完成
- 当前基线：**314 passed**（`uv run pytest`）；SQLite schema **v7**
- 产品方向：[PRODUCT.md](PRODUCT.md)

> **文档怎么读（重要，避免污染上下文）**
> - 本文件是每个会话唯一必读文档，控制在约 150 行；
> - 改 AI 客户端 / 存储 / 阅读检测 / 导出等模块前，定向读
>   [ARCHITECTURE.md](ARCHITECTURE.md) 的对应小节；
> - 历史交付、旧任务书与验证记录在 [HISTORY.md](HISTORY.md)，
>   只在排查“以前为什么这么改”时检索，**不要默认加载**；
> - 重构 / 修 bug / 规划 M4 前扫一遍 [TECH_DEBT.md](TECH_DEBT.md)；
> - 规划下一轮功能（AI 该用在哪、优先级、成本）读 [AI_STRATEGY.md](AI_STRATEGY.md)；
> - 完成里程碑：更新本文件看板，并在 HISTORY.md 追加交付记录，
>   **不要再把细节堆回本文件**。

## 1. 里程碑

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
| M3.12 | AI 调用层稳定性（连接复用/重试/额度/JSON 能力）、难度与答案键可信度、漏评分保护、调级 clamp、生成端闭环、查词语境重查 | ✅ 2026-11-19 |
| M3.13 | 兼容 opencode 网关（自动附加 `x-opencode-session`），404 提示补充协议不匹配说明 | ✅ 2026-11-19 |
| M3.14 | 测评选择题加“不知道”选项与每题补充说明（交给 AI 评分参考），选择题必答 | ✅ 2026-11-19 |
| M3.15 | AI 调用记录落库（任务 / token / 耗时 / 错误，不存 prompt 与回复）、设置页用量摘要、调用记录弹窗、应用内日志查看器 | ✅ 2026-11-19 |
| M4 | 深色模式、安装器 / 自动更新（可选，未排期） | ⬜ 未开始 |

当前能力（M3.15 后）：配置 AI → 测评 → 每日文章 → 双击查词 / 悬浮释义卡 →
加入生词本 → 阅读检测（两轮自适应 + 盲评 + 本地后验 + 产出题）→ 每周复盘；
SM-2 复习可完全离线；设置页可导出 JSON / CSV、查看 AI 调用记录与运行日志。
所有 AI 请求都带任务标签并写入 `ai_calls`（token / 耗时 / 错误，
不保存提示词与模型回复）。

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

## 4. M4 可选任务（未排期）

- **深色模式**：把 `gui/theme.py` 的颜色抽成两套 palette，`config.json` 的
  `ui.theme` 存偏好；QSS 模板用占位符替换颜色，字体链不变；
- **安装器 / 自动更新**：安装器可用 Inno Setup 包 onefile exe；自动更新需要
  稳定的发布源（当前网络环境下 GitHub 不通，需先确定分发方式）；
- **真实环境调优**：根据实际使用调整 `srs.py` 系数、文章校验区间、复习每日上限。

## 5. 下次会话开工指令

把下面这段直接发给 Claude：

```text
继续 py-learn 的 english_learner。先读 CLAUDE.md 与 docs/english_learner/PROGRESS.md
（当前看板，约 150 行），确认 M3.15 已完成、基线 314 passed。需要改 AI 客户端 /
存储 / 阅读检测 / 导出时，再定向读 ARCHITECTURE.md 对应小节；HISTORY.md 只在查
“以前为什么这么改”时看，不要默认加载；技术债见 TECH_DEBT.md。下一步优先做真实
环境调优：混合难度与自适应出题质量、置信度阈值校准、每周复盘文案、真实网关的
usage 覆盖度；可选 M4（深色模式、安装器 / 自动更新）继续保留。每完成一块：补测试，
更新本看板，并在 HISTORY.md 追加交付记录。不要引入 ORM，不要跳过 TaskWorker 直接
调 AI，services/storage 不得 import PySide6。
```

开工前先自检：

```bash
uv sync
uv run pytest -q
uv run ruff check src/english_learner tests
```
