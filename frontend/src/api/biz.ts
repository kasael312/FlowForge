/** Industry data API (``/biz``) — customers, services, memberships, staff, appointments, inbox, import. */

import { API_URL, ApiError, formatDetail, rawRequest } from './client'

export type ServiceCategory = 'gym_package' | 'pt_session' | 'spa_treatment' | 'course'
export type MembershipStatus = 'active' | 'expiring' | 'expired' | 'frozen'
export type AppointmentStatus = 'booked' | 'confirmed' | 'arrived' | 'done' | 'cancelled' | 'no_show'
export type StaffRole = 'pt' | 'therapist' | 'receptionist'

export const CATEGORY_LABEL: Record<ServiceCategory, string> = {
  gym_package: 'Gói tập',
  pt_session: 'Buổi PT',
  spa_treatment: 'Liệu trình spa',
  course: 'Khoá / lớp',
}
export const MEMBERSHIP_LABEL: Record<MembershipStatus, string> = { active: 'Đang dùng', expiring: 'Sắp hết hạn', expired: 'Hết hạn', frozen: 'Tạm dừng' }
export const STAFF_ROLE_LABEL: Record<StaffRole, string> = { pt: 'Huấn luyện viên', therapist: 'Kỹ thuật viên', receptionist: 'Lễ tân' }
export const APPOINTMENT_LABEL: Record<AppointmentStatus, string> = { booked: 'Đã đặt', confirmed: 'Đã xác nhận', arrived: 'Đã đến', done: 'Hoàn thành', cancelled: 'Đã huỷ', no_show: 'Không đến' }

export interface Customer {
  id: string
  name: string
  phone: string
  zalo_id: string | null
  fb_psid: string | null
  tags: string[]
  source: string
  notes: string
  created_at: string
  last_visit_at: string | null
  membership: { id: string; status: MembershipStatus; service_name: string; end_date: string | null; sessions_left: number | null } | null
}
export interface CustomerInput {
  name: string
  phone: string
  zalo_id?: string | null
  fb_psid?: string | null
  tags: string[]
  source?: string
  notes: string
}
export interface Service {
  id: string
  name: string
  category: ServiceCategory
  duration_min: number
  price: number
  sessions: number
  validity_days: number
  description: string
  sale_notes: string
  active: boolean
  created_at: string
}
export type ServiceInput = Omit<Service, 'id' | 'created_at'>
export interface Membership {
  id: string
  customer_id: string
  customer_name: string
  service_id: string
  service_name: string
  start_date: string
  end_date: string | null
  sessions_total: number
  sessions_used: number
  sessions_left: number | null
  status: MembershipStatus
  amount_paid: number
  days_left: number | null
  created_at: string
}
export interface Staff {
  id: string
  name: string
  role: StaffRole
  working_hours: Record<string, string[][]>
  branch_id: string | null
  phone: string
  active: boolean
  created_at: string
}
export interface Appointment {
  id: string
  customer_id: string
  customer_name: string
  service_id: string | null
  service_name: string | null
  staff_id: string | null
  staff_name: string | null
  branch_id: string | null
  start_at: string
  end_at: string
  status: AppointmentStatus
  source: 'ai' | 'staff' | 'manual'
  notes: string
  created_at: string
}
export interface Conversation {
  id: string
  channel: 'zalo' | 'facebook'
  external_thread_id: string
  customer_id: string | null
  customer_name: string | null
  ai_enabled: boolean
  unread: boolean
  last_message_at: string | null
  last_inbound_at: string | null
  last_text: string
  window: { open: boolean; fallback: null | 'zns' | 'tag' }
  created_at: string
}
export interface Message {
  id: string
  conversation_id: string
  direction: 'in' | 'out'
  text: string
  sender: 'customer' | 'ai' | 'staff'
  status: 'ai_suggested' | 'sent' | 'handoff'
  created_at: string
}
export type ConnectorKind = 'zalo' | 'facebook' | 'google_calendar'
export interface ZnsTemplate {
  id: string
  name: string
  price: number
  params: string[]
}
export interface Connector {
  id: string
  kind: ConnectorKind
  enabled: boolean
  config: Record<string, unknown>
  secret_keys: string[]
  webhook_url: string | null
  verify_token: string | null
  status: 'disconnected' | 'connected' | 'error'
  last_checked_at: string | null
  last_error: string | null
  last_event_at: string | null
  updated_at: string
  test_message?: string | null
  test_details?: Record<string, unknown> | null
}
export type DocKind = 'price' | 'package' | 'rules' | 'faq' | 'other'
export interface KbDocument {
  id: string
  title: string
  kind: DocKind
  kind_label: string
  filename: string
  size: number
  status: 'ready' | 'error'
  error: string | null
  chunk_count: number
  embedder: string
  preview: string
  created_at: string
}
export interface AiSettings {
  forbidden: string[]
  address_self: string
  address_customer: string
  friendliness: number
  style_examples: string[]
  medical_guard: boolean
  handoff_keywords: string[]
  handoff_message: string
  handoff_preview: string
  guard_categories: string[]
  updated_at: string
}
export interface KbHit {
  chunk_id: string
  document_id: string
  title: string
  kind: DocKind
  ordinal: number
  text: string
  score: number
}
export interface KbPreview {
  guard: { handoff: boolean; category: string | null; matched: string | null; reply: string | null }
  hits: KbHit[]
  system_prompt: string
}
export interface ConfigField {
  key: string
  label: string
  type: 'text' | 'number' | 'select' | 'bool' | 'time' | 'textarea'
  options: { value: string; label: string }[]
  default: unknown
  help: string
}
export interface Automation {
  id: string
  key: string
  name: string
  description: string
  trigger_label: string
  enabled: boolean
  config_schema: ConfigField[]
  config: Record<string, unknown>
  runs_7d: number
  failed_7d: number
  last_run_at: string | null
  last_error: string | null
  needs_model: boolean
}
export interface TryResult {
  run_id: string
  status: string
  error: string | null
  messages: string[]
  inputs: Record<string, unknown>
}
export interface Notification {
  id: string
  kind: string
  title: string
  body: string
  read: boolean
  delivered_via: string
  created_at: string
}
export interface Overview {
  date: string
  appointments_today: number
  arrived_today: number
  no_show_rate: number | null
  no_show_30d: number
  expiring_memberships: number
  unread_conversations: number
  ai_suggestions: number
  ai_messages_week: number
  hours_saved_week: number
  new_customers_week: number
  customers: number
  upcoming_today: { id: string; time: string; customer_name: string; service_name: string; staff_name: string; status: string }[]
  notifications: { id: string; kind: string; title: string; body: string; created_at: string }[]
}

export type ImportEntity = 'customers' | 'memberships' | 'services'
export interface ImportPreview {
  entity: ImportEntity
  columns: string[]
  sample: Record<string, string>[]
  row_count: number
  fields: { key: string; label: string; required: boolean }[]
  suggested_mapping: Record<string, string>
}
export interface ImportResult {
  entity: ImportEntity
  created: number
  updated: number
  skipped: number
  errors: string[]
}

async function upload<T>(path: string, file: File): Promise<T> {
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

const q = (params: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v))
  const str = s.toString()
  return str ? `?${str}` : ''
}

export const biz = {
  listCustomers: (params: { q?: string; tag?: string; limit?: number; offset?: number } = {}) => rawRequest<Customer[]>(`/biz/customers${q(params)}`),
  getCustomer: (id: string) => rawRequest<Customer>(`/biz/customers/${id}`),
  createCustomer: (body: CustomerInput) => rawRequest<Customer>('/biz/customers', { method: 'POST', body: JSON.stringify(body) }),
  updateCustomer: (id: string, body: Partial<CustomerInput>) => rawRequest<Customer>(`/biz/customers/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteCustomer: (id: string) => rawRequest<void>(`/biz/customers/${id}`, { method: 'DELETE' }),
  customerMemberships: (id: string) => rawRequest<Membership[]>(`/biz/customers/${id}/memberships`),
  customerAppointments: (id: string) => rawRequest<Appointment[]>(`/biz/customers/${id}/appointments`),
  listServices: (includeInactive = false) => rawRequest<Service[]>(`/biz/services${q({ include_inactive: includeInactive })}`),
  createService: (body: ServiceInput) => rawRequest<Service>('/biz/services', { method: 'POST', body: JSON.stringify(body) }),
  updateService: (id: string, body: Partial<ServiceInput>) => rawRequest<Service>(`/biz/services/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteService: (id: string) => rawRequest<void>(`/biz/services/${id}`, { method: 'DELETE' }),
  listMemberships: (params: { status?: MembershipStatus; customer_id?: string } = {}) => rawRequest<Membership[]>(`/biz/memberships${q(params)}`),
  createMembership: (body: { customer_id: string; service_id: string; start_date: string; end_date?: string | null; sessions_total?: number | null; sessions_used?: number; amount_paid?: number }) =>
    rawRequest<Membership>('/biz/memberships', { method: 'POST', body: JSON.stringify(body) }),
  updateMembership: (id: string, body: { end_date?: string | null; sessions_total?: number; sessions_used?: number; amount_paid?: number; status?: MembershipStatus }) =>
    rawRequest<Membership>(`/biz/memberships/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  useSession: (id: string) => rawRequest<Membership>(`/biz/memberships/${id}/use`, { method: 'POST' }),
  listStaff: (includeInactive = false) => rawRequest<Staff[]>(`/biz/staff${q({ include_inactive: includeInactive })}`),
  createStaff: (body: Omit<Staff, 'id' | 'created_at'>) => rawRequest<Staff>('/biz/staff', { method: 'POST', body: JSON.stringify(body) }),
  updateStaff: (id: string, body: Partial<Omit<Staff, 'id' | 'created_at'>>) => rawRequest<Staff>(`/biz/staff/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteStaff: (id: string) => rawRequest<void>(`/biz/staff/${id}`, { method: 'DELETE' }),
  listAppointments: (params: { day?: string; start?: string; end?: string; staff_id?: string; branch_id?: string; status?: AppointmentStatus } = {}) => rawRequest<Appointment[]>(`/biz/appointments${q(params)}`),
  createAppointment: (body: Record<string, unknown>, force = false) => rawRequest<Appointment>(`/biz/appointments${q({ force })}`, { method: 'POST', body: JSON.stringify(body) }),
  updateAppointment: (id: string, body: Record<string, unknown>, force = false) => rawRequest<Appointment>(`/biz/appointments/${id}${q({ force })}`, { method: 'PUT', body: JSON.stringify(body) }),
  listConversations: (unread?: boolean) => rawRequest<Conversation[]>(`/biz/conversations${q({ unread })}`),
  updateConversation: (id: string, body: { ai_enabled?: boolean; unread?: boolean; customer_id?: string }) => rawRequest<Conversation>(`/biz/conversations/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  listMessages: (id: string) => rawRequest<Message[]>(`/biz/conversations/${id}/messages`),
  approveSuggestion: (conversationId: string, messageId: string, text?: string) =>
    rawRequest<Message>(`/biz/conversations/${conversationId}/messages/${messageId}/send${q({ text })}`, { method: 'POST' }),
  discardSuggestion: (conversationId: string, messageId: string) => rawRequest<void>(`/biz/conversations/${conversationId}/messages/${messageId}`, { method: 'DELETE' }),
  overview: () => rawRequest<Overview>('/biz/overview'),
  listConnectors: () => rawRequest<Connector[]>('/biz/connectors'),
  upsertConnector: (kind: ConnectorKind, body: { enabled?: boolean; config?: Record<string, unknown>; secrets?: Record<string, string | null>; rotate_webhook_token?: boolean }) =>
    rawRequest<Connector>(`/biz/connectors/${kind}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteConnector: (kind: ConnectorKind) => rawRequest<void>(`/biz/connectors/${kind}`, { method: 'DELETE' }),
  testConnector: (kind: ConnectorKind) => rawRequest<Connector>(`/biz/connectors/${kind}/test`, { method: 'POST' }),
  syncCalendar: () => rawRequest<{ pushed: number; created: number; moved: number; cancelled: number }>('/biz/connectors/google_calendar/sync', { method: 'POST' }),
  sendMessage: (conversationId: string, body: { text: string; sender?: 'staff' | 'customer'; zns_template_id?: string; zns_params?: Record<string, string>; fb_tag?: string; deliver?: boolean }) =>
    rawRequest<Message>(`/biz/conversations/${conversationId}/messages`, { method: 'POST', body: JSON.stringify(body) }),
  listKbDocuments: () => rawRequest<KbDocument[]>('/biz/kb/documents'),
  uploadKbDocument: (file: File, kind: DocKind, title: string) => upload<KbDocument>(`/biz/kb/documents?kind=${kind}&title=${encodeURIComponent(title)}`, file),
  addKbText: (title: string, kind: DocKind, text: string) => rawRequest<KbDocument>('/biz/kb/documents/text', { method: 'POST', body: JSON.stringify({ title, kind, text }) }),
  deleteKbDocument: (id: string) => rawRequest<void>(`/biz/kb/documents/${id}`, { method: 'DELETE' }),
  kbSearch: (question: string, k = 5) => rawRequest<{ hits: KbHit[] }>('/biz/kb/search', { method: 'POST', body: JSON.stringify({ question, k }) }),
  kbPreview: (question: string, k = 4) => rawRequest<KbPreview>('/biz/kb/preview', { method: 'POST', body: JSON.stringify({ question, k }) }),
  getAiSettings: () => rawRequest<AiSettings>('/biz/ai/settings'),
  updateAiSettings: (body: Partial<AiSettings>) => rawRequest<AiSettings>('/biz/ai/settings', { method: 'PUT', body: JSON.stringify(body) }),
  listAutomations: () => rawRequest<Automation[]>('/biz/automations'),
  updateAutomation: (key: string, body: { enabled?: boolean; config?: Record<string, unknown> }) => rawRequest<Automation>(`/biz/automations/${key}`, { method: 'PUT', body: JSON.stringify(body) }),
  tryAutomation: (key: string) => rawRequest<TryResult>(`/biz/automations/${key}/try?wait_seconds=90`, { method: 'POST' }),
  listNotifications: () => rawRequest<Notification[]>('/biz/notifications'),
  markNotificationRead: (id: string) => rawRequest<Notification>(`/biz/notifications/${id}/read`, { method: 'POST' }),
  importPreview: (entity: ImportEntity, file: File) => upload<ImportPreview>(`/biz/import/preview?entity=${entity}`, file),
  importApply: (entity: ImportEntity, mapping: Record<string, string>, file: File) => upload<ImportResult>(`/biz/import?entity=${entity}&mapping=${encodeURIComponent(JSON.stringify(mapping))}`, file),
}
