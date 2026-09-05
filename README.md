# AI Workbench

基于 FastAPI 和 PostgreSQL 16 的 Agent 开发平台。当前完成 Agent 草稿、模型连接管理、配置校验、不可变版本快照和查询接口。

## 本地启动

需要 Python 3.13+、uv 和 PostgreSQL 16。本机已配置 Docker 容器 `xind-postgres`，开发库 `ai_workbench`、测试库 `ai_workbench_test`，专用账号 `ai_workbench`。连接配置保存在不进入 Git 的 `.env`，不要将密码写入文档或请求示例。

新环境按 `.env.example` 创建 `.env`，填写实际连接串；开发、测试数据库须事先创建。测试库名必须以 `_test` 结尾。运行：

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1
```

访问 [OpenAPI 调试页](http://127.0.0.1:8000/docs) 和 [健康检查](http://127.0.0.1:8000/health)。根目录 `main.py` 兼容 `uvicorn main:app`。应用启动不会自动建表；数据库不可达或缺少业务表时健康检查返回 `503`。

## 前端控制台

前端位于 `frontend/`，使用 React、TypeScript 和 Vite。先启动上述 FastAPI 服务，再在另一个终端运行：

```bash
cd frontend
npm install
npm run dev
```

访问 [Agent 控制台](http://127.0.0.1:5173)。开发服务器会将 `/v1` 和 `/health` 代理到 `127.0.0.1:8000`，无需单独配置 CORS。生产构建使用 `npm run build`，产物位于 `frontend/dist/`；部署时应将 API 与前端置于同源反向代理之后。

控制台提供 `/overview`、`/agents`、`/tools`、`/workflows`、`/runs`、`/evaluations`、`/settings` 和 `/help` 八个可直接访问、刷新和前进后退的页面。Agents 已接通真实 API，支持分页列表、名称与标签筛选、草稿创建和修改、工具版本绑定、发布新版本及版本时间线。设置页支持创建、编辑、启停及测试 Model Connection；Tools 页支持 HTTP 与远程 MCP Streamable HTTP 工具的注册、发现、测试、启停和版本发布。

Workflows、Runs 和 Evaluations 目前提供完整的信息架构、模块状态和跨页面入口；对应后端 API 尚未实现，页面会明确显示“待接入”，不会伪造业务数据或成功操作。

## 验证

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run alembic check
cd frontend && npm run lint && npm run build
```

集成测试使用 `.env` 或环境变量中的 `WORKBENCH_TEST_DATABASE_URL`，自动升级专用测试库的迁移，并为每个用例分配随机 Workspace；测试后只清理该用例的数据。没有测试库配置时数据库用例会跳过，不代表集成验证通过。测试覆盖并发发布、草稿与快照隔离、失败回滚、分页、Workspace 查询约束和数据库不可用。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 数据库与业务表健康检查 |
| POST | `/v1/model-connections` | 创建 OpenAI 兼容模型连接 |
| GET | `/v1/model-connections` | 分页列出连接，可按名称和启用状态筛选 |
| GET | `/v1/model-connections/{id}` | 查询连接详情 |
| PATCH | `/v1/model-connections/{id}` | 修改或启停连接 |
| POST | `/v1/model-connections/{id}/test` | 解密数据库凭证并验证模型可用性 |
| POST | `/v1/tools` | 注册 HTTP 或 MCP Tool 草稿 |
| GET | `/v1/tools` | 分页查询工具目录 |
| GET / PATCH | `/v1/tools/{id}` | 查询或修改工具草稿 |
| POST | `/v1/tools/{id}/test` | 使用草稿配置执行脱敏测试 |
| POST | `/v1/tools/{id}/discover` | 发现远程 MCP Server 提供的工具 |
| POST | `/v1/tools/{id}/versions` | 发布不可变 ToolVersion |
| GET | `/v1/tools/{id}/versions` | 查询工具版本 |
| POST | `/v1/agents` | 创建草稿 |
| GET | `/v1/agents` | 列表，支持 `offset`、`limit`、名称子串 `name` 和精确标签 `tag` |
| GET | `/v1/agents/{id}` | 草稿详情 |
| PATCH | `/v1/agents/{id}` | 更新草稿顶层字段 |
| POST | `/v1/agents/{id}/versions` | 发布配置快照 |
| GET | `/v1/agents/{id}/versions` | 版本列表，最新在前，支持分页 |
| GET | `/v1/agents/{id}/versions/{version}` | 版本详情 |

创建草稿只需 `name`；发布要求非空 `system_prompt`，且 `model_config.connection_id` 必须指向当前 Workspace 内存在并启用的 Model Connection。请求示例见 `test_main.http`。

Model Connection 首期固定使用 `openai_compatible` 协议。创建时提交 `api_key`，服务使用 AES-256-GCM 和随机 nonce 加密后仅持久化密文；连接 ID 作为附加认证数据（AAD），密文无法复制到另一条连接使用。数据库约束保证每条连接恰好配置密文或旧版环境变量引用中的一种。读取接口只返回 `credential_configured`，不会返回 API Key 或密文。编辑时省略 `api_key` 会保留现有凭证，提供新值则完成轮换。连接测试临时解密真实值，并通过 `{base_url}/models` 验证鉴权及 `model_name`。主密钥缺失、密文认证失败和凭证不存在会返回不同的安全错误码，均不回显密钥。测试不会跟随重定向，也不会回显上游正文或请求头。当前本地开发版本允许 HTTP、回环和私网目标，上线前必须增加权限与 SSRF 限制。

部署前必须生成独立的 32 字节主密钥，并以 Base64 写入不会进入 Git 的 `.env`：

```bash
# 生成一次并妥善备份；丢失后数据库中的 API Key 无法恢复
openssl rand -base64 32

# .env
WORKBENCH_CREDENTIAL_ENCRYPTION_KEY=粘贴上一步的输出
```

修改主密钥后需要重启后端；不要直接轮换主密钥，否则既有密文将无法解密。迁移前已经保存的 `env://` 引用仍可用于连接测试，在编辑页输入 API Key 并保存后会自动转为数据库密文。

HTTP Tool 使用固定 Endpoint、显式主机允许列表和输入字段映射；运行前解析 DNS 并拒绝回环、私网、链路本地及其他非公网地址，不跟随重定向，并限制响应大小。MCP Tool 首期仅支持远程 Streamable HTTP，使用官方 Python SDK 完成协议协商、工具发现及调用；出站目标和凭证策略与 HTTP Tool 共用。stdio MCP 会启动本地子进程，首期出于安全原因不开放。工具凭证使用独立 AAD 加密，不进入 ToolVersion 或 AgentVersion 快照。

标签筛选区分大小写，与名称条件取交集，先筛选再分页；`total` 为筛选后总数。例如 `/v1/agents?tag=demo&name=助手&limit=20`。标签中的 `%`、`_` 等字符按字面匹配，不作为通配符。

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

这是 M1 的定义管理部分。模型连接、HTTP/MCP Tool Registry 及 Agent 工具版本绑定已经完成，但尚未实现 Agent Run/Worker；此处发布表示配置快照发布，不表示可执行上线。尚未实现鉴权、审批执行、Workflow、Trace 和 Eval，服务仅用于本地开发。固定 Workspace 来自服务端配置，不提供安全多租户承诺。下一步进入 Run 状态机、Worker 和基础 Trace。
