# AI Workbench

基于 FastAPI 和 PostgreSQL 16 的 Agent 开发平台。支持配置、发布 Agent，通过真实模型与只读 HTTP/MCP 工具执行任务，并提供 PostgreSQL 租约、Checkpoint、恢复、取消、预算、SSE 和基础步骤 Trace。

## 本地启动

需要 Python 3.13+、uv 和 PostgreSQL 16。本机已配置 Docker 容器 `xind-postgres`，开发库 `ai_workbench`、测试库 `ai_workbench_test`，专用账号 `ai_workbench`。连接配置保存在不进入 Git 的 `.env`，不要将密码写入文档或请求示例。

新环境按 `.env.example` 创建 `.env`，填写实际连接串；开发、测试数据库须事先创建。测试库名必须以 `_test` 结尾。运行：

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1
```

Runtime 使用独立 Worker。另开终端启动：

```bash
uv run python -m app.worker
```

默认每个 Worker 串行执行一个 Run；可以启动多个 Worker 横向并发。Worker 使用数据库行锁和租约领取任务，异常退出后，其他 Worker 会在租约过期后从最新 Checkpoint 继续原 Run。

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

Runs 页面已接通真实 API，支持从已发布 Agent 版本创建 Run、状态筛选、详情与事件轮询，以及协作式取消。Workflows 和 Evaluations 仍为信息架构占位，对应后端 API 尚未实现。

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
| POST | `/v1/runs` | 基于已发布 Agent 版本创建 Run，支持幂等键 |
| GET | `/v1/runs` | 按状态、Agent 或 thread 筛选运行摘要 |
| GET | `/v1/runs/{id}` | 查询状态、输入、结果或结构化错误 |
| GET | `/v1/runs/{id}/events` | 按递增游标读取持久化事件 |
| GET | `/v1/runs/{id}/stream` | SSE，支持 `after` 和 `Last-Event-ID` 断线续传 |
| GET | `/v1/runs/{id}/steps` | 步骤状态、尝试次数、耗时及调用摘要 |
| POST | `/v1/runs/{id}/cancel` | 幂等提交协作式取消 |

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

这是 M1 的受控模型执行阶段，尚未实现鉴权、人工审批、Workflow、完整 Trace / Eval、费用计价和 Redis 调度。服务仅用于本地开发，固定 Workspace 不提供安全多租户承诺。默认仍部署单 Worker、单并发；多 Worker 的领取与 thread 互斥有数据库测试保护，不代表已经完成规模化压测。

## 模型 Runtime 使用与验收

部署升级时先停止旧 API 和 Worker，再执行 `uv run alembic upgrade head` 并启动新版进程；不要混跑新旧执行器。`0007` 为旧 Run 保留 `deterministic` 模式，新建 Run 默认使用 `model`，会产生上游调用费用。Runs 创建器可以显式选择「确定性测试」，不调用模型或工具。

1. 创建并测试 Model Connection。真实生成由 LangChain `ChatOpenAI` 调用 Chat Completions，平台不再手写模型 HTTP 请求或 SSE 解码。当前锁定版本会将输出上限转换为 `max_completion_tokens`，启用 `stream_usage`，绑定工具时关闭并行工具调用；温度等参数的模型特定处理由 LangChain 完成。供应商必须兼容相应参数及流式格式；仅 `/models` 测试成功不保证生成接口兼容，不支持时会以安全错误结束。
2. 发布一个只读 HTTP GET 或标注为只读的远程 MCP Tool，再将具体 ToolVersion 绑定到 Agent，填写系统提示与预算并发布 AgentVersion。
3. 从 Agent 版本页或 Runs 创建真实模型 Run。输入是 JSON 对象；系统提示应明确如何解释输入、何时调用工具。设置输出 Schema 时会要求模型输出 JSON 并在本地校验；此版本未启用供应商的强制结构化输出模式。
4. 在 Run 详情观察模型输出、步骤 Trace、Token 用量和最终结果。结果格式为 `{"text":"...","structured":null}`，配置输出 Schema 时 `structured` 为已校验 JSON。中间输出按模型步骤与尝试次数分组，失败尝试不会冒充最终结果。
5. 在模型或工具等待期间取消 Run，状态应从 `cancelling` 转为 `cancelled`，不继续后续步骤。总时限从首次领取算起，含重试与恢复等待；到期为 `timed_out`。单次模型/工具超时是独立的安全错误。

运行语义与限制：

- Run 创建时固定 Agent、模型非敏感配置与 ToolVersion 快照；凭证调用时解密，不放入运行快照。调用前重新检查连接/工具启用状态及工具风险；端点改变时拒绝继续，避免向旧地址发送新凭证。其他草稿变化不改变已发布调用参数。
- 仅执行已绑定、已发布、`risk_level=read` 且无需审批的工具，HTTP 限 GET。MCP 只读性依赖注册方如实声明，不是远端副作用的技术证明。保留现有工具出站限制、超时和响应大小限制。
- 模型响应、工具响应与运行对话上下文在每步完成时和 Checkpoint 同事务落库。提交后的步骤不会重做；外部调用完成但检查点未提交时，接管可能再次调用或再次计费，仍是「至少一次」，不是 exactly-once。
- 每次模型/工具尝试消耗一步；工具预算统计逻辑调用，HTTP 内部 GET 重试仍由发布配置控制。调用前用消息/工具定义 UTF-8 字节数加余量及输出上限做保守 Token 预留，拿到有效用量后替换；失败、中断或未报告用量的调用保留预留，恢复不会清零。该估计不是精确 tokenizer 或供应商硬消费上限；供应商超报用量会在入账后终止 Run。
- `usage.total_tokens` 是已报告用量，`charged_tokens` 是预算占用（含未知用量预留），`unmeasured_calls` 标记成功但未报告用量的调用。未配置价格，不显示伪造费用。
- SSE 发送 `run_event`，其 `id` 为每个 Run 内递增序号；终态补齐全部历史后发送 `done`。支持超过 100 条事件分页补齐，浏览器断线自动续传。模型增量是脱敏后的中间观测，最终结果只认 `succeeded`。
- thread ID 仅用于串行调度，不自动继承其他 Run 的对话。当前 Trace 为步骤及事件级摘要，不是完整 Span 树；字段级敏感数据策略与保留清理仍待实现。不要在输入或提示中粘贴密钥。

自动回归：`uv run pytest -q`。模型 Runtime 专项：`uv run pytest tests/test_model_runtime.py -q`，使用专用 PostgreSQL 测试库及模拟上游，不调用付费模型。覆盖协议流解析、只读工具循环、检查点恢复、同 thread 并发领取、旧租约写回拒绝、长请求续租、取消、超时、预算、脱敏及 SSE 游标。

### LangChain / LangGraph 执行实现

- LangChain：使用 `langchain-openai` 的 `ChatOpenAI.bind_tools()` / `astream()`；聚合原生 `AIMessageChunk`、工具调用片段和用量。SDK 负责 HTTP 请求和 SSE 协议解析，平台只保留策略检查、脱敏、格式转换与错误归类。模型输出解码后限制 2 MiB；原始流帧的缓冲与解码交由 SDK，不再声称平台在解码前限制原始响应。
- LangGraph：使用 `StateGraph` 的 `model`、`tool` 节点和条件边执行循环，无手写循环调度。每个节点通过现有 Worker 事务，原子提交 StepExecution、用量、事件和 Checkpoint；下一节点只读取已提交状态。
- 恢复：继续使用 PostgreSQL 中已有的运行检查点格式，旧模型 Run 也可恢复。LangGraph 不另配置独立 checkpointer；领取后读取最新数据库状态，从待执行模型或工具节点进入图。此阶段不提供 LangGraph 原生 time-travel / `Command(resume=...)` 审批能力。
- 重试：LangChain / SDK 的 `max_retries=0`，不配置图节点自动重试；失败由平台统一重试并计入预算。数据库租约、单 Worker 单并发、只读工具策略、取消与 SSE 契约不变。
- 隐私：图和模型调用显式禁用 LangSmith 自动追踪，避免开发机环境变量触发未经授权的提示/工具结果上传；仍使用本地 RunEvent 与步骤 Trace。
- 升级：在项目根目录运行 `uv sync`，然后重启 API 和 Worker。本次框架替换不新增数据库迁移。业务 HTTP Tool 的网络执行和 `/models` 连接探测仍保留，不属于手写模型生成请求。

实现参考：[LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)、[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)、[OpenAI Chat Completions](https://developers.openai.com/api/reference/cli/resources/chat)。
