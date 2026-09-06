# AI Workbench 开发计划：Durable Runtime 最小闭环

- 日期：2026-09-06
- 对应 PRD：`docs/agent-workbench-prd.md` V0.1
- 当前阶段：M1「单 Agent 闭环」
- 今日主题：可恢复的确定性 Agent Runtime

## 1. 今日目标

打通以下纵向链路：

```text
选择已发布 AgentVersion → 创建 Run → 独立 Worker 领取
→ 输入校验 → Checkpoint → 确定性结果 → 事件与前端展示
```

Worker 异常退出后，另一个 Worker 应通过 PostgreSQL 租约接管同一个 Run，并从最新 Checkpoint 继续。

## 2. 交付范围

### 后端 P0

- Run、StepExecution、Checkpoint、RunEvent 数据模型和迁移。
- 创建、列表、详情、事件读取和取消五个 Runtime API。
- 同 Workspace 幂等键及请求指纹冲突检测。
- 独立、单并发 Worker；多个 Worker 可横向运行。
- `FOR UPDATE SKIP LOCKED` 领取、30 秒租约和 10 秒心跳。
- 租约过期恢复、已完成步骤跳过、恢复次数审计。
- 同一 thread 串行、不同 thread 可并行。
- 确定性错误立即失败，瞬时错误最多尝试三次。
- queued 直接取消、running 协作式取消、重复取消幂等。

### 前端

- Runs 列表、状态筛选及轮询刷新。
- 从 Runs 页面或 Agent 版本时间线创建 Run。
- Run 详情、输入、结果、错误与事件时间线。
- queued/running/cancelling Run 的取消入口。

## 3. 今日不做

- 真实模型或工具调用。
- SSE、LangChain、LangGraph。
- 人工审批和外部副作用幂等。
- Workflow、完整 Trace 和 Eval。
- 字段级敏感数据策略。

## 4. 状态机

```text
queued → running → succeeded
                 → failed

queued  → cancelled
running → cancelling → cancelled

running --租约过期--> queued
```

## 5. 验收标准

- [x] 只能针对已发布 AgentVersion 创建 Run。
- [x] 相同幂等请求只产生一个 Run，不同请求复用 Key 返回 409。
- [x] 两个 Worker 不会同时领取同一 Run。
- [x] Worker 崩溃后另一个 Worker接管原 Run。
- [x] 恢复后已完成步骤不重复执行。
- [x] queued 和 running Run 均可协作式取消。
- [x] 同一 thread 串行调度。
- [x] Runtime API、恢复和重试有 PostgreSQL 集成测试。
- [x] Runs 前端接入真实 API。
- [x] 前端 lint 和生产构建通过。

## 6. 后续入口

原计划的后续入口已于同日继续实施，以下补充范围不改变上文确定性阶段的历史记录。

## 7. 同日追加：受控模型与工具闭环

- [x] 真实 OpenAI-compatible Chat Completions 流式适配，保留显式确定性模式。
- [x] 固定运行配置快照，调用已绑定 ToolVersion，仅放行只读无审批工具。
- [x] 对话和工具响应 Checkpoint，已提交步骤恢复不重复调用。
- [x] 租约代次与过期检查，阻止旧 Worker 续租或写回；同 thread 并发领取互斥。
- [x] 步骤/工具/Token 预算、总时限和请求等待期间的取消。
- [x] SSE 历史补齐与游标恢复、Runs 模型输出和基础步骤 Trace。
- [x] PostgreSQL 全量回归、前端 lint 和生产构建。

仍不做人工审批、写工具、Workflow、完整 Span 树、Eval、费用定价和 Redis 调度。工具只读声明需要注册方保证；模型调用与工具调用仍可能在检查点前崩溃时重复。后续应先进行用户实际模型服务联调，再推进审批与外部副作用幂等设计。

## 8. 实现调整：采用 LangChain / LangGraph

按用户要求，真实生成改用 LangChain `ChatOpenAI`，删除手写模型 HTTP/SSE 实现；循环编排改用 LangGraph `StateGraph` 的模型/工具节点与条件路由。保留单 Worker 单并发、数据库租约、检查点事务、只读策略、预算、取消和前端事件接口。

检查点继续由已有 PostgreSQL 事务管理，不引入第二套独立提交的图持久化；在每个节点边界读取已提交状态恢复。SDK 重试关闭，平台统一处理重试计费；LangSmith 隐式追踪关闭。更新依赖和锁文件后须 `uv sync` 并重启进程，无新增数据库迁移。真实供应商联调仍需单独验收。
