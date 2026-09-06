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

下一步将确定性 `produce_result` 替换为受预算约束的模型调用循环，再通过已发布 ToolVersion 接入工具调用。事件契约、StepExecution 和 Checkpoint 保持不变，随后增加 SSE 投递层与基础 Trace 展示。
