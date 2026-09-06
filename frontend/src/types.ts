export type ModelConfig = {
  connection_id: string | null
  temperature: number
  max_output_tokens: number
  timeout_seconds: number
}

export type ExecutionLimits = {
  max_steps: number
  max_tool_calls: number
  timeout_seconds: number
  token_budget: number
}

export type Agent = {
  id: string
  workspace_id: string
  name: string
  description: string
  owner: string
  tags: string[]
  system_prompt: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown> | null
  model_config: ModelConfig
  tool_bindings: Array<{ tool_id: string; version: number }>
  execution_limits: ExecutionLimits
  latest_version: number | null
  created_at: string
  updated_at: string
}

export type AgentPage = { items: Agent[]; total: number; offset: number; limit: number }

export type Version = {
  id: string
  agent_id: string
  workspace_id: string
  version: number
  snapshot: Omit<Agent, 'id' | 'workspace_id' | 'latest_version' | 'created_at' | 'updated_at'>
  published_at: string
}

export type VersionPage = { items: Version[]; total: number; offset: number; limit: number }

export type AgentFormData = {
  name: string
  description: string
  owner: string
  tags: string
  systemPrompt: string
  connectionId: string
  temperature: string
  maxOutputTokens: string
  timeoutSeconds: string
  maxSteps: string
  maxToolCalls: string
  tokenBudget: string
  inputSchema: string
  outputSchema: string
  toolBindings: string[]
}

export type ModelConnection = {
  id: string
  workspace_id: string
  name: string
  provider: 'openai_compatible'
  model_name: string
  base_url: string
  credential_configured: boolean
  timeout_seconds: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export type ModelConnectionPage = {
  items: ModelConnection[]
  total: number
  offset: number
  limit: number
}

export type ModelConnectionFormData = {
  name: string
  modelName: string
  baseUrl: string
  apiKey: string
  timeoutSeconds: string
  enabled: boolean
}

export type ConnectionTestResult = {
  connection_id: string
  success: boolean
  code: string
  message: string
  latency_ms: number
  tested_at: string
}

export type ToolAuth = {
  type: 'none' | 'bearer' | 'header'
  header_name: string
}

export type HttpToolConfig = {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  endpoint: string
  allowed_hosts: string[]
  path_params: Record<string, string>
  query_params: Record<string, string>
  header_params: Record<string, string>
  body_mode: 'none' | 'json'
  auth: ToolAuth
  timeout_seconds: number
  response_max_bytes: number
  retry: { max_attempts: number; backoff_seconds: number; retry_statuses: number[] }
}

export type McpToolConfig = {
  transport: 'streamable_http'
  server_url: string
  remote_tool_name: string
  allowed_hosts: string[]
  auth: ToolAuth
  timeout_seconds: number
}

export type Tool = {
  id: string
  workspace_id: string
  name: string
  description: string
  owner: string
  tags: string[]
  tool_type: 'http' | 'mcp'
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown> | null
  config: HttpToolConfig | McpToolConfig
  risk_level: 'read' | 'write' | 'high'
  requires_approval: boolean
  enabled: boolean
  credential_configured: boolean
  latest_version: number | null
  created_at: string
  updated_at: string
}

export type ToolPage = { items: Tool[]; total: number; offset: number; limit: number }

export type ToolFormData = {
  name: string
  description: string
  owner: string
  tags: string
  toolType: 'http' | 'mcp'
  endpoint: string
  method: HttpToolConfig['method']
  remoteToolName: string
  allowedHosts: string
  pathParams: string
  queryParams: string
  headerParams: string
  bodyMode: 'none' | 'json'
  authType: ToolAuth['type']
  authHeaderName: string
  credential: string
  timeoutSeconds: string
  responseMaxBytes: string
  inputSchema: string
  outputSchema: string
  riskLevel: Tool['risk_level']
  requiresApproval: boolean
  enabled: boolean
}

export type ToolTestResult = {
  tool_id: string
  success: boolean
  code: string
  message: string
  output: unknown
  duration_ms: number
  tested_at: string
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'timed_out' | 'cancelling' | 'cancelled'

export type RunTarget = { type: 'agent'; id: string; version: number }

export type RunSummary = {
  execution_mode: 'model' | 'deterministic'
  usage: Record<string, number>
  id: string
  workspace_id: string
  target: RunTarget
  thread_id: string | null
  status: RunStatus
  execution_attempts: number
  recovery_count: number
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

export type Run = RunSummary & {
  input: Record<string, unknown>
  result: Record<string, unknown> | null
  error: { code: string; message: string } | null
  cancel_requested_at: string | null
}

export type RunPage = { items: RunSummary[]; total: number; offset: number; limit: number }

export type RunEvent = {
  id: string
  run_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  created_at: string
}

export type RunEventPage = {
  items: RunEvent[]
  next_cursor: number
  has_more: boolean
}

export type RunStep = {
  id: string
  step_key: string
  status: string
  attempt_count: number
  input_summary: Record<string, unknown> | null
  output_summary: Record<string, unknown> | null
  error_code: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}
