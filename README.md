# 豆瓣电影爬虫（douban-movies）

用 Java 视角学习 Python 工程结构：抓取指定年份的豆瓣电影，按评分从高到低生成
Markdown 报告，并附带每部电影的热门短评。

工程骨架包含：包分层、依赖管理、虚拟环境、入口点、静态检查、打包。

## 快速开始

```bash
# 1. 按 pyproject.toml + uv.lock 创建 .venv 并安装依赖（含 dev 组）
uv sync

# 2. 抓取并生成报告
uv run douban-movies 2023 -n 50 -o report.md

# 3. 静态检查 / 格式化
uv run ruff check .
uv run ruff format .

# 4. 打包（库/CLI 才需要）
uv build          # 产出 dist/*.whl 和 dist/*.tar.gz
```

## 命令行用法

输入年份，抓取该年电影并按评分从高到低生成 Markdown 报告，默认 100 部、
每部抓 15 条热门评论。

```bash
uv run douban-movies 2023 -n 50 -o report.md   # 前 50 部，保存 Markdown 报告
uv run douban-movies 2023 --comments 10        # 每部只抓 10 条评论
uv run douban-movies 2023 --comments 0         # 不抓评论，只输出榜单
uv run douban-movies 2023 > report.md          # 不传 -o：Markdown 打到 stdout
uv run douban-movies 2023 -o out.json          # 保存 JSON（含评论）
uv run douban-movies 2023 -o out.csv           # 保存 CSV（Excel 直接打开）
uv run douban-movies                           # 不带年份则交互输入
uv run douban-movies 2019 --no-filter          # 关闭过滤，保留演唱会/晚会等
uv run douban-movies 2019 --exclude 纪录片     # 额外排除关键字（可重复）
uv run python -m douban_movie 2023             # 等价写法：按包名运行
uv run python douban_main.py 2023              # PyCharm 右键运行入口
```

> 默认 100 部 + 每部 15 条评论 = 100 多次请求，按默认 `--delay 1` 大约要 2 分钟。
> 只想快速看榜单时加 `--comments 0`。

实现要点（都是实测踩出来的坑）：

- 豆瓣接口的 `year` 参数单独传会被忽略，年份必须放进 `tags=电影,2023`；
- 服务端排序有轻微倒挂，程序会在本地再按评分排一次；
- 默认按标题过滤演唱会、音乐会、晚会、周六夜现场、电视特别篇等非电影条目，
  过滤规则在 `src/douban_movie/filters.py`；被过滤的条目会占名额，程序会自动多翻页补足；
- 每部电影默认抓 15 条热门短评，走移动端 Rexxar 接口（网页版详情页会被反爬拦截），
  评论按点赞数从高到低排序；单部失败不影响整批，最后会提示失败数量；
- Markdown 报告 = 顶部速览表（可点击跳转）+ 每部电影详情 + 评论引用块；
- 进度和错误全部走 stderr，stdout 只放 Markdown，方便 `> report.md` 重定向；
- 默认每次请求间隔 1 秒并带指数退避重试，被反爬时调大 `--delay`；
- Windows 上读写文件必须显式 `encoding="utf-8"`，CSV 用 `utf-8-sig` 兼容 Excel。

接口是豆瓣的公开接口，请控制请求频率，仅用于个人学习。

## 目录结构

```
py-learn/
├── src/
│   └── douban_movie/         # 包目录 = Java 的 package
│       ├── __init__.py       # 包入口，对外导出公共 API
│       ├── __main__.py       # 支持 python -m douban_movie（≈ Main-Class）
│       ├── models.py         # Movie / Comment 实体（≈ Java record）
│       ├── client.py         # HTTP 客户端（≈ FeignClient / RestTemplate）
│       ├── crawler.py        # 业务编排：分页 + 过滤 + 去重 + 排序 + 评论
│       ├── filters.py        # 标题过滤规则（排除演唱会/晚会/特别篇）
│       ├── report.py         # Markdown 报告渲染（≈ 模板引擎）
│       ├── storage.py        # 导出 Markdown / JSON / CSV
│       └── cli.py            # 表现层（≈ Controller / main）
├── tests/                    # 测试目录 = Java 的 src/test/java（当前为空）
├── douban_main.py            # 根目录薄入口，方便 PyCharm 右键运行
├── pyproject.toml            # 项目总配置 = pom.xml
├── uv.lock                   # 锁定所有依赖的精确版本
├── .python-version           # 指定 Python 版本
├── .gitignore
└── .venv/                    # 虚拟环境（不入库，uv sync 自动生成）
```

## 与 Java / Maven 对照

| Java / Maven | Python / uv |
|---|---|
| `pom.xml` | `pyproject.toml` |
| `src/main/java` | `src/douban_movie` |
| `src/test/java` | `tests/` |
| `com.example.MovieCrawler` | `douban_movie.crawler.MovieCrawler`（包路径 = 目录路径） |
| `mvn dependency:resolve` | `uv sync` |
| `mvn test` | `uv run pytest` |
| `mvn package` | `uv build` |
| `mvn checkstyle:check` | `uv run ruff check .` |
| 本地仓库 `~/.m2` | 下载缓存（`~/.cache/uv`） |
| 一次构建的 classpath | 一个 `.venv/Lib/site-packages` |
| Lombok `@Data` | 标准库 `@dataclass` |
| `System.exit(1)` | `raise SystemExit(1)` / `main()` 返回 1 |

## 分层调用关系

```
cli (Controller)
  └── crawler (Service)
        ├── client (HTTP / FeignClient)
        ├── filters (Predicate)
        ├── models (Entity)
        ├── report (View / Template)
        └── storage (Repository / Exporter)
```

## PyCharm 配置

打开项目后，PyCharm 通常会自动识别 `.venv`。如果没有：

`Settings → Project: py-learn → Python Interpreter → Add Interpreter → Add Local Interpreter → Existing → 选择 .venv\Scripts\python.exe`

之后 `douban_main.py` 可以直接右键运行；也可以在底部 Terminal 里执行
`uv run douban-movies 2023 -o report.md`。
