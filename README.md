# Daily English · 每日英语

每天一篇略高于你当前水平的英文文章，阅读时双击查词、把生词收进生词本，
第二天用间隔重复把它们记住。

应用完全在本机运行：PySide6 桌面界面，SQLite 存学习数据，AI 能力来自你
自己配置的 OpenAI 兼容服务。没有账号、云同步和社交功能；不联网也能复习。

## 使用流程

- **首次启动**：配置服务地址、API Key、模型 → 真实连接测试 → 水平测评
  （可跳过并手动选级）→ 进入首页。
- **每天**：读今日文章 → 双击查词 → 加入生词本 → 完成阅读检测 → 复习到期词。
- **每周**：查看本周检测、生词与复习数据，确认是否调整等级。

## 功能概览

**每日文章**：根据当前 CEFR 等级（A2–C1）和兴趣生成 250–350 词的文章，
当天缓存；可带提示词换一篇。生成时会顺带写出难词的语境义，打开文章后
后台再批量预取一批，双击这些词直接出释义。

**阅读与查词**：正文全宽阅读器，双击单词弹出悬浮释义卡（音标、词性、
语境义、英文释义、例句），确认后才加入生词本并高亮。生词按词典原形入库，
同时记录它在文章中的词形。

**阅读检测**：读完后先答 3 道当前等级题，再按表现出 2 道边界题和
2 道英译中题；不会的选择题可选「不知道」并补充原因。AI 讲评加盲评，
本地后验与连续证据决定是否调级，单次检测不会直接跨级。

**生词本与复习**：列表、筛选、搜索、编辑释义、批量删除；简化 SM-2
四档复习，每日目标默认 20，每 10 张一个检查点；可开启拼写回忆，系统按
学习阶段自动编排看词 / 拼写 / 双阶段卡。复习完全离线，只有求助 AI 时才联网。

**额外练习**：回译练习、每周复盘、档内进度仪表盘、学习效果看板。

**学习统计**：侧边栏「统计」页把本地记录画成积累曲线：26 周打卡热力图、
累计生词 / 阅读 / 复习、近 30 天每日活动、词汇掌握分布、按等级的检测
得分趋势和 12 个成就徽章；全部离线聚合，不消耗 token。

**本地数据**：JSON / CSV 导出、整库导出与导入迁移、每次启动自动备份；
Windows 上 API Key 使用 DPAPI 加密；AI 调用只记录 token、耗时和错误，
不保存提示词与模型回复。

**桌面体验**：启动遮罩、窗口大小与位置记忆、深色等宽的低调主题、
请求可取消、AI 错误统一翻译成中文提示。

## 运行

需要 Python 3.13 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
uv run english-learner
```

首次启动必须填写服务地址、API Key 和模型 id，并通过一次真实请求的连接测试，
才能进入主窗口。常见服务商已内置预设，通常只需要粘贴一个 Key。

```bash
uv run english-learner --reconfigure   # 强制重新配置 AI
uv run english-learner --version       # 查看版本号
```

如果已经构建过，也可以直接双击 `dist/DailyEnglish.exe`。

## 打包

在 Windows x64 机器上（不能跨平台构建）：

```bash
uv sync                                                              # 含 dev 组的 pyinstaller
uv run pyinstaller --noconfirm --clean DailyEnglish.spec             # 单文件
DAILYENGLISH_ONEDIR=1 uv run pyinstaller --noconfirm --clean DailyEnglish.spec   # 目录版
```

PowerShell 里改环境变量的写法是 `$env:DAILYENGLISH_ONEDIR=1`，再执行最后一条命令。

- **单文件**：`dist/DailyEnglish.exe`，约 36MB，把 Python 解释器、PySide6、
  VC++ 运行库与内置字体全部打进一个 exe，目标机器**不需要安装 Python 或
  PySide6**，双击即用；代价是每次启动要解压到临时目录，首屏前多等 2~4 秒。
- **目录版**：`dist/DailyEnglish/`，把整个目录一起发给用户，双击里面的
  `DailyEnglish.exe` 即可；启动更快，杀软误报更少，适合再做安装包。

`DailyEnglish.spec` 已经裁掉了用不到的 Qt 模块与插件（Qml/Quick、Pdf、Svg、
Network、OpenGL、虚拟键盘、多余图片格式等）以及 pygments/setuptools 等
开发期依赖；界面只用到 QtCore / QtGui / QtWidgets，升级 PySide6 后重新打包
需做一次双击冒烟测试。目标机器要求：**Windows 10 1809+ / Windows 11，64 位**。

分发时提醒使用者：exe 未做代码签名，首次运行可能被 SmartScreen 拦下，点
「更多信息 → 仍要运行」即可；API Key 用 Windows DPAPI 加密，和当前 Windows
用户绑定，换机器后需要重新填写。配置与学习数据仍写入用户目录，不随 exe 走。

## 配置 AI 服务

兼容 OpenAI Chat Completions 协议即可，内置 DeepSeek、Kimi、通义、智谱、
硅基流动、OpenRouter、Ollama 等预设。

- `base_url`：通常以 `/v1` 结尾；Ollama 为 `http://localhost:11434/v1`
- `api_key`：本地 Ollama 可随便填（如 `ollama`）
- `model`：以服务商控制台为准，例如 `deepseek-chat`
- `temperature`：0 更稳定，1 更有创造性

保存前会发送一条极短的 chat 请求，同时验证地址、Key 和模型权限；
失败时给出中文原因，修好之前不能进入下一步。

## 数据位置

配置、日志与数据库都在用户目录，不在仓库内：

| 平台 | 目录 |
|---|---|
| Windows | `%APPDATA%\EnglishLearner\` |
| macOS | `~/Library/Application Support/EnglishLearner/` |
| Linux | `~/.config/english_learner/` |

- `config.json`：AI 配置、界面主题与字体、复习偏好、窗口几何快照；
- `data.db`：文章、生词、高亮、阅读检测、复习记录、回译练习、每周复盘、
  每日统计、AI 调用记录（schema v8）；
- `logs/app.log`：运行日志，每份 1 MB 轮转，保留 3 个备份；
- `backups/data-YYYYMMDD.db`：每次启动自动备份，保留最近 7 份。

换电脑时，在「设置 → 数据导出与备份」导出数据库，在新机器导入即可；
导入前会自动备份本机数据。API Key 不随数据库迁移，需要在新机器重新填写。

## 项目结构

```
src/english_learner/
├── app.py          启动入口：日志、启动遮罩、配置判断、主窗口
├── models/         领域 dataclass
├── storage/        SQLite 连接、建表迁移与纯 SQL 仓库
├── services/       业务逻辑（纯 Python，不依赖 PySide6）
└── gui/            PySide6 视图、弹窗、页面与后台线程
run_app.py          IDE 启动入口
tests/              存储、服务层与 GUI 冒烟测试
docs/english_learner/ 产品、进度、架构、技术债与历史记录
```

分层约定：`gui/` 只管界面，`services/` 与 `storage/` 不 import PySide6；
所有网络调用都在 `gui/workers.py` 的 `TaskWorker` 子线程执行；数据库使用
标准库 `sqlite3` 手写 SQL，不引入 ORM。

## 开发

```bash
uv run pytest -q                              # 单元测试（674 个）
uv run ruff check src/english_learner tests   # 静态检查
uv run ruff format src/english_learner tests  # 格式化
```

用 PyCharm 时，解释器指向 `.venv\Scripts\python.exe`，运行根目录的
`run_app.py` 即可；也可以新建 Python 配置，运行方式选 Module name，
填 `english_learner`。

## 文档

- [PRODUCT.md](docs/english_learner/PRODUCT.md)：产品定位、范围与已确认决策
- [PROGRESS.md](docs/english_learner/PROGRESS.md)：当前进度与下一步
- [ARCHITECTURE.md](docs/english_learner/ARCHITECTURE.md)：文件地图、关键实现与踩坑
- [TECH_DEBT.md](docs/english_learner/TECH_DEBT.md)：已知问题与技术债
- [HISTORY.md](docs/english_learner/HISTORY.md)：已完成里程碑的交付记录

## 状态

核心功能已经闭环，可以作为日常学习工具使用，674 个测试通过。
剩余的都是可选增强：M6.6 强证据升级、M6.7 边界文案、安装器与自动更新。
