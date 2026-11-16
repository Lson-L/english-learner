# py-learn

用 Java 视角学习 Python 工程结构的最小可运行项目：一个命令行待办任务管理器。

工程骨架包含：包分层、依赖管理、虚拟环境、入口点、单元测试、静态检查、打包。

## 快速开始

```bash
# 1. 按 pyproject.toml + uv.lock 创建 .venv 并安装依赖（含 dev 组）
uv sync

# 2. 跑测试
uv run pytest

# 3. 运行 CLI
uv run py-learn add "学习 Python 工程结构"
uv run py-learn list
uv run py-learn done 1
uv run py-learn remove 1

# 4. 静态检查 / 格式化
uv run ruff check .
uv run ruff format .

# 5. 打包（库/CLI 才需要）
uv build          # 产出 dist/*.whl 和 dist/*.tar.gz
```

## 目录结构

```
py-learn/
├── src/
│   └── pylearn/              # 包目录 = Java 的 package
│       ├── __init__.py       # 包入口，对外导出公共 API
│       ├── __main__.py       # 支持 python -m pylearn（≈ Main-Class）
│       ├── exceptions.py     # 自定义异常
│       ├── models.py         # 实体层（≈ entity / POJO，用 dataclass）
│       ├── storage.py        # 仓储层（≈ Repository / DAO，JSON 文件存储）
│       ├── service.py        # 业务层（≈ Service）
│       └── cli.py            # 表现层（≈ Controller / main）
├── tests/                    # 测试目录 = Java 的 src/test/java
│   ├── conftest.py           # 公共 fixture（≈ @BeforeEach 集中定义）
│   ├── test_cli.py
│   ├── test_service.py
│   └── test_storage.py
├── main.py                   # 根目录薄入口，方便 PyCharm 右键运行
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
| `src/main/java` | `src/pylearn` |
| `src/test/java` | `tests/` |
| `com.example.TaskService` | `pylearn.service.TaskService`（包路径 = 目录路径） |
| `mvn dependency:resolve` | `uv sync` |
| `mvn test` | `uv run pytest` |
| `mvn package` | `uv build` |
| `mvn checkstyle:check` | `uv run ruff check .` |
| 本地仓库 `~/.m2` | 下载缓存（`~/.cache/uv`） |
| 一次构建的 classpath | 一个 `.venv/Lib/site-packages` |
| JUnit `@Test` | pytest 的 `test_*.py` + `assert` |
| JUnit `@TempDir` | pytest 内置 fixture `tmp_path` |
| Lombok `@Data` | 标准库 `@dataclass` |
| `System.exit(1)` | `raise SystemExit(1)` / `main()` 返回 1 |

## 分层调用关系

```
cli (Controller)
  └── service (Service)
        ├── models (Entity)
        └── storage (Repository)
```

## PyCharm 配置

打开项目后，PyCharm 通常会自动识别 `.venv`。如果没有：

`Settings → Project: py-learn → Python Interpreter → Add Interpreter → Add Local Interpreter → Existing → 选择 .venv\Scripts\python.exe`

之后 `main.py` 可以直接右键运行；pytest 测试文件里也会出现绿色运行按钮。
