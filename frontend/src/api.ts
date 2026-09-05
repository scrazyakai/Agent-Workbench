import type {
  Agent, AgentFormData, AgentPage, ConnectionTestResult, ModelConnection,
  ModelConnectionFormData, ModelConnectionPage, Tool, ToolFormData, ToolPage,
  ToolTestResult, Version, VersionPage,
} from './types'

type ErrorBody = { error?: { message?: string; details?: Array<{ message?: string }> } }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    throw new ApiError(
      response.status,
      'API 返回了非 JSON 响应，请确认 FastAPI 已启动并重启前端开发服务以加载代理配置',
    )
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorBody
    const detail = body.error?.details?.[0]?.message
    throw new ApiError(response.status, detail || body.error?.message || `请求失败 (${response.status})`)
  }
  return response.json() as Promise<T>
}

function parseSchema(value: string, nullable = false) {
  if (!value.trim() && nullable) return null
  return JSON.parse(value || '{}') as Record<string, unknown>
}

export function toPayload(form: AgentFormData) {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    owner: form.owner.trim(),
    tags: form.tags.split(',').map((tag) => tag.trim()).filter(Boolean),
    system_prompt: form.systemPrompt,
    input_schema: parseSchema(form.inputSchema),
    output_schema: parseSchema(form.outputSchema, true),
    model_config: {
      connection_id: form.connectionId.trim() || null,
      temperature: Number(form.temperature),
      max_output_tokens: Number(form.maxOutputTokens),
      timeout_seconds: Number(form.timeoutSeconds),
    },
    tool_bindings: form.toolBindings.map(value => {
      const [tool_id, version] = value.split('@')
      return { tool_id, version: Number(version) }
    }),
    execution_limits: {
      max_steps: Number(form.maxSteps),
      max_tool_calls: Number(form.maxToolCalls),
      timeout_seconds: Number(form.timeoutSeconds),
      token_budget: Number(form.tokenBudget),
    },
  }
}

export const api = {
  listAgents: (params: URLSearchParams) => request<AgentPage>(`/v1/agents?${params}`),
  getAgent: (id: string) => request<Agent>(`/v1/agents/${id}`),
  createAgent: (form: AgentFormData) => request<Agent>('/v1/agents', {
    method: 'POST', body: JSON.stringify(toPayload(form)),
  }),
  updateAgent: (id: string, form: AgentFormData) => request<Agent>(`/v1/agents/${id}`, {
    method: 'PATCH', body: JSON.stringify(toPayload(form)),
  }),
  publishAgent: (id: string) => request<Version>(`/v1/agents/${id}/versions`, { method: 'POST' }),
  listVersions: (id: string) => request<VersionPage>(`/v1/agents/${id}/versions?limit=100`),
  listModelConnections: (params = new URLSearchParams({ limit: '100' })) =>
    request<ModelConnectionPage>(`/v1/model-connections?${params}`),
  createModelConnection: (form: ModelConnectionFormData) =>
    request<ModelConnection>('/v1/model-connections', {
      method: 'POST', body: JSON.stringify(toConnectionPayload(form, true)),
    }),
  updateModelConnection: (id: string, form: ModelConnectionFormData) =>
    request<ModelConnection>(`/v1/model-connections/${id}`, {
      method: 'PATCH', body: JSON.stringify(toConnectionPayload(form)),
    }),
  setModelConnectionEnabled: (id: string, enabled: boolean) =>
    request<ModelConnection>(`/v1/model-connections/${id}`, {
      method: 'PATCH', body: JSON.stringify({ enabled }),
    }),
  testModelConnection: (id: string) =>
    request<ConnectionTestResult>(`/v1/model-connections/${id}/test`, { method: 'POST' }),
  listTools: () => request<ToolPage>('/v1/tools?limit=100'),
  createTool: (form: ToolFormData) => request<Tool>('/v1/tools', {
    method: 'POST', body: JSON.stringify(toToolPayload(form)),
  }),
  updateTool: (id: string, form: ToolFormData) => request<Tool>(`/v1/tools/${id}`, {
    method: 'PATCH', body: JSON.stringify(toToolPayload(form)),
  }),
  setToolEnabled: (id: string, enabled: boolean) => request<Tool>(`/v1/tools/${id}`, {
    method: 'PATCH', body: JSON.stringify({ enabled }),
  }),
  publishTool: (id: string) => request(`/v1/tools/${id}/versions`, { method: 'POST' }),
  testTool: (id: string, argumentsValue: Record<string, unknown>) =>
    request<ToolTestResult>(`/v1/tools/${id}/test`, {
      method: 'POST', body: JSON.stringify({ arguments: argumentsValue }),
    }),
  discoverMcpTools: (id: string) => request<{ tools: Array<{ name: string; description: string }> }>(`/v1/tools/${id}/discover`, { method: 'POST' }),
}

function toConnectionPayload(form: ModelConnectionFormData, requireApiKey = false) {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    provider: 'openai_compatible',
    model_name: form.modelName.trim(),
    base_url: form.baseUrl.trim(),
    timeout_seconds: Number(form.timeoutSeconds),
    enabled: form.enabled,
  }
  if (requireApiKey || form.apiKey.trim()) payload.api_key = form.apiKey.trim()
  return payload
}

function parseMapping(value: string) {
  return JSON.parse(value || '{}') as Record<string, string>
}

function toToolPayload(form: ToolFormData) {
  const common: Record<string, unknown> = {
    name: form.name.trim(), description: form.description.trim(), owner: form.owner.trim() || 'unassigned',
    tags: form.tags.split(',').map(value => value.trim()).filter(Boolean), tool_type: form.toolType,
    input_schema: parseSchema(form.inputSchema), output_schema: parseSchema(form.outputSchema, true),
    risk_level: form.riskLevel, requires_approval: form.requiresApproval, enabled: form.enabled,
  }
  const auth = { type: form.authType, header_name: form.authHeaderName.trim() || 'Authorization' }
  const allowed_hosts = form.allowedHosts.split(',').map(value => value.trim()).filter(Boolean)
  common.config = form.toolType === 'http' ? {
    method: form.method, endpoint: form.endpoint.trim(), allowed_hosts,
    path_params: parseMapping(form.pathParams), query_params: parseMapping(form.queryParams),
    header_params: parseMapping(form.headerParams), body_mode: form.bodyMode, auth,
    timeout_seconds: Number(form.timeoutSeconds), response_max_bytes: Number(form.responseMaxBytes),
    retry: { max_attempts: 1, backoff_seconds: 0.25, retry_statuses: [502, 503, 504] },
  } : {
    transport: 'streamable_http', server_url: form.endpoint.trim(),
    remote_tool_name: form.remoteToolName.trim(), allowed_hosts, auth,
    timeout_seconds: Number(form.timeoutSeconds),
  }
  if (form.credential.trim()) common.credential = form.credential.trim()
  return common
}
