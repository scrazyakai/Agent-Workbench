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
