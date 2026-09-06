import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, ArrowRight, Bot, Box, Check, ChevronLeft, ChevronRight, CircleHelp,
  Clock3, Database, FileCode2, GitBranch, Globe2, KeyRound, LayoutDashboard,
  ListChecks, Menu, MoreHorizontal, Network, Play, Plus, Search, Settings,
  Sparkles, Tag, TestTube2, Users, Wrench, X, Zap,
} from 'lucide-react'
import { api, ApiError } from './api'
import type {
  Agent, AgentFormData, ConnectionTestResult, HttpToolConfig, McpToolConfig, ModelConnection,
  ModelConnectionFormData, Run, RunEvent, RunStatus, RunSummary, Tool, ToolFormData,
  ToolTestResult, Version,
} from './types'

const PAGE_SIZE = 6

type RoutePath = '/overview' | '/agents' | '/tools' | '/workflows' | '/runs' | '/evaluations' | '/settings' | '/help'

const routeMeta: Record<RoutePath, { label: string; eyebrow: string; title: string; description: string }> = {
  '/overview': { label: '概览', eyebrow: 'WORKSPACE OVERVIEW', title: '概览', description: '掌握当前工作区的构建与运行状态。' },
  '/agents': { label: 'Agents', eyebrow: 'AGENT BUILDER', title: 'Agents', description: '创建、配置并发布可靠的智能体。' },
  '/tools': { label: 'Tools', eyebrow: 'TOOL REGISTRY', title: 'Tools', description: '集中注册、测试和管理 Agent 可调用的工具。' },
  '/workflows': { label: 'Workflows', eyebrow: 'GRAPH BUILDER', title: 'Workflows', description: '编排可预测、可调试的多步骤协作流程。' },
  '/runs': { label: 'Runs', eyebrow: 'AGENT RUNTIME', title: 'Runs', description: '查看任务状态、执行进度和失败原因。' },
  '/evaluations': { label: 'Evaluations', eyebrow: 'QUALITY LOOP', title: 'Evaluations', description: '用固定数据集比较版本效果、延迟与成本。' },
  '/settings': { label: '设置', eyebrow: 'WORKSPACE SETTINGS', title: '设置', description: '管理工作区运行环境与基础配置。' },
  '/help': { label: '帮助中心', eyebrow: 'DOCUMENTATION', title: '帮助中心', description: '快速了解当前能力、接口和后续建设范围。' },
}

function currentRoute(): RoutePath {
  const path = window.location.pathname === '/' ? '/overview' : window.location.pathname
  return path in routeMeta ? path as RoutePath : '/overview'
}

const emptyForm: AgentFormData = {
  name: '', description: '', owner: '', tags: '', systemPrompt: '', connectionId: '',
  temperature: '0.7', maxOutputTokens: '1024', timeoutSeconds: '60', maxSteps: '20',
  maxToolCalls: '10', tokenBudget: '10000', inputSchema: '{\n  "type": "object"\n}',
  outputSchema: '',
  toolBindings: [],
}

function formFromAgent(agent: Agent): AgentFormData {
  return {
    name: agent.name, description: agent.description, owner: agent.owner,
    tags: agent.tags.join(', '), systemPrompt: agent.system_prompt,
    connectionId: agent.model_config.connection_id || '',
    temperature: String(agent.model_config.temperature),
    maxOutputTokens: String(agent.model_config.max_output_tokens),
    timeoutSeconds: String(agent.model_config.timeout_seconds),
    maxSteps: String(agent.execution_limits.max_steps),
    maxToolCalls: String(agent.execution_limits.max_tool_calls),
    tokenBudget: String(agent.execution_limits.token_budget),
    inputSchema: JSON.stringify(agent.input_schema, null, 2),
    outputSchema: agent.output_schema ? JSON.stringify(agent.output_schema, null, 2) : '',
    toolBindings: agent.tool_bindings.map(binding => `${binding.tool_id}@${binding.version}`),
  }
}

function relativeDate(value: string) {
  const diff = Date.now() - new Date(value).getTime()
  const minutes = Math.max(1, Math.floor(diff / 60000))
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

function Sidebar({ open, route, close, navigate }: { open: boolean; route: RoutePath; close: () => void; navigate: (path: RoutePath) => void }) {
  const items = [
    [LayoutDashboard, '概览', '/overview'], [Bot, 'Agents', '/agents'], [Box, 'Tools', '/tools'],
    [GitBranch, 'Workflows', '/workflows'], [Activity, 'Runs', '/runs'],
    [TestTube2, 'Evaluations', '/evaluations'],
  ] as const
  return <>
    {open && <button className="scrim" aria-label="关闭导航" onClick={close} />}
    <aside className={`sidebar ${open ? 'is-open' : ''}`}>
      <div className="brand"><span className="brand-mark"><Sparkles size={18} /></span><span>Workbench</span></div>
      <nav aria-label="主导航">
        <p className="nav-label">工作台</p>
        {items.map(([Icon, label, path]) => <button key={label} className={route === path ? 'nav-item active' : 'nav-item'} onClick={() => navigate(path)}>
          <Icon size={18} /><span>{label}</span>
        </button>)}
        <p className="nav-label nav-label-bottom">系统</p>
        <button className={route === '/settings' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('/settings')}><Settings size={18} /><span>设置</span></button>
        <button className={route === '/help' ? 'nav-item active' : 'nav-item'} onClick={() => navigate('/help')}><CircleHelp size={18} /><span>帮助中心</span></button>
      </nav>
      <div className="workspace-chip"><div className="avatar">AK</div><div><strong>Akai Workspace</strong><span>Developer</span></div><MoreHorizontal size={18} /></div>
    </aside>
  </>
}

function PageHeading({ route, action }: { route: RoutePath; action?: React.ReactNode }) {
  const meta = routeMeta[route]
  return <section className="page-heading"><div><span className="eyebrow">{meta.eyebrow}</span><h1>{meta.title}</h1><p>{meta.description}</p></div>{action}</section>
}

function ModuleCard({ icon: Icon, title, description, status, action }: { icon: typeof Bot; title: string; description: string; status: string; action: () => void }) {
  return <button className="module-card" onClick={action}><span className="module-icon"><Icon size={20} /></span><span><strong>{title}</strong><small>{description}</small></span><span className={status === '可用' ? 'module-status ready' : 'module-status'}>{status}</span><ArrowRight size={17} /></button>
}

function WorkspacePage({ route, agentTotal, connections, reloadConnections, tools, reloadTools, notify, navigate, runPreset, clearRunPreset }: { route: Exclude<RoutePath, '/agents'>; agentTotal: number; connections: ModelConnection[]; reloadConnections: () => Promise<void>; tools: Tool[]; reloadTools: () => Promise<void>; notify: (message: string, kind?: 'success' | 'error') => void; navigate: (path: RoutePath) => void; runPreset: { agentId: string; version: number } | null; clearRunPreset: () => void }) {
  const planned = () => notify('该模块的后端能力将在下一阶段接入', 'error')
  if (route === '/overview') return <>
    <PageHeading route={route} action={<button className="primary create-button" onClick={() => navigate('/agents')}><Plus size={18} />创建 Agent</button>} />
    <section className="overview-hero"><div><span>工作区状态</span><h2>Agent 与 Tool 定义管理已就绪</h2><p>模型连接、HTTP/MCP 工具、不可变版本和发布依赖校验已经接通。下一步进入 Run 与 Worker 执行链路。</p><button className="secondary" onClick={() => navigate('/agents')}>查看 Agents <ArrowRight size={16} /></button></div><div className="readiness"><strong>{agentTotal}</strong><span>Agent 定义</span><div><i /><small>PostgreSQL 已连接</small></div></div></section>
    <div className="section-title"><div><h2>平台模块</h2><p>按 PRD 纵向闭环逐步开放</p></div></div>
    <section className="module-grid">
      <ModuleCard icon={Bot} title="Agent Builder" description="草稿、配置与版本发布" status="可用" action={() => navigate('/agents')} />
      <ModuleCard icon={Wrench} title="Tool Registry" description="HTTP、MCP、版本与凭证" status="可用" action={() => navigate('/tools')} />
      <ModuleCard icon={Network} title="Workflow" description="顺序、分支和审批节点" status="待接入" action={() => navigate('/workflows')} />
      <ModuleCard icon={Play} title="Agent Runtime" description="异步运行与事件流" status="待接入" action={() => navigate('/runs')} />
    </section>
  </>

  if (route === '/tools') return <ToolsRegistry tools={tools} reload={reloadTools} notify={notify} />

  if (route === '/workflows') return <>
    <PageHeading route={route} action={<button className="primary create-button" onClick={planned}><Plus size={18} />新建 Workflow</button>} />
    <section className="workflow-preview"><div className="flow-node start"><span>START</span></div><i /><div className="flow-node"><Bot size={17} /><span>Agent</span></div><i /><div className="flow-node"><ListChecks size={17} /><span>Approval</span></div><i /><div className="flow-node end"><span>END</span></div></section>
    <EmptyModule icon={GitBranch} title="还没有 Workflow" description="后续支持顺序执行、条件分支、人工审批和带次数上限的审核循环。" action="了解 Agent 版本" onAction={() => navigate('/agents')} />
  </>

  if (route === '/runs') return <RunsPage notify={notify} preset={runPreset} clearPreset={clearRunPreset} />

  if (route === '/evaluations') return <>
    <PageHeading route={route} action={<button className="primary create-button" onClick={planned}><Plus size={18} />创建评测</button>} />
    <section className="summary-row"><div><Database /><span>数据集</span><strong>0</strong></div><div><TestTube2 /><span>评测任务</span><strong>0</strong></div><div><Check /><span>平均通过率</span><strong>—</strong></div></section>
    <EmptyModule icon={TestTube2} title="建立第一个回归数据集" description="导入 JSONL 样本后，可对固定 Agent 版本执行 Schema、字段断言和模型评测。" action="查看 Agents" onAction={() => navigate('/agents')} />
  </>

  if (route === '/settings') return <ModelConnectionsSettings connections={connections} reload={reloadConnections} notify={notify} />

  return <>
    <PageHeading route={route} />
    <section className="help-grid"><article><span>01</span><h2>创建 Agent</h2><p>填写提示词、模型连接引用和执行约束，先保存为草稿。</p><button onClick={() => navigate('/agents')}>打开 Agent Builder <ArrowRight size={15} /></button></article><article><span>02</span><h2>发布版本</h2><p>发布会复制完整配置快照，继续修改草稿不会影响历史版本。</p><button onClick={() => navigate('/agents')}>管理版本 <ArrowRight size={15} /></button></article><article><span>03</span><h2>调试 API</h2><p>FastAPI 自动生成 OpenAPI 文档，可直接检查当前可用接口。</p><a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">打开 API 文档 <ArrowRight size={15} /></a></article></section>
    <section className="notice-panel"><CircleHelp /><div><h2>当前交付边界</h2><p>Agents、模型连接及 HTTP/MCP Tools 已接通真实后端；Workflows、Runs 和 Evaluations 将按 PRD 后续里程碑实现。</p></div></section>
  </>
}

const emptyConnectionForm: ModelConnectionFormData = {
  name: '', modelName: '', baseUrl: 'https://api.openai.com/v1',
  apiKey: '', timeoutSeconds: '10', enabled: true,
}

function connectionForm(connection?: ModelConnection): ModelConnectionFormData {
  if (!connection) return emptyConnectionForm
  return {
    name: connection.name, modelName: connection.model_name, baseUrl: connection.base_url,
    apiKey: '', timeoutSeconds: String(connection.timeout_seconds),
    enabled: connection.enabled,
  }
}

function ModelConnectionsSettings({ connections, reload, notify }: { connections: ModelConnection[]; reload: () => Promise<void>; notify: (message: string, kind?: 'success' | 'error') => void }) {
  const [drawer, setDrawer] = useState<ModelConnection | null | undefined>(undefined)
  const [testing, setTesting] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, ConnectionTestResult>>({})
  const test = async (connection: ModelConnection) => {
    setTesting(connection.id)
    try {
      const result = await api.testModelConnection(connection.id)
      setResults(current => ({ ...current, [connection.id]: result }))
      notify(result.message, result.success ? 'success' : 'error')
    } catch (error) { notify(error instanceof Error ? error.message : '连接测试失败', 'error') }
    finally { setTesting(null) }
  }
  const toggle = async (connection: ModelConnection) => {
    try {
      await api.setModelConnectionEnabled(connection.id, !connection.enabled)
      await reload(); notify(connection.enabled ? '连接已停用' : '连接已启用')
    } catch (error) { notify(error instanceof Error ? error.message : '状态更新失败', 'error') }
  }
  return <>
    <PageHeading route="/settings" action={<button className="primary create-button" onClick={() => setDrawer(null)}><Plus size={18} />新建连接</button>} />
    <section className="settings-list settings-summary"><div className="settings-row"><span className="feature-icon"><Users /></span><div><h2>Workspace</h2><p>当前使用服务端固定 Workspace，连接按工作区隔离。</p></div><strong>default</strong></div><div className="settings-row"><span className="feature-icon"><KeyRound /></span><div><h2>凭证安全</h2><p>API Key 使用 AES-GCM 加密保存，接口与 Agent 快照均不会回显凭证。</p></div><span className="module-status ready">已启用</span></div></section>
    <section className="data-panel connections-panel"><header><div><h2>模型连接</h2><span>{connections.length} 个连接 · OpenAI 兼容协议</span></div></header>
      {connections.length ? <div className="connection-list">{connections.map(connection => <article className="connection-row" key={connection.id}>
        <span className="feature-icon"><Globe2 /></span><div className="connection-main"><div><strong>{connection.name}</strong><span className={connection.enabled ? 'status published' : 'status draft'}>{connection.enabled ? '已启用' : '已停用'}</span></div><p>{connection.model_name} · {connection.base_url}</p><small>{connection.credential_configured ? 'API Key 已配置' : 'API Key 未配置'}</small>{results[connection.id] && <em className={results[connection.id].success ? 'test-result success' : 'test-result failure'}>{results[connection.id].message} · {results[connection.id].latency_ms} ms</em>}</div>
        <div className="connection-actions"><button className="text-button" onClick={() => void test(connection)} disabled={testing === connection.id}><Zap size={15} />{testing === connection.id ? '测试中…' : '测试'}</button><button className="text-button" onClick={() => setDrawer(connection)}>编辑</button><button className="secondary" onClick={() => void toggle(connection)}>{connection.enabled ? '停用' : '启用'}</button></div>
      </article>)}</div> : <div className="empty-connections"><KeyRound /><h3>还没有模型连接</h3><p>创建连接后，Agent 才能选择模型并发布版本。</p></div>}
    </section>
    {drawer !== undefined && <ModelConnectionDrawer connection={drawer} close={() => setDrawer(undefined)} saved={async () => { await reload(); setDrawer(undefined) }} notify={notify} />}
  </>
}

function ModelConnectionDrawer({ connection, close, saved, notify }: { connection: ModelConnection | null; close: () => void; saved: () => Promise<void>; notify: (message: string, kind?: 'success' | 'error') => void }) {
  const [form, setForm] = useState(() => connectionForm(connection || undefined))
  const [busy, setBusy] = useState(false)
  const set = (key: keyof ModelConnectionFormData) => (event: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: event.target.value })
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true)
    try {
      if (connection) await api.updateModelConnection(connection.id, form)
      else await api.createModelConnection(form)
      await saved(); notify(connection ? '模型连接已更新' : '模型连接已创建')
    } catch (error) { notify(error instanceof Error ? error.message : '保存失败', 'error') }
    finally { setBusy(false) }
  }
  return <div className="drawer-layer"><button className="drawer-scrim" aria-label="关闭连接编辑器" onClick={close} /><section className="drawer connection-drawer"><header className="drawer-header"><div><span className="eyebrow">MODEL CONNECTION</span><h2>{connection ? `编辑 ${connection.name}` : '新建模型连接'}</h2></div><button className="icon-button" aria-label="关闭" onClick={close}><X /></button></header><form onSubmit={submit} className="agent-form"><section><h3>连接配置</h3><div className="form-grid"><label>名称<span>*</span><input value={form.name} onChange={set('name')} required /></label><label>供应商<input value="OpenAI Compatible" disabled /></label><label className="full">模型名称<span>*</span><input value={form.modelName} onChange={set('modelName')} placeholder="gpt-5" required /></label><label className="full">Base URL<span>*</span><input value={form.baseUrl} onChange={set('baseUrl')} required /></label><label className="full">API Key{!connection && <span>*</span>}<input type="password" autoComplete="new-password" value={form.apiKey} onChange={set('apiKey')} placeholder={connection ? '留空表示保留当前 API Key' : '请输入 API Key'} required={!connection} /><small>{connection ? '留空不会修改已保存的 API Key；输入新值将覆盖。' : '保存时使用 AES-GCM 加密，之后不会在界面或 API 中回显。'}</small></label><label>连接超时（秒）<input type="number" min="1" max="120" value={form.timeoutSeconds} onChange={set('timeoutSeconds')} required /></label><label className="checkbox-label"><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />启用连接</label></div></section><footer className="drawer-actions"><div className="spacer" /><button type="button" className="text-button" onClick={close}>取消</button><button className="primary" disabled={busy}>{busy ? '保存中…' : '保存连接'}</button></footer></form></section></div>
}

const emptyToolForm: ToolFormData = {
  name: '', description: '', owner: '', tags: '', toolType: 'http',
  endpoint: 'https://api.example.com/v1/action', method: 'POST', remoteToolName: '',
  allowedHosts: 'api.example.com', pathParams: '{}', queryParams: '{}', headerParams: '{}',
  bodyMode: 'json', authType: 'none', authHeaderName: 'Authorization', credential: '',
  timeoutSeconds: '15', responseMaxBytes: '1048576',
  inputSchema: '{\n  "type": "object",\n  "additionalProperties": false\n}',
  outputSchema: '', riskLevel: 'read', requiresApproval: false, enabled: true,
}

function toolForm(tool?: Tool): ToolFormData {
  if (!tool) return emptyToolForm
  const isHttp = tool.tool_type === 'http'
  const config = tool.config
  const auth = config.auth
  return {
    name: tool.name, description: tool.description, owner: tool.owner, tags: tool.tags.join(', '),
    toolType: tool.tool_type,
    endpoint: isHttp ? (config as HttpToolConfig).endpoint : (config as McpToolConfig).server_url,
    method: isHttp ? (config as HttpToolConfig).method : 'POST',
    remoteToolName: isHttp ? '' : (config as McpToolConfig).remote_tool_name,
    allowedHosts: config.allowed_hosts.join(', '),
    pathParams: JSON.stringify(isHttp ? (config as HttpToolConfig).path_params : {}, null, 2),
    queryParams: JSON.stringify(isHttp ? (config as HttpToolConfig).query_params : {}, null, 2),
    headerParams: JSON.stringify(isHttp ? (config as HttpToolConfig).header_params : {}, null, 2),
    bodyMode: isHttp ? (config as HttpToolConfig).body_mode : 'none',
    authType: auth.type, authHeaderName: auth.header_name, credential: '',
    timeoutSeconds: String(config.timeout_seconds),
    responseMaxBytes: String(isHttp ? (config as HttpToolConfig).response_max_bytes : 1048576),
    inputSchema: JSON.stringify(tool.input_schema, null, 2),
    outputSchema: tool.output_schema ? JSON.stringify(tool.output_schema, null, 2) : '',
    riskLevel: tool.risk_level, requiresApproval: tool.requires_approval, enabled: tool.enabled,
  }
}

function ToolsRegistry({ tools, reload, notify }: { tools: Tool[]; reload: () => Promise<void>; notify: (message: string, kind?: 'success' | 'error') => void }) {
  const [drawer, setDrawer] = useState<Tool | null | undefined>(undefined)
  const [testTarget, setTestTarget] = useState<Tool | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, ToolTestResult>>({})
  const publish = async (tool: Tool) => {
    setBusy(tool.id)
    try { await api.publishTool(tool.id); await reload(); notify(`已发布 ${tool.name} 新版本`) }
    catch (error) { notify(error instanceof Error ? error.message : '发布失败', 'error') }
    finally { setBusy(null) }
  }
  const toggle = async (tool: Tool) => {
    setBusy(tool.id)
    try { await api.setToolEnabled(tool.id, !tool.enabled); await reload(); notify(tool.enabled ? '工具已停用' : '工具已启用') }
    catch (error) { notify(error instanceof Error ? error.message : '状态更新失败', 'error') }
    finally { setBusy(null) }
  }
  const discover = async (tool: Tool) => {
    setBusy(tool.id)
    try { const result = await api.discoverMcpTools(tool.id); notify(`MCP Server 提供 ${result.tools.length} 个工具`) }
    catch (error) { notify(error instanceof Error ? error.message : 'MCP 发现失败', 'error') }
    finally { setBusy(null) }
  }
  return <>
    <PageHeading route="/tools" action={<button className="primary create-button" onClick={() => setDrawer(null)}><Plus size={18} />注册工具</button>} />
    <section className="feature-layout"><div className="feature-card"><div className="feature-icon"><Globe2 /></div><div><span className="status published">可用</span><h2>HTTP Tool</h2><p>固定目标、Schema 校验、凭证注入、SSRF 防护与响应限制。</p></div></div><div className="feature-card"><div className="feature-icon"><Network /></div><div><span className="status published">可用</span><h2>MCP Tool</h2><p>兼容远程 Streamable HTTP Server，支持发现及调用指定工具。</p></div></div></section>
    <section className="data-panel connections-panel"><header><div><h2>工具目录</h2><span>{tools.length} 个工具 · HTTP / MCP</span></div></header>
      {tools.length ? <div className="connection-list">{tools.map(tool => {
        const endpoint = tool.tool_type === 'http' ? (tool.config as HttpToolConfig).endpoint : (tool.config as McpToolConfig).server_url
        return <article className="connection-row" key={tool.id}><span className="feature-icon">{tool.tool_type === 'http' ? <Globe2 /> : <Network />}</span><div className="connection-main"><div><strong>{tool.name}</strong><span className={tool.enabled ? 'status published' : 'status draft'}>{tool.tool_type.toUpperCase()} · {tool.enabled ? '已启用' : '已停用'}</span></div><p>{endpoint}</p><small>{tool.latest_version ? `最新 v${tool.latest_version}` : '尚未发布'} · {tool.credential_configured ? '凭证已配置' : '无凭证'}</small>{results[tool.id] && <em className={results[tool.id].success ? 'test-result success' : 'test-result failure'}>{results[tool.id].message} · {results[tool.id].duration_ms} ms</em>}</div><div className="connection-actions"><button className="text-button" onClick={() => setTestTarget(tool)} disabled={busy === tool.id}><Zap size={15} />测试</button>{tool.tool_type === 'mcp' && <button className="text-button" onClick={() => void discover(tool)} disabled={busy === tool.id}>发现</button>}<button className="text-button" onClick={() => void publish(tool)} disabled={busy === tool.id}>发布</button><button className="text-button" onClick={() => setDrawer(tool)}>编辑</button><button className="secondary" onClick={() => void toggle(tool)} disabled={busy === tool.id}>{tool.enabled ? '停用' : '启用'}</button></div></article>
      })}</div> : <div className="empty-connections"><Wrench /><h3>还没有工具</h3><p>注册一个 HTTP API 或远程 MCP Tool，测试后发布不可变版本。</p></div>}
    </section>
    {drawer !== undefined && <ToolDrawer tool={drawer} close={() => setDrawer(undefined)} saved={async () => { await reload(); setDrawer(undefined) }} notify={notify} />}
    {testTarget && <ToolTestDrawer tool={testTarget} close={() => setTestTarget(null)} completed={result => setResults(current => ({ ...current, [testTarget.id]: result }))} notify={notify} />}
  </>
}

function ToolDrawer({ tool, close, saved, notify }: { tool: Tool | null; close: () => void; saved: () => Promise<void>; notify: (message: string, kind?: 'success' | 'error') => void }) {
  const [form, setForm] = useState<ToolFormData>(() => toolForm(tool || undefined))
  const [busy, setBusy] = useState(false)
  const set = (key: keyof ToolFormData) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setForm({ ...form, [key]: event.target.value })
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true)
    try {
      if (tool) await api.updateTool(tool.id, form); else await api.createTool(form)
      await saved(); notify(tool ? '工具草稿已更新' : '工具已注册')
    } catch (error) { notify(error instanceof SyntaxError ? 'Schema 或参数映射不是合法 JSON' : error instanceof Error ? error.message : '保存失败', 'error') }
    finally { setBusy(false) }
  }
  return <div className="drawer-layer"><button className="drawer-scrim" aria-label="关闭工具编辑器" onClick={close} /><section className="drawer"><header className="drawer-header"><div><span className="eyebrow">TOOL REGISTRY</span><h2>{tool ? `编辑 ${tool.name}` : '注册工具'}</h2></div><button className="icon-button" aria-label="关闭" onClick={close}><X /></button></header><form onSubmit={submit} className="agent-form">
    <section><h3>基本信息</h3><div className="form-grid"><label>名称<span>*</span><input value={form.name} onChange={set('name')} required /></label><label>类型<select value={form.toolType} onChange={event => setForm({ ...form, toolType: event.target.value as 'http' | 'mcp' })}><option value="http">HTTP</option><option value="mcp">MCP</option></select></label><label>负责人<input value={form.owner} onChange={set('owner')} /></label><label>标签<input value={form.tags} onChange={set('tags')} placeholder="查询, 业务" /></label><label className="full">描述<textarea value={form.description} onChange={set('description')} rows={3} /></label></div></section>
    <section><h3>{form.toolType === 'http' ? 'HTTP 请求' : 'MCP Server'}</h3><div className="form-grid"><label className="full">{form.toolType === 'http' ? 'Endpoint' : 'Server URL'}<span>*</span><input value={form.endpoint} onChange={set('endpoint')} required /></label><label className="full">允许的主机<span>*</span><input value={form.allowedHosts} onChange={set('allowedHosts')} placeholder="api.example.com" required /><small>逗号分隔；运行时还会拒绝私网、回环和保留地址。</small></label>{form.toolType === 'http' ? <><label>HTTP 方法<select value={form.method} onChange={set('method')}><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select></label><label>Body<select value={form.bodyMode} onChange={set('bodyMode')}><option value="json">完整输入作为 JSON</option><option value="none">无请求体</option></select></label><label className="full code-field">Query 映射<textarea value={form.queryParams} onChange={set('queryParams')} rows={3} spellCheck={false} /></label><label className="full code-field">Path 映射<textarea value={form.pathParams} onChange={set('pathParams')} rows={3} spellCheck={false} /></label><label className="full code-field">Header 映射<textarea value={form.headerParams} onChange={set('headerParams')} rows={3} spellCheck={false} /></label></> : <label className="full">远程工具名称<span>*</span><input value={form.remoteToolName} onChange={set('remoteToolName')} required /></label>}</div></section>
    <section><h3>凭证与策略</h3><div className="form-grid"><label>认证方式<select value={form.authType} onChange={set('authType')}><option value="none">无认证</option><option value="bearer">Bearer Token</option><option value="header">自定义 Header</option></select></label>{form.authType === 'header' && <label>认证 Header<input value={form.authHeaderName} onChange={set('authHeaderName')} required /></label>}<label className="full">凭证<input type="password" autoComplete="new-password" value={form.credential} onChange={set('credential')} placeholder={tool ? '留空保留现有凭证' : '按认证方式填写'} /><small>使用 AES-GCM 加密保存，API 和版本快照均不会回显。</small></label><label>超时（秒）<input type="number" min="1" max="300" value={form.timeoutSeconds} onChange={set('timeoutSeconds')} /></label>{form.toolType === 'http' && <label>响应大小上限（字节）<input type="number" min="1024" value={form.responseMaxBytes} onChange={set('responseMaxBytes')} /></label>}<label>风险等级<select value={form.riskLevel} onChange={event => { const riskLevel = event.target.value as ToolFormData['riskLevel']; setForm({ ...form, riskLevel, requiresApproval: riskLevel === 'high' || form.requiresApproval }) }}><option value="read">只读</option><option value="write">写入</option><option value="high">高风险</option></select></label><label className="checkbox-label"><input type="checkbox" checked={form.requiresApproval} onChange={event => setForm({ ...form, requiresApproval: event.target.checked })} disabled={form.riskLevel === 'high'} />需要审批</label><label className="checkbox-label"><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />启用工具</label></div></section>
    <section><h3>Schema</h3><div className="form-grid"><label className="full code-field">输入 JSON Schema<textarea value={form.inputSchema} onChange={set('inputSchema')} rows={6} spellCheck={false} /></label><label className="full code-field">输出 JSON Schema（可选）<textarea value={form.outputSchema} onChange={set('outputSchema')} rows={5} spellCheck={false} /></label></div></section>
    <footer className="drawer-actions"><div className="spacer" /><button type="button" className="text-button" onClick={close}>取消</button><button className="primary" disabled={busy}>{busy ? '保存中…' : '保存工具'}</button></footer>
  </form></section></div>
}

function ToolTestDrawer({ tool, close, completed, notify }: { tool: Tool; close: () => void; completed: (result: ToolTestResult) => void; notify: (message: string, kind?: 'success' | 'error') => void }) {
  const [input, setInput] = useState('{}')
  const [busy, setBusy] = useState(false)
  const run = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true)
    try {
      const result = await api.testTool(tool.id, JSON.parse(input) as Record<string, unknown>)
      completed(result); notify(result.message, result.success ? 'success' : 'error'); close()
    } catch (error) { notify(error instanceof SyntaxError ? '测试参数不是合法 JSON' : error instanceof Error ? error.message : '测试失败', 'error') }
    finally { setBusy(false) }
  }
  return <div className="drawer-layer"><button className="drawer-scrim" aria-label="关闭工具测试" onClick={close} /><section className="drawer connection-drawer"><header className="drawer-header"><div><span className="eyebrow">TOOL TEST</span><h2>测试 {tool.name}</h2></div><button className="icon-button" aria-label="关闭" onClick={close}><X /></button></header><form onSubmit={run} className="agent-form"><section><h3>调用参数</h3><div className="form-grid"><label className="full code-field">JSON 输入<textarea value={input} onChange={event => setInput(event.target.value)} rows={12} spellCheck={false} /></label></div></section><footer className="drawer-actions"><div className="spacer" /><button type="button" className="text-button" onClick={close}>取消</button><button className="primary" disabled={busy}>{busy ? '调用中…' : '执行测试'}</button></footer></form></section></div>
}

const runLabels: Record<RunStatus, string> = {
  queued: '排队中', running: '运行中', succeeded: '已成功', failed: '失败',
  cancelling: '取消中', cancelled: '已取消',
}

function RunsPage({ notify, preset, clearPreset }: { notify: (message: string, kind?: 'success' | 'error') => void; preset: { agentId: string; version: number } | null; clearPreset: () => void }) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [total, setTotal] = useState(0)
  const [status, setStatus] = useState<RunStatus | ''>('')
  const [loading, setLoading] = useState(true)
  const [createOpen, setCreateOpen] = useState(Boolean(preset))
  const [selected, setSelected] = useState<string | null>(null)
  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: '100' })
    if (status) params.set('status', status)
    try {
      const result = await api.listRuns(params)
      setRuns(result.items); setTotal(result.total)
    } catch (error) { notify(error instanceof Error ? error.message : '无法加载运行记录', 'error') }
    finally { setLoading(false) }
  }, [status, notify])
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 2000); return () => window.clearInterval(timer) }, [load])
  useEffect(() => { if (preset) setCreateOpen(true) }, [preset])
  const active = runs.filter(run => run.status === 'queued' || run.status === 'running' || run.status === 'cancelling').length
  const succeeded = runs.filter(run => run.status === 'succeeded').length
  const recovered = runs.reduce((sum, run) => sum + run.recovery_count, 0)
  return <>
    <PageHeading route="/runs" action={<button className="primary create-button" onClick={() => setCreateOpen(true)}><Plus size={18} />创建 Run</button>} />
    <section className="summary-row"><div><Activity /><span>活跃 Run</span><strong>{active}</strong></div><div><Check /><span>当前成功</span><strong>{succeeded}</strong></div><div><Clock3 /><span>恢复次数</span><strong>{recovered}</strong></div></section>
    <section className="toolbar run-toolbar"><label>状态<select value={status} onChange={event => setStatus(event.target.value as RunStatus | '')}><option value="">全部状态</option>{Object.entries(runLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><span className="result-count">{total} 个结果 · 每 2 秒刷新</span></section>
    <section className="data-panel runs-panel"><header><h2>最近运行</h2><span>PostgreSQL durable runtime</span></header>
      {loading ? <div className="state-panel"><div className="spinner" /><p>正在加载运行记录…</p></div> : runs.length ? <div className="run-list">{runs.map(run => <button className="run-row" key={run.id} onClick={() => setSelected(run.id)}><code>{run.id.slice(0, 8)}</code><span>Agent · v{run.target.version}</span><span className={`status run-status ${run.status}`}>{runLabels[run.status]}</span><span>{relativeDate(run.updated_at)}</span><ChevronRight size={16} /></button>)}</div> : <EmptyModule icon={Play} title="暂无运行记录" description="选择一个已发布 Agent 版本，创建第一个可恢复 Run。" action="创建 Run" onAction={() => setCreateOpen(true)} compact />}
    </section>
    {createOpen && <CreateRunDrawer preset={preset} close={() => { setCreateOpen(false); clearPreset() }} saved={run => { setCreateOpen(false); clearPreset(); setSelected(run.id); void load() }} notify={notify} />}
    {selected && <RunDetailDrawer runId={selected} close={() => setSelected(null)} changed={load} notify={notify} />}
  </>
}

function CreateRunDrawer({ preset, close, saved, notify }: { preset: { agentId: string; version: number } | null; close: () => void; saved: (run: Run) => void; notify: (message: string, kind?: 'success' | 'error') => void }) {
  const [agents, setAgents] = useState<Agent[]>([])
  const [versions, setVersions] = useState<Version[]>([])
  const [agentId, setAgentId] = useState(preset?.agentId || '')
  const [version, setVersion] = useState(preset ? String(preset.version) : '')
  const [input, setInput] = useState('{}')
  const [threadId, setThreadId] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.listAgents(new URLSearchParams({ limit: '100' })).then(result => {
      const published = result.items.filter(agent => agent.latest_version)
      setAgents(published)
      if (!agentId && published[0]) setAgentId(published[0].id)
    }).catch(error => notify(error instanceof Error ? error.message : '无法加载 Agent', 'error'))
  }, [agentId, notify])
  useEffect(() => {
    if (!agentId) { setVersions([]); return }
    api.listVersions(agentId).then(result => {
      setVersions(result.items)
      if (!result.items.some(item => String(item.version) === version)) setVersion(result.items[0] ? String(result.items[0].version) : '')
    }).catch(error => notify(error instanceof Error ? error.message : '无法加载版本', 'error'))
  }, [agentId, version, notify])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true)
    try {
      const value = JSON.parse(input) as Record<string, unknown>
      const run = await api.createRun({ target: { type: 'agent', id: agentId, version: Number(version) }, input: value, ...(threadId.trim() ? { thread_id: threadId.trim() } : {}), ...(key.trim() ? { idempotency_key: key.trim() } : {}) })
      notify('Run 已创建'); saved(run)
    } catch (error) { notify(error instanceof SyntaxError ? 'Run 输入不是合法 JSON' : error instanceof Error ? error.message : '创建失败', 'error') }
    finally { setBusy(false) }
  }
  return <div className="drawer-layer"><button className="drawer-scrim" aria-label="关闭 Run 创建器" onClick={close} /><section className="drawer connection-drawer"><header className="drawer-header"><div><span className="eyebrow">AGENT RUNTIME</span><h2>创建 Run</h2></div><button className="icon-button" aria-label="关闭" onClick={close}><X /></button></header><form onSubmit={submit} className="agent-form"><section><h3>执行目标</h3><div className="form-grid"><label className="full">已发布 Agent<select value={agentId} onChange={event => { setAgentId(event.target.value); setVersion('') }} required><option value="">请选择 Agent</option>{agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label><label>版本<select value={version} onChange={event => setVersion(event.target.value)} required><option value="">请选择版本</option>{versions.map(item => <option key={item.id} value={item.version}>v{item.version}</option>)}</select></label><label>Thread ID<input value={threadId} onChange={event => setThreadId(event.target.value)} placeholder="可选，同一 Thread 串行" /></label><label className="full">幂等键<input value={key} onChange={event => setKey(event.target.value)} placeholder="可选，重试创建时复用" /></label></div></section><section><h3>输入</h3><div className="form-grid"><label className="full code-field">JSON 输入<textarea value={input} onChange={event => setInput(event.target.value)} rows={10} spellCheck={false} /></label></div></section><footer className="drawer-actions"><div className="spacer" /><button type="button" className="text-button" onClick={close}>取消</button><button className="primary" disabled={busy || !agentId || !version}>{busy ? '创建中…' : '创建 Run'}</button></footer></form></section></div>
}

function RunDetailDrawer({ runId, close, changed, notify }: { runId: string; close: () => void; changed: () => Promise<void>; notify: (message: string, kind?: 'success' | 'error') => void }) {
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const load = useCallback(async () => {
    try { const [current, timeline] = await Promise.all([api.getRun(runId), api.getRunEvents(runId)]); setRun(current); setEvents(timeline.items) }
    catch (error) { notify(error instanceof Error ? error.message : '无法加载 Run', 'error') }
  }, [runId, notify])
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 2000); return () => window.clearInterval(timer) }, [load])
  const cancel = async () => {
    try { await api.cancelRun(runId); notify('已提交取消请求'); await load(); await changed() }
    catch (error) { notify(error instanceof Error ? error.message : '取消失败', 'error') }
  }
  return <div className="drawer-layer"><button className="drawer-scrim" aria-label="关闭 Run 详情" onClick={close} /><section className="drawer"><header className="drawer-header"><div><span className="eyebrow">RUN DETAILS</span><h2>{run ? `${runLabels[run.status]} · ${run.id.slice(0, 8)}` : '加载中…'}</h2></div><button className="icon-button" aria-label="关闭" onClick={close}><X /></button></header>{run && <div className="run-detail"><section className="run-meta"><div><span>Agent 版本</span><strong>v{run.target.version}</strong></div><div><span>恢复次数</span><strong>{run.recovery_count}</strong></div><div><span>执行失败次数</span><strong>{run.execution_attempts}</strong></div></section><section><div className="section-title"><div><h3>输入</h3></div></div><pre>{JSON.stringify(run.input, null, 2)}</pre></section>{(run.result || run.error) && <section><div className="section-title"><div><h3>{run.result ? '结果' : '错误'}</h3></div></div><pre>{JSON.stringify(run.result || run.error, null, 2)}</pre></section>}<section><div className="section-title"><div><h3>事件时间线</h3></div></div><div className="event-list">{events.map(event => <div key={event.id}><i /><span><strong>{event.event_type}</strong><small>#{event.sequence} · {new Date(event.created_at).toLocaleString('zh-CN')}</small></span></div>)}</div></section></div>}<footer className="drawer-actions">{run && ['queued', 'running', 'cancelling'].includes(run.status) && <button className="secondary" onClick={() => void cancel()}>取消 Run</button>}<div className="spacer" /><button className="primary" onClick={close}>关闭</button></footer></section></div>
}

function EmptyModule({ icon: Icon, title, description, action, onAction, compact = false }: { icon: typeof Bot; title: string; description: string; action: string; onAction: () => void; compact?: boolean }) {
  return <div className={`state-panel empty-state module-empty ${compact ? 'compact' : ''}`}><div className="empty-icon"><Icon /></div><h2>{title}</h2><p>{description}</p><button className="secondary" onClick={onAction}>{action}<ArrowRight size={15} /></button></div>
}

function AgentCard({ agent, select }: { agent: Agent; select: () => void }) {
  return <article className="agent-card" data-testid={`agent-${agent.id}`}>
    <div className="card-top"><div className="agent-icon"><Bot size={22} /></div><button className="icon-button" aria-label={`${agent.name} 更多操作`}><MoreHorizontal size={19} /></button></div>
    <button className="card-main" onClick={select}>
      <div className="title-row"><h2>{agent.name}</h2><span className={agent.latest_version ? 'status published' : 'status draft'}>{agent.latest_version ? '已发布' : '草稿'}</span></div>
      <p>{agent.description || '暂未添加描述，点击打开并完善这个 Agent 的用途。'}</p>
      <div className="tag-row">{agent.tags.slice(0, 3).map(tag => <span key={tag}><Tag size={12} />{tag}</span>)}{agent.tags.length === 0 && <span className="muted-tag">无标签</span>}</div>
    </button>
    <div className="card-footer"><span><Clock3 size={14} />{relativeDate(agent.updated_at)}</span><span className="version-badge">{agent.latest_version ? `v${agent.latest_version}` : '未发布'}</span></div>
  </article>
}

type DrawerProps = {
  agent: Agent | null; mode: 'create' | 'edit'; close: () => void;
  connections: ModelConnection[]; tools: Tool[]; saved: (agent: Agent) => void; notify: (message: string, kind?: 'success' | 'error') => void;
  runVersion: (agentId: string, version: number) => void
}

function AgentDrawer({ agent, mode, close, connections, tools, saved, notify, runVersion }: DrawerProps) {
  const [form, setForm] = useState<AgentFormData>(() => agent ? formFromAgent(agent) : emptyForm)
  const [tab, setTab] = useState<'config' | 'versions'>('config')
  const [versions, setVersions] = useState<Version[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (tab === 'versions' && agent) api.listVersions(agent.id).then(r => setVersions(r.items)).catch(e => notify(e.message, 'error'))
  }, [tab, agent, notify])

  const set = (key: keyof AgentFormData) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setForm({ ...form, [key]: event.target.value })
  const currentConnection = connections.find(connection => connection.id === form.connectionId)
  const legacyConnection = form.connectionId && !currentConnection
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true)
    try {
      const result = mode === 'create' ? await api.createAgent(form) : await api.updateAgent(agent!.id, form)
      saved(result); notify(mode === 'create' ? 'Agent 草稿已创建' : '草稿已保存'); close()
    } catch (error) {
      notify(error instanceof SyntaxError ? 'JSON Schema 格式不正确' : error instanceof ApiError ? error.message : '保存失败', 'error')
    } finally { setBusy(false) }
  }
  const publish = async () => {
    if (!agent) return
    setBusy(true)
    try {
      await api.publishAgent(agent.id)
      const updated = await api.getAgent(agent.id)
      saved(updated); notify(`已发布 v${updated.latest_version}`); close()
    } catch (error) { notify(error instanceof Error ? error.message : '发布失败', 'error') }
    finally { setBusy(false) }
  }

  return <div className="drawer-layer"><button className="drawer-scrim" aria-label="关闭编辑器" onClick={close} />
    <section className="drawer" aria-label={mode === 'create' ? '创建 Agent' : `编辑 ${agent?.name}`}>
      <header className="drawer-header"><div><span className="eyebrow">{mode === 'create' ? 'NEW AGENT' : 'AGENT BUILDER'}</span><h2>{mode === 'create' ? '创建 Agent 草稿' : agent?.name}</h2></div><button className="icon-button" aria-label="关闭" onClick={close}><X /></button></header>
      {mode === 'edit' && <div className="tabs"><button className={tab === 'config' ? 'active' : ''} onClick={() => setTab('config')}>配置</button><button className={tab === 'versions' ? 'active' : ''} onClick={() => setTab('versions')}>版本 {agent?.latest_version ? `(${agent.latest_version})` : ''}</button></div>}
      {tab === 'config' ? <form onSubmit={submit} className="agent-form">
        <section><h3>基本信息</h3><div className="form-grid"><label>名称<span>*</span><input aria-label="名称" value={form.name} onChange={set('name')} placeholder="例如：运营数据助手" required /></label><label>负责人<input aria-label="负责人" value={form.owner} onChange={set('owner')} placeholder="团队成员或邮箱" /></label><label className="full">描述<textarea aria-label="描述" value={form.description} onChange={set('description')} placeholder="说明这个 Agent 解决什么问题" rows={3} /></label><label className="full">标签<input aria-label="标签" value={form.tags} onChange={set('tags')} placeholder="数据, 运营（逗号分隔）" /></label></div></section>
        <section><h3>模型与提示词</h3><div className="form-grid"><label className="full">模型连接<select aria-label="模型连接" value={form.connectionId} onChange={set('connectionId')}><option value="">请选择已启用的模型连接</option>{legacyConnection && <option value={form.connectionId}>原引用不存在 · {form.connectionId}</option>}{connections.map(connection => <option key={connection.id} value={connection.id} disabled={!connection.enabled}>{connection.name} · {connection.model_name}{connection.enabled ? '' : '（已停用）'}</option>)}</select>{(legacyConnection || (currentConnection && !currentConnection.enabled)) && <small className="field-warning">当前连接不可用于发布，请重新选择。</small>}</label><label>温度<input aria-label="温度" type="number" min="0" max="2" step="0.1" value={form.temperature} onChange={set('temperature')} /></label><label>最大输出 Token<input aria-label="最大输出 Token" type="number" min="1" value={form.maxOutputTokens} onChange={set('maxOutputTokens')} /></label><label className="full">系统提示词<span>*</span><textarea aria-label="系统提示词" value={form.systemPrompt} onChange={set('systemPrompt')} placeholder="描述 Agent 的角色、任务和边界…" rows={7} /></label></div></section>
        <section><h3>工具绑定</h3><div className="tool-binding-list">{tools.filter(tool => tool.latest_version).length ? tools.filter(tool => tool.latest_version).map(tool => { const value = `${tool.id}@${tool.latest_version}`; const checked = form.toolBindings.includes(value); return <label key={tool.id} className={!tool.enabled ? 'disabled' : ''}><input type="checkbox" checked={checked} disabled={!tool.enabled} onChange={event => setForm({ ...form, toolBindings: event.target.checked ? [...form.toolBindings, value] : form.toolBindings.filter(item => item !== value) })} /><span><strong>{tool.name}</strong><small>{tool.tool_type.toUpperCase()} · v{tool.latest_version}{tool.enabled ? '' : ' · 已停用'}</small></span></label> }) : <p>还没有已发布的工具，请先前往 Tools 注册并发布。</p>}</div></section>
        <section><h3>执行约束</h3><div className="form-grid three"><label>最大步骤<input aria-label="最大步骤" type="number" min="1" value={form.maxSteps} onChange={set('maxSteps')} /></label><label>工具调用上限<input aria-label="工具调用上限" type="number" min="0" value={form.maxToolCalls} onChange={set('maxToolCalls')} /></label><label>运行超时（秒）<input aria-label="运行超时" type="number" min="1" value={form.timeoutSeconds} onChange={set('timeoutSeconds')} /></label><label>Token 预算<input aria-label="Token 预算" type="number" min="1" value={form.tokenBudget} onChange={set('tokenBudget')} /></label></div></section>
        <section><h3>输入与输出</h3><div className="form-grid"><label className="full code-field">输入 JSON Schema<textarea aria-label="输入 JSON Schema" value={form.inputSchema} onChange={set('inputSchema')} rows={5} spellCheck={false} /></label><label className="full code-field">输出 JSON Schema（可选）<textarea aria-label="输出 JSON Schema" value={form.outputSchema} onChange={set('outputSchema')} rows={5} spellCheck={false} placeholder={'{\n  "type": "object"\n}'} /></label></div></section>
        <footer className="drawer-actions">{mode === 'edit' && <button type="button" className="secondary" onClick={publish} disabled={busy}><Zap size={16} />发布新版本</button>}<div className="spacer" /><button type="button" className="text-button" onClick={close}>取消</button><button className="primary" disabled={busy}>{busy ? '处理中…' : mode === 'create' ? '创建草稿' : '保存草稿'}</button></footer>
      </form> : <div className="version-panel">{versions.length ? versions.map(v => <div className="version-row" key={v.id}><div className="timeline-dot"><Check size={14} /></div><div><strong>版本 v{v.version}</strong><p>{new Date(v.published_at).toLocaleString('zh-CN')}</p><small>{v.snapshot.model_config.connection_id || '未设置模型连接'} · {v.snapshot.system_prompt.slice(0, 46)}{v.snapshot.system_prompt.length > 46 ? '…' : ''}</small></div><button className="secondary" onClick={() => runVersion(v.agent_id, v.version)}><Play size={14} />运行</button></div>) : <div className="empty-versions"><FileCode2 /><h3>还没有发布版本</h3><p>配置完整后发布第一个不可变版本。</p></div>}</div>}
    </section>
  </div>
}

export default function App() {
  const [route, setRoute] = useState<RoutePath>(currentRoute)
  const [agents, setAgents] = useState<Agent[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [name, setName] = useState('')
  const [tag, setTag] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [drawer, setDrawer] = useState<{ mode: 'create' | 'edit'; agent: Agent | null } | null>(null)
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' } | null>(null)
  const [menu, setMenu] = useState(false)
  const [connections, setConnections] = useState<ModelConnection[]>([])
  const [tools, setTools] = useState<Tool[]>([])
  const [runPreset, setRunPreset] = useState<{ agentId: string; version: number } | null>(null)

  useEffect(() => {
    const handlePopState = () => setRoute(currentRoute())
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const navigate = useCallback((path: RoutePath) => {
    if (window.location.pathname !== path) window.history.pushState({}, '', path)
    setRoute(path)
    setMenu(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  const notify = useCallback((message: string, kind: 'success' | 'error' = 'success') => {
    setToast({ message, kind }); window.setTimeout(() => setToast(null), 3200)
  }, [])
  const params = useMemo(() => {
    const value = new URLSearchParams({ offset: String(page * PAGE_SIZE), limit: String(PAGE_SIZE) })
    if (name.trim()) value.set('name', name.trim())
    if (tag.trim()) value.set('tag', tag.trim())
    return value
  }, [name, tag, page])
  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { const result = await api.listAgents(params); setAgents(result.items); setTotal(result.total) }
    catch (e) { setError(e instanceof Error ? e.message : '无法加载 Agent') }
    finally { setLoading(false) }
  }, [params])
  useEffect(() => { void load() }, [load])
  const loadConnections = useCallback(async () => {
    const result = await api.listModelConnections()
    setConnections(result.items)
  }, [])
  useEffect(() => {
    void loadConnections().catch(error => {
      setConnections([])
      notify(error instanceof Error ? error.message : '无法加载模型连接', 'error')
    })
  }, [loadConnections, notify])
  const loadTools = useCallback(async () => {
    const result = await api.listTools()
    setTools(result.items)
  }, [])
  useEffect(() => {
    void loadTools().catch(error => {
      setTools([])
      notify(error instanceof Error ? error.message : '无法加载工具', 'error')
    })
  }, [loadTools, notify])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const published = agents.filter(agent => agent.latest_version).length
  return <div className="app-shell">
    <Sidebar open={menu} route={route} close={() => setMenu(false)} navigate={navigate} />
    <main className="main-content">
      <header className="topbar"><button className="mobile-menu" aria-label="打开导航" onClick={() => setMenu(true)}><Menu /></button><div className="breadcrumbs"><span>工作台</span><ChevronRight size={14} /><strong>{routeMeta[route].label}</strong></div><div className="top-actions"><button className="icon-button notification" aria-label="运行通知" onClick={() => navigate('/runs')}><Activity size={18} /><i /></button><span className="environment"><i />开发环境</span></div></header>
      <div className="content-wrap">
        {route === '/agents' ? <>
          <PageHeading route={route} action={<button className="primary create-button" onClick={() => setDrawer({ mode: 'create', agent: null })}><Plus size={18} />新建 Agent</button>} />
          <section className="stats" aria-label="Agent 概览"><div><span>全部 Agent</span><strong>{total}</strong><small>当前筛选结果</small></div><div><span>当前页已发布</span><strong>{published}</strong><small><i className="green-dot" />已有版本</small></div><div><span>当前页草稿</span><strong>{Math.max(0, agents.length - published)}</strong><small>尚未发布</small></div></section>
          <section className="toolbar"><div className="search-box"><Search size={18} /><input aria-label="搜索 Agent" value={name} onChange={e => { setName(e.target.value); setPage(0) }} placeholder="搜索 Agent 名称…" />{name && <button aria-label="清除搜索" onClick={() => setName('')}><X size={15} /></button>}</div><div className="tag-filter"><Tag size={16} /><input aria-label="按标签筛选" value={tag} onChange={e => { setTag(e.target.value); setPage(0) }} placeholder="筛选标签" /></div><span className="result-count">{total} 个结果</span></section>
          {loading ? <div className="state-panel"><div className="spinner" /><p>正在加载 Agent…</p></div> : error ? <div className="state-panel error-state"><Activity /><h2>连接服务失败</h2><p>{error}</p><button className="secondary" onClick={load}>重新加载</button></div> : agents.length ? <div className="agent-grid">{agents.map(agent => <AgentCard key={agent.id} agent={agent} select={() => setDrawer({ mode: 'edit', agent })} />)}</div> : <div className="state-panel empty-state"><div className="empty-icon"><Bot /></div><h2>{name || tag ? '没有匹配的 Agent' : '创建你的第一个 Agent'}</h2><p>{name || tag ? '试试调整名称或标签筛选条件。' : '从一份可编辑草稿开始，完成配置后发布不可变版本。'}</p>{!name && !tag && <button className="primary" onClick={() => setDrawer({ mode: 'create', agent: null })}><Plus size={18} />创建 Agent</button>}</div>}
          {total > PAGE_SIZE && <nav className="pagination" aria-label="分页"><button className="icon-button" aria-label="上一页" disabled={page === 0} onClick={() => setPage(page - 1)}><ChevronLeft /></button><span>第 {page + 1} / {pages} 页</span><button className="icon-button" aria-label="下一页" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}><ChevronRight /></button></nav>}
        </> : <WorkspacePage route={route} agentTotal={total} connections={connections} reloadConnections={loadConnections} tools={tools} reloadTools={loadTools} notify={notify} navigate={navigate} runPreset={runPreset} clearRunPreset={() => setRunPreset(null)} />}
      </div>
    </main>
    {drawer && <AgentDrawer mode={drawer.mode} agent={drawer.agent} connections={connections} tools={tools} close={() => setDrawer(null)} saved={() => void load()} notify={notify} runVersion={(agentId, version) => { setDrawer(null); setRunPreset({ agentId, version }); navigate('/runs') }} />}
    {toast && <div role="status" className={`toast ${toast.kind}`}><span>{toast.kind === 'success' ? <Check size={16} /> : <X size={16} />}</span>{toast.message}</div>}
  </div>
}
