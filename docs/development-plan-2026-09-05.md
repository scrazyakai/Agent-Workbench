# AI Workbench 今日开发计划

- 日期：2026-09-05
- 对应 PRD：`docs/agent-workbench-prd.md` V0.1
- 当前阶段：M1「单 Agent 闭环」启动
- 今日主题：搭建后端工程底座，完成 Agent 定义的最小 API 闭环

## 1. 背景与范围判断

PRD 首期包含 Agent Builder、Tool Registry、Agent Runtime、Durable Execution、Workflow / Multi-Agent、Trace + Eval 六个领域，无法在一个开发日内可靠交付。当前仓库仅有 FastAPI 示例接口，尚无领域模型、数据库、测试和工程分层。

因此今天不追求完整 Agent 执行，而是优先建立后续功能可复用的基础，并打通第一个纵向切片：

```text
创建 Agent 草稿 → 查询 Agent → 修改草稿 → 发布不可变版本 → 查询版本
```

这条链路直接服务于 PRD 的 M1，同时验证“草稿可编辑、发布版本不可变”的核心产品规则。

## 2. 今日目标

今天结束时应具备以下能力：

1. 项目采用清晰的 API、领域服务、数据访问分层，不再继续堆叠在 `main.py`。
2. 服务提供健康检查和 `/v1/agents` 基础接口。
3. 支持创建、读取、更新 Agent 草稿。
4. 支持发布 AgentVersion 快照，已发布版本不可被修改。
5. Agent 和 AgentVersion 数据模型预留 `workspace_id`、负责人、Schema、模型配置、工具绑定和执行约束字段。
6. 核心规则有自动化测试，项目可通过单条命令完成验证。
7. 本地开发方式、环境变量和 API 调试示例有简短说明。

## 3. 今日不做

- 不接入真实大模型、LangChain 或 LangGraph。
- 不实现工具注册、外部 HTTP 调用和凭证管理。
- 不实现 Run、SSE、Worker、队列和 Checkpoint。
- 不实现 React 管理界面。
- 不实现登录鉴权、多角色权限或多租户隔离；仅保留 `workspace_id`。
- 不实现 Workflow、Trace、Eval。

以上内容依赖今天建立的数据契约和工程结构，在后续纵向迭代中完成。

## 4. 技术决策

### 4.1 建议技术栈

| 能力 | 今日选择 | 原因 |
| --- | --- | --- |
| Web API | FastAPI | 已在项目中使用，支持 OpenAPI 与 Pydantic 校验 |
| 配置 | pydantic-settings | 集中管理环境变量，避免配置散落 |
| ORM | SQLAlchemy 2.x | 便于后续迁移 PostgreSQL |
| 开发与测试数据库 | PostgreSQL 16 | 按用户补充要求使用已有 Docker 实例，开发库与测试库独立 |
| 迁移 | Alembic | 从第一天保留可审计的 Schema 演进记录 |
| 测试 | pytest + FastAPI TestClient | 覆盖 API 和核心领域规则 |

已统一采用 PostgreSQL 16；使用 psycopg 3 驱动，并以数据库行锁保护并发发布。

### 4.2 建议目录结构

```text
ai-workbench/
├── app/
│   ├── api/
│   │   ├── router.py
│   │   └── routes/
│   │       ├── agents.py
│   │       └── health.py
│   ├── core/
│   │   └── config.py
│   ├── db/
│   │   ├── models.py
│   │   └── session.py
│   ├── schemas/
│   │   └── agents.py
│   ├── services/
│   │   └── agents.py
│   └── main.py
├── migrations/
├── tests/
│   ├── conftest.py
│   └── test_agents_api.py
├── docs/
├── .env.example
└── pyproject.toml
```

`main.py` 可暂时保留为兼容入口，但只负责导入并暴露 `app.main:app`。

## 5. 数据模型与核心规则

### 5.1 Agent 草稿

建议首日字段：

| 字段 | 说明 |
| --- | --- |
| `id` | UUID |
| `workspace_id` | 首期使用固定默认 Workspace，保留隔离字段 |
| `name` | Agent 名称，同一 Workspace 内建议唯一 |
| `description` | 描述 |
| `owner` | 负责人标识 |
| `tags` | 标签数组 |
| `system_prompt` | 系统提示词 |
| `input_schema` | 输入 JSON Schema |
| `output_schema` | 可选输出 JSON Schema |
| `model_config` | 模型连接引用及温度、Token、超时配置 |
| `tool_bindings` | 工具版本引用数组，今天仅存储、不执行 |
| `execution_limits` | 步骤、工具次数、时长和 Token 上限 |
| `latest_version` | 最近发布版本号，无版本时为空 |
| `created_at` / `updated_at` | 审计时间 |

### 5.2 AgentVersion

版本记录包含 Agent 发布时的完整配置快照、递增版本号和发布时间。发布后只允许读取，不提供更新和删除 API。

### 5.3 今日必须验证的规则

- 创建和更新时校验输入、输出 JSON Schema 的基本合法性。
- 发布前必须存在非空系统提示词和模型连接引用。
- 发布时复制完整配置，不与草稿共享可变对象。
- 发布后修改草稿，不影响已有版本内容。
- 同一 Agent 的版本号严格递增。
- 不存在的资源返回 `404`；请求冲突返回 `409`；字段或发布校验失败返回 `422`。

## 6. API 交付清单

| 方法 | 路径 | 今日行为 |
| --- | --- | --- |
| `GET` | `/health` | 返回服务与数据库可用状态 |
| `POST` | `/v1/agents` | 创建 Agent 草稿 |
| `GET` | `/v1/agents` | 分页列出 Agent |
| `GET` | `/v1/agents/{agent_id}` | 查询 Agent 草稿及最近版本号 |
| `PATCH` | `/v1/agents/{agent_id}` | 局部更新草稿 |
| `POST` | `/v1/agents/{agent_id}/versions` | 校验并发布不可变快照 |
| `GET` | `/v1/agents/{agent_id}/versions` | 查询版本列表 |
| `GET` | `/v1/agents/{agent_id}/versions/{version}` | 查询指定版本 |

暂不提供删除接口，避免提前引入“被引用版本不可删除”的依赖治理问题。

## 7. 执行安排

### 09:30–10:30：工程基线

- 调整目录结构，创建应用工厂或统一应用入口。
- 增加配置管理、数据库 Session 和依赖注入。
- 加入 SQLAlchemy、Alembic、pytest 等依赖。
- 建立开发、测试环境的独立数据库配置。

产出：服务可启动，`GET /health` 可访问，测试框架可运行。

### 10:30–12:00：Schema 与持久化

- 定义 Agent、AgentVersion ORM 模型。
- 定义请求与响应 Pydantic Schema。
- 创建首个 Alembic migration。
- 处理 UUID、时间戳、JSON 字段和唯一约束。

产出：空数据库可由 migration 初始化，模型可写入和读取。

### 13:30–15:30：Agent 草稿 API

- 实现创建、列表、详情和局部更新。
- 增加分页参数和统一错误响应。
- 增加名称冲突、资源不存在及非法 Schema 校验。

产出：通过 API 完成 Agent 草稿的创建、查询和更新。

### 15:30–17:00：发布版本闭环

- 实现发布前校验。
- 在事务内计算版本号并保存完整快照。
- 实现版本列表和详情接口。
- 验证修改草稿不会污染已发布快照。

产出：完成“草稿 → 发布 → 继续编辑草稿”的核心闭环。

### 17:00–18:00：测试、文档与收尾

- 补齐 API 正常路径和关键失败路径测试。
- 更新 `.env.example`、启动命令和 HTTP 请求示例。
- 运行测试与静态检查，记录未完成项。
- 根据实际进度更新本计划末尾的执行记录。

产出：可复现的本地启动与验证说明，今日代码达到可交接状态。

## 8. 任务优先级

### P0：今日必须完成

- 工程分层与配置基线。
- Agent / AgentVersion 数据模型和 migration。
- Agent 草稿创建、查询、更新。
- 发布不可变版本及版本查询。
- 核心 API 自动化测试。

### P1：时间允许完成

- 列表分页及按名称、标签筛选。
- 统一错误码与请求关联 ID。
- Ruff 静态检查和格式化配置。
- OpenAPI 示例和 `test_main.http` 更新。

### P2：顺延至下一开发日

- ModelConnection 独立实体，不再只保存引用 ID。
- Tool / ToolVersion 注册与绑定校验。
- Run 状态机及异步执行骨架。
- 可移植的容器化本地环境说明（本机已复用 PostgreSQL 16 容器）。

若时间不足，按 P2 → P1 的顺序裁剪，不牺牲 P0 的测试和不可变版本规则。

## 9. 测试清单

至少覆盖以下场景：

1. 创建合法 Agent 返回 `201`，并可从详情接口读取。
2. 缺少必填字段或 JSON Schema 非法时返回 `422`。
3. 同一 Workspace 创建重名 Agent 返回 `409`。
4. 更新不存在的 Agent 返回 `404`。
5. 配置不完整时禁止发布并返回可定位字段的错误。
6. 首次和再次发布分别生成版本 `1`、`2`。
7. 发布版本后修改草稿，版本快照保持不变。
8. 版本详情不可通过 API 修改。
9. 列表分页边界符合约定。
10. 健康检查在数据库不可用时能反映异常状态。

## 10. 今日完成标准（Definition of Done）

- [x] Uvicorn 实际启动验证通过（冒烟使用 127.0.0.1:8011，开发命令见 README）。
- [x] 全新本地数据库可通过 Alembic migration 初始化。
- [x] API 清单中的 8 个接口均可通过 OpenAPI 或 HTTP 示例验证。
- [x] 自动化测试覆盖所有核心规则，16 项测试全部通过。
- [x] 已发布 AgentVersion 在草稿更新后保持不变。
- [x] `.env` 已被 Git 忽略且权限为 600；本次未创建 Git 提交。
- [x] README 说明启动、迁移、测试命令。
- [x] 已记录遗留问题和下一开发日的建议入口。

## 11. 风险与应对

| 风险 | 影响 | 今日应对 |
| --- | --- | --- |
| 过早设计完整平台模型 | 首日投入过大、接口反复调整 | 只实现 Agent 纵向切片，其他领域仅保留引用 |
| 开发库与测试库混用 | 测试数据污染 | 独立 PostgreSQL 测试库，测试库名必须以 `_test` 结尾 |
| JSON Schema 校验范围失控 | 占用过多时间 | 今日只校验 Schema 自身合法性，运行时输入校验后续实现 |
| 发布并发导致重复版本号 | 数据冲突 | 发布及编辑均锁定同一 Agent 行，并保留数据库唯一约束 |
| 模型配置结构过早固化 | 接入多供应商时返工 | 首日保存稳定公共字段与连接引用，供应商参数置于扩展配置 |

## 12. 下一开发日建议

在今天 P0 全部通过后，下一步进入 M1 的第二个纵向切片：

```text
注册 HTTP Tool → 发布 ToolVersion → Agent 绑定具体工具版本
→ 创建 Run → Worker 执行一次受控工具调用 → 记录基础 Trace
```

该切片应优先使用 Mock 模型或确定性测试执行器打通运行状态和工具安全边界，再接入真实模型，避免将基础调度问题与模型不确定性混在一起排查。

## 13. 执行记录

- 实际完成：P0 全部完成；另完成分页、名称筛选、统一错误响应、请求 ID、Ruff 配置、HTTP 示例和开发说明。
- 数据库：复用 Docker `xind-postgres`（PostgreSQL 16），新建 `ai_workbench`、`ai_workbench_test`，使用独立项目角色。两库均已完成初始迁移。
- 测试结果：真实 PostgreSQL 16 上 16 项集成测试通过（含 4 路并发发布）；Ruff 检查与格式检查通过；`alembic check` 无模型漂移；真实 HTTP `/health` 与 Agent 列表返回正常。开发库未写入演示 Agent。
- 重要决策：按用户要求全程采用 PostgreSQL；草稿编辑与发布使用同一行锁；版本复制完整配置；`model_config` 使用 Pydantic 字段别名；PATCH 嵌套对象整体替换。
- 未完成及原因：标签筛选顺延；模型和工具依赖存在性、权限校验依赖后续 Registry，目前仅校验配置结构和必要引用。版本发布尚不等于运行能力交付。
- 依赖提示：当前 Starlette 的 TestClient 存在 httpx 与 AnyIO 弃用提示，不影响本次测试通过；依赖升级时跟进。
- 下一步：实现 ModelConnection 与 ToolVersion，再进入 Run/Worker；负责人待团队分配。
