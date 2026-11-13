/** Types mirroring the backend Pydantic schemas. */

export type StepType = 'plan' | 'code' | 'test' | 'review' | 'custom' | 'shell' | 'condition' | 'data_transform' | 'http_request' | 'loop' | 'merge' | 'template' | 'output' | 'approval'
export const STEP_TYPES: readonly StepType[] = ['plan', 'code', 'test', 'review', 'custom', 'shell', 'condition', 'data_transform', 'http_request', 'loop', 'merge', 'template', 'output', 'approval']
/** Node types whose outgoing edges carry a handle. */
export const BRANCH_HANDLES: Partial<Record<StepType, [string, string]>> = { condition: ['true', 'false'], loop: ['body', 'done'] }

export type ToolName = string
/** Default tool set for new steps (classic code tools). */
export const ALL_TOOLS: readonly ToolName[] = ['read_file', 'write_file', 'run_shell', 'list_files', 'git_diff']

export interface StepData {
  label: string
  type: StepType
  system_prompt: string
  tools_allowed: ToolName[]
  max_turns: number
  on_fail: string // 'retry' | 'stop' | `goto:${string}`
  retry_limit: number
  command: string | null
  expression: string | null
  model_id?: string | null
  fallback_model_id?: string | null
  language?: string | null
  code?: string | null
  url?: string | null
  method?: string | null
  headers?: Record<string, string> | null
  body?: string | null
  items_expression?: string | null
  merge_steps?: string[] | null
  merge_mode?: string | null
  template?: string | null
  output_path?: string | null
  output_kind?: string | null
  output_source?: string | null
  deliver_tool?: string | null
  deliver_args?: Record<string, unknown> | null
  /** approval: seconds to wait for a decision (default 24h). */
  approval_timeout_seconds?: number | null
  /** Skills whose SKILL.md is added to the prompt and whose files are mounted at /skills/<name>/. */
  skill_ids?: string[] | null
  [key: string]: unknown
}

export interface FlowNode {
  id: string
  type: string
  position: { x: number; y: number }
  data: StepData
  [key: string]: unknown
}

export interface FlowEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  label?: string | null
  [key: string]: unknown
}

export type FlowVisibility = 'private' | 'shared_link'

export type InputType = 'text' | 'textarea' | 'number' | 'url' | 'select' | 'file' | 'boolean'
export const INPUT_TYPES: readonly InputType[] = ['text', 'textarea', 'number', 'url', 'select', 'file', 'boolean']

export interface InputField {
  name: string
  label: string
  type: InputType
  required: boolean
  default: string | number | boolean | null
  options: string[]
  placeholder: string
  description: string
}

export type SandboxNetwork = 'none' | 'allowlist' | 'bridge'

export interface SandboxSettings {
  cpus?: number | null
  memory_mb?: number | null
  timeout_seconds?: number | null
  network?: SandboxNetwork | null
  allowed_hosts?: string[]
}

export interface Flow {
  id: string
  name: string
  description: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  input_schema: InputField[]
  sandbox: SandboxSettings | null
  visibility: FlowVisibility
  share_token: string | null
  created_at: string
  updated_at: string
}

export interface SharedFlow {
  name: string
  description: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  input_schema: InputField[]
  updated_at: string
}

export interface GraphReport {
  errors: string[]
  warnings: string[]
}

export interface Template {
  id: string
  key: string | null
  name: string
  description: string
  builtin: boolean
  node_count: number
  nodes: FlowNode[]
  edges: FlowEdge[]
  input_schema: InputField[]
  sandbox: SandboxSettings | null
  /** {node_id: [skill names]} attached when the template is used. */
  skills?: Record<string, string[]>
  rules?: { name: string; scope: string; step_id?: string | null }[]
  created_at: string
}

export interface SkillFile {
  path: string
  size: number
  content_type: string
}

export type SkillKind = 'skill' | 'persona'

export interface Skill {
  id: string
  name: string
  kind: SkillKind
  description: string
  content: string
  tools_required: string[]
  files: SkillFile[]
  builtin: boolean
  skill_md: string
  created_at: string
  updated_at: string
}

export interface SkillInput {
  name: string
  kind: SkillKind
  description: string
  content: string
  tools_required: string[]
}

export type RuleScope = 'global' | 'flow' | 'step'

export interface Rule {
  id: string
  key: string | null
  name: string
  content: string
  scope: RuleScope
  flow_id: string | null
  step_id: string | null
  priority: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface RuleInput {
  name: string
  content: string
  scope: RuleScope
  flow_id?: string | null
  step_id?: string | null
  priority: number
  enabled: boolean
}

export interface PromptSection {
  kind: 'base' | 'rule' | 'persona' | 'skill' | 'step'
  name: string
  text: string
}

export interface ResolvedPrompt {
  step_id: string
  system_prompt: string
  sections: PromptSection[]
}

export interface User {
  id: string
  email: string
  is_admin?: boolean
  current_workspace_id?: string | null
  created_at: string
}

export interface Branch {
  id: string
  name: string
  address: string
  phone: string
}

export interface Workspace {
  id: string
  name: string
  industry: string
  industry_label: string
  timezone: string
  branches: Branch[]
  onboarding: Record<string, unknown>
  role: 'owner' | 'staff' | string
  created_at: string
}

export interface WorkspaceMember {
  id: string
  user_id: string
  email: string
  role: string
  created_at: string
}

export interface OnboardingQuestion {
  key: string
  label: string
  type: 'select' | 'number' | 'text'
  options: { value: string; label: string }[]
  default: unknown
  required: boolean
  placeholder: string
}

export interface OnboardingQuestions {
  questions: OnboardingQuestion[]
  industries: { key: string; label: string; description: string }[]
}

export interface OnboardingAnswers {
  name: string
  industry: string
  branch_count: number
  main_channel: 'zalo' | 'facebook' | 'both'
  current_software: string
}

export interface ApprovalDecision {
  decision: 'approve' | 'reject'
  comment?: string
}

export interface StepTryRequest {
  node?: FlowNode
  task?: string
  prev_output?: string
  prev_success?: boolean
  prev_exit_code?: number | null
  repo?: string
  branch?: string
}

export interface StepTryResult {
  step_id: string
  type: StepType
  success: boolean
  output: string
  error: string | null
  tokens: number
  system_prompt: string
  user_message: string
}

export interface FlowSummary {
  id: string
  name: string
  description: string
  node_count: number
  created_at: string
  updated_at: string
}

export interface FlowInput {
  name: string
  description: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  input_schema?: InputField[]
  sandbox?: SandboxSettings | null
}

export type RunStatus = 'queued' | 'running' | 'waiting' | 'success' | 'failed' | 'cancelled'
export type StepRunStatus = 'pending' | 'running' | 'waiting' | 'success' | 'failed' | 'skipped'

export interface Run {
  id: string
  flow_id: string
  repo_url: string
  branch: string
  task_input: string
  status: RunStatus
  error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  cost_tokens: number
  cost_usd: number | null
  work_branch: string | null
  pr_url: string | null
  inputs: Record<string, unknown> | null
  trigger_id: string | null
  outputs: RunOutput[] | null
  sandbox: SandboxSettings | null
}

export interface Artifact {
  id: string
  run_id: string
  name: string
  size: number
  content_type: string
  created_at: string
}

export interface RunOutput {
  name: string
  kind: 'text' | 'json' | 'file'
  step_id: string
  value?: unknown
  path?: string
  delivered?: { tool: string; result: string }
}

export interface RunDetail extends Run {
  final_diff: string | null
}

export interface StepRun {
  id: string
  run_id: string
  step_id: string
  step_type: string
  attempt: number
  status: StepRunStatus
  messages: unknown[]
  tool_calls: ToolCallRecord[]
  output: string
  duration: number
  tokens: number
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number | null
  /** Personas/skills applied to this step. */
  skills?: { name: string; kind: string }[] | null
  started_at: string
  finished_at: string | null
}

export interface ToolCallRecord {
  turn: number
  tool: string
  input: Record<string, unknown>
  output: string
  is_error: boolean
  duration: number
}

/** Events sent over `WS /runs/{id}/logs`. */
export interface LogEvent {
  event: 'run_status' | 'step_start' | 'step_end' | 'text' | 'tool_call' | 'tool_result' | 'log' | 'variables' | 'output' | 'artifacts' | 'approval_requested' | 'approval_resolved' | 'end'
  ts?: string
  step_id?: string
  step_run_id?: string
  status?: string
  error?: string | null
  type?: string
  attempt?: number
  label?: string
  text?: string
  tool?: string
  input?: Record<string, unknown>
  output?: string
  is_error?: boolean
  duration?: number
  message?: string
  level?: string
  replayed?: boolean
  [key: string]: unknown
}


export type KeyProvider = 'anthropic' | 'openai' | 'github' | 'other' | 'tavily' | 'smtp' | 'slack' | 'telegram' | 'notion' | 'google' | 'database'
export const KEY_PROVIDERS: readonly KeyProvider[] = ['anthropic', 'openai', 'github', 'other', 'tavily', 'smtp', 'slack', 'telegram', 'notion', 'google', 'database']

export interface ApiKey {
  id: string
  provider: KeyProvider
  label: string
  last4: string
  created_at: string
}

export interface KeyTestResult {
  ok: boolean
  message: string
}

export interface Stats {
  flows: number
  runs_total: number
  runs_today: number
  runs_active: number
  success_rate: number | null
  recent_runs: Run[]
  cost_by_model?: ModelCost[]
  total_cost_usd?: number
}

export interface ModelCost {
  model: string
  cost_usd: number
  input_tokens: number
  output_tokens: number
  steps: number
}

export interface FlowImportResult {
  flow: Flow
  warnings: string[]
}

export type ModelProvider = 'anthropic' | 'openai' | 'ollama' | 'openai_compatible'
export const MODEL_PROVIDERS: readonly ModelProvider[] = ['anthropic', 'openai', 'ollama', 'openai_compatible']

export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

export interface ModelParams {
  temperature?: number | null
  max_tokens?: number | null
  effort?: Effort | null
}

export interface LlmModel {
  id: string
  provider: ModelProvider
  model_name: string
  label: string
  base_url: string | null
  api_key_id: string | null
  api_key_last4: string | null
  params: ModelParams
  is_default: boolean
  created_at: string
  pricing_key: string
  price: { input: number; output: number } | null
}

export interface LlmModelInput {
  provider: ModelProvider
  model_name: string
  label: string
  base_url?: string | null
  api_key_id?: string | null
  params?: ModelParams
  is_default?: boolean
}

export interface PricingTable {
  prices: Record<string, { input: number; output: number }>
}

export type ToolCategory = 'code' | 'web' | 'files' | 'data' | 'communication' | 'integrations' | 'media' | 'industry' | 'mcp'
export const TOOL_CATEGORIES: readonly ToolCategory[] = ['code', 'web', 'files', 'data', 'communication', 'integrations', 'media', 'industry', 'mcp']

export interface ToolInfo {
  name: string
  description: string
  category: ToolCategory
  input_schema: Record<string, unknown>
  required_credentials: string[]
  missing_credentials: string[]
  needs_sandbox: boolean
  status: 'ready' | 'missing_credentials' | 'offline'
  server_id: string | null
}

export interface McpServer {
  id: string
  name: string
  url: string | null
  command: string | null
  args: string[]
  env_keys: string[]
  header_keys: string[]
  enabled: boolean
  last_error: string | null
  tool_count: number
  created_at: string
}

export interface McpServerInput {
  name: string
  url?: string | null
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  headers?: Record<string, string>
  enabled?: boolean
}

export type TriggerType = 'manual' | 'schedule' | 'webhook' | 'email' | 'file_watch' | 'github'
export const TRIGGER_TYPES: readonly TriggerType[] = ['schedule', 'webhook', 'email', 'file_watch', 'github']

export interface Trigger {
  id: string
  flow_id: string
  type: TriggerType
  name: string
  config: Record<string, unknown>
  enabled: boolean
  last_fired: string | null
  last_error: string | null
  created_at: string
  webhook: { url: string; signature_header: string; secret: string | null; example: string } | null
  watch_folder: string | null
}

export interface TriggerInput {
  type: TriggerType
  name?: string
  config?: Record<string, unknown>
  enabled?: boolean
}

export interface Upload {
  id: string
  name: string
  size: number
  content_type: string
  created_at: string
}
