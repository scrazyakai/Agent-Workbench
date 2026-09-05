import type { Agent, AgentFormData, AgentPage, Version, VersionPage } from './types'

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
    tool_bindings: [],
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
}
