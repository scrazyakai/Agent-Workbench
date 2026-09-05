# AI Workbench

基于 FastAPI 和 PostgreSQL 16 的 Agent 开发平台。当前完成 Agent 草稿、配置校验、不可变版本快照和查询接口。

## 本地启动

需要 Python 3.13+、uv 和 PostgreSQL 16。本机已配置 Docker 容器 `xind-postgres`，开发库 `ai_workbench`、测试库 `ai_workbench_test`，专用账号 `ai_workbench`。连接配置保存在不进入 Git 的 `.env`，不要将密码写入文档或请求示例。

新环境按 `.env.example` 创建 `.env`，填写实际连接串；开发、测试数据库须事先创建。测试库名必须以 `_test` 结尾。运行：

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1
```

访问 [OpenAPI 调试页](http://127.0.0.1:8000/docs) 和 [健康检查](http://127.0.0.1:8000/health)。根目录 `main.py` 兼容 `uvicorn main:app`。应用启动不会自动建表；数据库不可达或缺少业务表时健康检查返回 `503`。

## 验证

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run alembic check
```

集成测试使用 `.env` 或环境变量中的 `WORKBENCH_TEST_DATABASE_URL`，自动升级专用测试库的迁移，并为每个用例分配随机 Workspace；测试后只清理该用例的数据。没有测试库配置时数据库用例会跳过，不代表集成验证通过。测试覆盖并发发布、草稿与快照隔离、失败回滚、分页、Workspace 查询约束和数据库不可用。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 数据库与业务表健康检查 |
| POST | `/v1/agents` | 创建草稿 |
| GET | `/v1/agents` | 列表，支持 `offset`、`limit` 和名称子串 `name` |
| GET | `/v1/agents/{id}` | 草稿详情 |
| PATCH | `/v1/agents/{id}` | 更新草稿顶层字段 |
| POST | `/v1/agents/{id}/versions` | 发布配置快照 |
| GET | `/v1/agents/{id}/versions` | 版本列表，最新在前，支持分页 |
| GET | `/v1/agents/{id}/versions/{version}` | 版本详情 |

创建草稿只需 `name`；发布要求非空 `system_prompt` 和 `model_config.connection_id`。请求示例见 `test_main.http`。

PATCH 中未提供的顶层字段保持原值，提供的嵌套对象整体替换；嵌套对象中省略的字段采用默认值。`output_schema` 可显式设为 `null`，其余字段不可设为 `null`。输入输出 Schema 使用 JSON Schema Draft 2020-12，目前校验 Schema 定义本身。

草稿配置存储于 JSON 字段，名称单独建立 Workspace 内唯一约束；版本独立复制完整配置。PostgreSQL 行锁串行化同一 Agent 的修改和发布，版本唯一约束提供额外保护。不可变性指服务/API 不提供版本修改和删除操作，数据库管理员仍可直接修改数据。

错误结构为 `{"error":{"code":"...","message":"...","details":[],"request_id":"..."}}`，响应头含 `X-Request-ID`。同名冲突返回 `409`、配置无效返回 `422`、资源不存在返回 `404`、数据库异常返回 `503`，错误响应不包含原始数据库异常或凭证。

## 统一异常与日志

异常处理集中在 `app/core/exception_handlers.py`，访问日志与请求上下文在 `app/core/middleware.py`，JSON 日志配置在 `app/core/logging.py`。业务代码继续抛出 `DomainError(status, code, message, details)`；已知业务异常不应包含内部凭证或敏感内容。未知异常统一返回 `500/internal_error`，健康检查数据库失败也使用统一的 `503/database_unavailable` 错误结构。HTTP 异常保留 `WWW-Authenticate`、`Allow` 等协议响应头。

使用 `logging.getLogger(__name__)` 记录应用日志（模块应处于 `app` 命名空间）。应用与 Uvicorn 日志按行输出 JSON 到标准输出，方便终端、Docker 或日志采集系统处理；通过 `WORKBENCH_LOG_LEVEL=INFO` 设置级别。配置重复调用不会叠加处理器，第三方/root 日志配置保持独立。

每次请求由服务生成新的 request ID，返回在响应头和错误体中，并通过 ContextVar 自动加入业务日志。访问日志记录 HTTP 方法、路由模板、状态码及总耗时；不记录原始 URL、查询参数、请求体或鉴权头。Uvicorn 原始访问日志被关闭，避免重复和暴露 URL 参数。

数据库与未知异常日志保留异常类型、堆栈文件/行号/函数，不输出异常原文、SQL 参数、源码行或局部变量。业务日志应使用固定事件描述，避免把密钥拼入 message；该配置不会自动清洗任意业务字符串。流式响应或后台任务在响应发出后失败时，会记录异常并向服务器传播，无法将已发送响应改成 JSON 错误。

新增测试 `uv run pytest tests/test_error_logging.py -q` 不依赖数据库，验证统一错误、敏感内容不回显、协议头、并发上下文隔离与日志配置。

## 当前边界

这是 M1 的定义管理部分。模型和工具只保存引用，尚未验证引用存在性或权限，也不执行模型、工具和 Run；此处发布表示配置快照发布，不表示可执行上线。尚未实现鉴权、React、Workflow、Trace 和 Eval，服务仅用于本地开发。固定 Workspace 来自服务端配置，不提供安全多租户承诺。后续优先实现 ModelConnection、工具版本和执行链路。
