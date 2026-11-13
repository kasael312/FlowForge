import { API_URL, rawRequest } from './client'

export interface Plan {
  key: string
  name: string
  price_vnd: number
  ai_conversations_month: number
  branches: number
  description: string
  trial_days: number
}
export type SubscriptionStatus = 'trial' | 'active' | 'past_due' | 'expired'
export interface Usage {
  ai_conversations: number
  ai_conversations_limit: number
  branches: number
  branches_limit: number
  month: string
}
export interface Invoice {
  id: string
  number: string
  plan: string
  plan_name: string
  months: number
  amount_vnd: number
  status: 'pending' | 'paid' | 'cancelled'
  gateway: string
  transfer_code: string
  payment: {
    method?: string
    instructions?: string
    bank_name?: string
    bank_bin?: string
    account?: string
    account_name?: string
    qr_image_url?: string
    qr_payload?: string
    configured?: boolean
  }
  paid_at: string | null
  created_at: string | null
}
export interface BillingState {
  plan: Plan
  subscribed_plan: string
  status: SubscriptionStatus
  trial_ends_at: string | null
  current_period_end: string | null
  days_left: number | null
  usage: Usage
  warnings: string[]
  blocked: boolean
  plans: Plan[]
  gateways: { key: string; name: string }[]
  pending_invoice: Invoice | null
}

export interface AdminWorkspace {
  id: string
  name: string
  industry: string
  owner_email: string | null
  members: number
  customers: number
  created_at: string | null
  last_activity_at: string | null
  plan: string
  plan_name: string
  status: SubscriptionStatus
  trial_ends_at: string | null
  current_period_end: string | null
  days_left: number | null
  usage: Usage
  open_alerts: number
}
export interface AdminSummary {
  workspaces: number
  trials: number
  trials_ending_7d: number
  paid: number
  expired: number
  pending_invoices: number
  open_alerts: number
}
export interface AdminInvoice extends Invoice {
  workspace_id: string
  workspace_name: string
}
export interface AdminAlert {
  id: string
  workspace_id: string | null
  workspace_name: string
  key: string
  title: string
  body: string
  detail: Record<string, unknown>
  read: boolean
  created_at: string | null
}
export interface LogEntry {
  time: string | null
  kind: string
  text: string
  detail: Record<string, unknown>
}

export const STATUS_LABEL: Record<SubscriptionStatus, string> = { trial: 'Dùng thử', active: 'Đang dùng', past_due: 'Quá hạn (ân hạn)', expired: 'Hết hạn' }
export const vnd = (n: number) => `${n.toLocaleString('vi-VN')} đ`

export const billing = {
  get: () => rawRequest<BillingState>('/biz/billing'),
  listInvoices: () => rawRequest<Invoice[]>('/biz/billing/invoices'),
  createInvoice: (plan: string, months: number, gateway = 'bank_transfer') => rawRequest<Invoice>('/biz/billing/invoices', { method: 'POST', body: JSON.stringify({ plan, months, gateway }) }),
  cancelInvoice: (id: string) => rawRequest<Invoice>(`/biz/billing/invoices/${id}/cancel`, { method: 'POST' }),
  pdfUrl: (id: string) => `${API_URL}/biz/billing/invoices/${id}/pdf`,
}

export const admin = {
  summary: () => rawRequest<AdminSummary>('/admin/summary'),
  workspaces: (q?: string) => rawRequest<AdminWorkspace[]>(`/admin/workspaces${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  extendTrial: (id: string, days: number, note = '') => rawRequest<AdminWorkspace>(`/admin/workspaces/${id}/extend-trial`, { method: 'POST', body: JSON.stringify({ days, note }) }),
  setPlan: (id: string, plan: string, months: number, note = '') => rawRequest<AdminWorkspace>(`/admin/workspaces/${id}/plan`, { method: 'POST', body: JSON.stringify({ plan, months, note }) }),
  logs: (id: string) => rawRequest<LogEntry[]>(`/admin/workspaces/${id}/logs`),
  invoices: (status?: string) => rawRequest<AdminInvoice[]>(`/admin/invoices${status ? `?status=${status}` : ''}`),
  confirmInvoice: (id: string) => rawRequest<AdminInvoice>(`/admin/invoices/${id}/confirm`, { method: 'POST' }),
  cancelInvoice: (id: string) => rawRequest<AdminInvoice>(`/admin/invoices/${id}/cancel`, { method: 'POST' }),
  alerts: (unreadOnly = false) => rawRequest<AdminAlert[]>(`/admin/alerts${unreadOnly ? '?unread_only=true' : ''}`),
  readAlert: (id: string) => rawRequest<{ id: string; read: boolean }>(`/admin/alerts/${id}/read`, { method: 'POST' }),
}
