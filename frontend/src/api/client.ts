/** Thin typed wrapper around the backend REST API.
 *
 * Auth is cookie based (httpOnly). Every request is sent with credentials; on a
 * 401 the client tries `POST /auth/refresh` once and retries. If that fails the
 * registered `onUnauthorized` handler is called (the auth context uses it to
 * drop the session and redirect to /login).
 */

import type {
  ApprovalDecision,
  Branch,
  OnboardingAnswers,
  OnboardingQuestions,
  Workspace,
  WorkspaceMember,
  FlowImportResult,
  ResolvedPrompt,
  Rule,
  RuleInput,
  RuleScope,
  Skill,
  SkillInput,
  ApiKey,
  Artifact,
  Flow,
  FlowInput,
  FlowSummary,
  FlowVisibility,
  GraphReport,
  KeyProvider,
  KeyTestResult,
  LlmModel,
  LlmModelInput,
  McpServer,
  McpServerInput,
  PricingTable,
  Run,
  RunDetail,
  SharedFlow,
  Stats,
  StepRun,
  StepTryRequest,
  StepTryResult,
  Template,
  ToolInfo,
  Trigger,
  TriggerInput,
  Upload,
  User,
} from './types'

export const API_URL: string = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
export const WS_URL: string = API_URL.replace(/^http/, 'ws')

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

let unauthorizedHandler: (() => void) | null = null
/** Called when a request stays 401 after a refresh attempt. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn
}

let refreshing: Promise<boolean> | null = null
/** Single-flight refresh so parallel 401s trigger one refresh call. */
function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch(`${API_URL}/auth/refresh`, { method: 'POST', credentials: 'include' })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        refreshing = null
      })
  }
  return refreshing
}

/** POST one file as multipart/form-data (field `file`). */
async function multipart<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${API_URL}${path}`, { method: 'POST', body: form, credentials: 'include' })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = formatDetail(((await res.json()) as { detail?: unknown }).detail) ?? detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

async function request<T>(path: string, init?: RequestInit, retry = true): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (res.status === 401 && !path.startsWith('/auth/')) {
    if (retry && (await refreshSession())) return request<T>(path, init, false)
    unauthorizedHandler?.()
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      detail = formatDetail(body.detail) ?? detail
    } catch {
      /* non-JSON body */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** FastAPI returns either a string or a list of validation errors. */
export function formatDetail(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg ?? ''}`)
      .join('; ')
  }
  return undefined
}

/** Low-level request for feature API modules (e.g. src/api/biz.ts). */
export const rawRequest = request

export const api = {
  health: () => request<{ status: string }>('/health'),

  // auth
  register: (email: string, password: string) =>
    request<User>('/auth/register', { method: 'POST', body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    request<User>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  me: () => request<User>('/auth/me'),
  // ---- workspaces
  onboardingQuestions: () => request<OnboardingQuestions>('/onboarding/questions'),
  onboard: (body: OnboardingAnswers) => request<Workspace>('/onboarding', { method: 'POST', body: JSON.stringify(body) }),
  listWorkspaces: () => request<Workspace[]>('/workspaces'),
  currentWorkspace: () => request<Workspace>('/workspaces/current'),
  updateWorkspace: (body: { name?: string; industry?: string; timezone?: string; branches?: Branch[] }) =>
    request<Workspace>('/workspaces/current', { method: 'PUT', body: JSON.stringify(body) }),
  switchWorkspace: (id: string) => request<Workspace>(`/workspaces/${id}/switch`, { method: 'POST' }),
  listMembers: () => request<WorkspaceMember[]>('/workspaces/current/members'),
  addMember: (email: string, role: string) => request<WorkspaceMember>('/workspaces/current/members', { method: 'POST', body: JSON.stringify({ email, role }) }),
  updateMember: (id: string, role: string) => request<WorkspaceMember>(`/workspaces/current/members/${id}`, { method: 'PUT', body: JSON.stringify({ role }) }),
  removeMember: (id: string) => request<void>(`/workspaces/current/members/${id}`, { method: 'DELETE' }),
  refresh: () => request<User>('/auth/refresh', { method: 'POST' }),
  changePassword: (current_password: string, new_password: string) =>
    request<void>('/auth/password', { method: 'POST', body: JSON.stringify({ current_password, new_password }) }),
  stats: () => request<Stats>('/stats'),

  listFlows: () => request<FlowSummary[]>('/flows'),
  exportFlow: (id: string) => request<Record<string, unknown>>(`/flows/${id}/export`),
  importFlow: (doc: unknown) => request<FlowImportResult>('/flows/import', { method: 'POST', body: JSON.stringify(doc) }),
  importIntoFlow: (id: string, doc: unknown) => request<FlowImportResult>(`/flows/${id}/import`, { method: 'POST', body: JSON.stringify(doc) }),
  getFlow: (id: string) => request<Flow>(`/flows/${id}`),
  createFlow: (body: FlowInput) => request<Flow>('/flows', { method: 'POST', body: JSON.stringify(body) }),
  updateFlow: (id: string, body: Partial<FlowInput> & { visibility?: FlowVisibility }) =>
    request<Flow>(`/flows/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteFlow: (id: string) => request<void>(`/flows/${id}`, { method: 'DELETE' }),
  duplicateFlow: (id: string) => request<Flow>(`/flows/${id}/duplicate`, { method: 'POST' }),
  validateFlow: (body: Partial<FlowInput>) =>
    request<GraphReport>('/flows/validate', { method: 'POST', body: JSON.stringify(body) }),
  tryStep: (flowId: string, stepId: string, body: StepTryRequest) =>
    request<StepTryResult>(`/flows/${flowId}/steps/${stepId}/try`, { method: 'POST', body: JSON.stringify(body) }),

  // templates
  listTemplates: () => request<Template[]>('/templates'),
  useTemplate: (id: string) => request<Flow>(`/templates/${id}/use`, { method: 'POST' }),
  saveTemplate: (body: { flow_id: string; name?: string; description?: string }) =>
    request<Template>('/templates', { method: 'POST', body: JSON.stringify(body) }),
  deleteTemplate: (id: string) => request<void>(`/templates/${id}`, { method: 'DELETE' }),

  // models
  listModels: () => request<LlmModel[]>('/models'),
  createModel: (body: LlmModelInput) => request<LlmModel>('/models', { method: 'POST', body: JSON.stringify(body) }),
  updateModel: (id: string, body: Partial<LlmModelInput>) =>
    request<LlmModel>(`/models/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteModel: (id: string) => request<void>(`/models/${id}`, { method: 'DELETE' }),
  setDefaultModel: (id: string) => request<LlmModel>(`/models/${id}/default`, { method: 'POST' }),
  testModel: (id: string) => request<KeyTestResult>(`/models/${id}/test`, { method: 'POST' }),
  getPricing: () => request<PricingTable>('/pricing'),
  putPricing: (body: PricingTable) => request<PricingTable>('/pricing', { method: 'PUT', body: JSON.stringify(body) }),

  // tools + MCP
  listTools: () => request<ToolInfo[]>('/tools'),
  testTool: (name: string) => request<KeyTestResult>(`/tools/${encodeURIComponent(name)}/test`, { method: 'POST' }),
  listMcpServers: () => request<McpServer[]>('/mcp-servers'),
  createMcpServer: (body: McpServerInput) => request<McpServer>('/mcp-servers', { method: 'POST', body: JSON.stringify(body) }),
  updateMcpServer: (id: string, body: Partial<McpServerInput>) =>
    request<McpServer>(`/mcp-servers/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteMcpServer: (id: string) => request<void>(`/mcp-servers/${id}`, { method: 'DELETE' }),
  refreshMcpServer: (id: string) => request<McpServer>(`/mcp-servers/${id}/refresh`, { method: 'POST' }),

  // triggers + uploads
  listTriggers: (flowId: string) => request<Trigger[]>(`/flows/${flowId}/triggers`),
  createTrigger: (flowId: string, body: TriggerInput) =>
    request<Trigger>(`/flows/${flowId}/triggers`, { method: 'POST', body: JSON.stringify(body) }),
  updateTrigger: (id: string, body: Partial<TriggerInput> & { rotate_secret?: boolean }) =>
    request<Trigger>(`/triggers/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteTrigger: (id: string) => request<void>(`/triggers/${id}`, { method: 'DELETE' }),
  fireTrigger: (id: string) => request<Run>(`/triggers/${id}/fire`, { method: 'POST' }),
  uploadFile: (file: File): Promise<Upload> => multipart<Upload>('/uploads', file),
  // ---- skills
  listSkills: () => request<Skill[]>('/skills'),
  getSkill: (id: string) => request<Skill>(`/skills/${id}`),
  createSkill: (body: SkillInput) => request<Skill>('/skills', { method: 'POST', body: JSON.stringify(body) }),
  updateSkill: (id: string, body: Partial<SkillInput>) => request<Skill>(`/skills/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteSkill: (id: string) => request<void>(`/skills/${id}`, { method: 'DELETE' }),
  duplicateSkill: (id: string) => request<Skill>(`/skills/${id}/duplicate`, { method: 'POST' }),
  importSkill: (file: File) => multipart<Skill>('/skills/import', file),
  uploadSkillFile: (id: string, file: File, path?: string) => multipart<Skill>(`/skills/${id}/files${path ? `?path=${encodeURIComponent(path)}` : ''}`, file),
  deleteSkillFile: (id: string, path: string) => request<Skill>(`/skills/${id}/files/${path.split('/').map(encodeURIComponent).join('/')}`, { method: 'DELETE' }),
  // ---- rules
  listRules: (params?: { scope?: RuleScope; flow_id?: string }) => {
    const q = new URLSearchParams()
    if (params?.scope) q.set('scope', params.scope)
    if (params?.flow_id) q.set('flow_id', params.flow_id)
    const qs = q.toString()
    return request<Rule[]>(`/rules${qs ? `?${qs}` : ''}`)
  },
  createRule: (body: RuleInput) => request<Rule>('/rules', { method: 'POST', body: JSON.stringify(body) }),
  updateRule: (id: string, body: Partial<RuleInput>) => request<Rule>(`/rules/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteRule: (id: string) => request<void>(`/rules/${id}`, { method: 'DELETE' }),
  resolvedPrompt: (flowId: string, stepId: string) => request<ResolvedPrompt>(`/flows/${flowId}/steps/${stepId}/resolved-prompt`),
  skillExportUrl: (id: string) => `${API_URL}/skills/${id}/export`,
  skillFileUrl: (id: string, path: string) => `${API_URL}/skills/${id}/files/${path.split('/').map(encodeURIComponent).join('/')}`,

  // sharing
  getShared: (token: string) => request<SharedFlow>(`/shared/${token}`),
  cloneShared: (token: string) => request<Flow>(`/shared/${token}/clone`, { method: 'POST' }),

  startRun: (flowId: string, body: { repo_url: string; branch: string; task: string; inputs?: Record<string, unknown>; sandbox?: Record<string, unknown> }) =>
    request<Run>(`/flows/${flowId}/run`, { method: 'POST', body: JSON.stringify(body) }),
  listRuns: (params: { flow_id?: string; status?: string; limit?: number } = {}) => {
    const q = new URLSearchParams()
    if (params.flow_id) q.set('flow_id', params.flow_id)
    if (params.status) q.set('status', params.status)
    if (params.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<Run[]>(`/runs${qs ? `?${qs}` : ''}`)
  },
  getRun: (id: string) => request<RunDetail>(`/runs/${id}`),
  getRunSteps: (id: string) => request<StepRun[]>(`/runs/${id}/steps`),
  cancelRun: (id: string) => request<Run>(`/runs/${id}/cancel`, { method: 'POST' }),
  approveRun: (id: string, body: ApprovalDecision) => request<Run>(`/runs/${id}/approve`, { method: 'POST', body: JSON.stringify(body) }),
  listArtifacts: (runId: string) => request<Artifact[]>(`/runs/${runId}/artifacts`),
  artifactDownloadUrl: (runId: string, artifactId: string) => `${API_URL}/runs/${runId}/artifacts/${artifactId}/download`,

  logsUrl: (runId: string) => `${WS_URL}/runs/${runId}/logs`,

  // API keys
  listKeys: () => request<ApiKey[]>('/keys'),
  createKey: (body: { provider: KeyProvider; label: string; value: string }) =>
    request<ApiKey>('/keys', { method: 'POST', body: JSON.stringify(body) }),
  deleteKey: (id: string) => request<void>(`/keys/${id}`, { method: 'DELETE' }),
  testKeyValue: (provider: KeyProvider, value: string) =>
    request<KeyTestResult>('/keys/test', { method: 'POST', body: JSON.stringify({ provider, value }) }),
  testStoredKey: (id: string) => request<KeyTestResult>(`/keys/${id}/test`, { method: 'POST' }),
}
