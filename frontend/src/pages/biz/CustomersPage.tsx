import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { MEMBERSHIP_LABEL, biz, type Appointment, type Customer, type CustomerInput, type Membership } from '../../api/biz'
import { ImportWizard } from '../../components/biz/ImportWizard'
import { Button, ErrorBanner, Modal } from '../../components/ui'
import { formatDateVi } from '../../lib/format'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'
const STATUS_COLOR: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200',
  expiring: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200',
  expired: 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-200',
  frozen: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200',
}

type Draft = { name: string; phone: string; tags: string; notes: string; zalo_id: string; fb_psid: string }
const EMPTY: Draft = { name: '', phone: '', tags: '', notes: '', zalo_id: '', fb_psid: '' }
const toDraft = (c: Customer): Draft => ({ name: c.name, phone: c.phone, tags: c.tags.join(', '), notes: c.notes, zalo_id: c.zalo_id ?? '', fb_psid: c.fb_psid ?? '' })
const toInput = (d: Draft): CustomerInput => ({
  name: d.name.trim(),
  phone: d.phone.trim(),
  tags: d.tags.split(',').map((t) => t.trim()).filter(Boolean),
  notes: d.notes,
  zalo_id: d.zalo_id.trim() || null,
  fb_psid: d.fb_psid.trim() || null,
})

export function MembershipBadge({ m }: { m: Customer['membership'] }) {
  if (!m) return <span className="text-xs text-zinc-400">Chưa có gói</span>
  const extra = m.sessions_left != null ? ` · còn ${m.sessions_left} buổi` : m.end_date ? ` · đến ${formatDateVi(m.end_date, true)}` : ''
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] ${STATUS_COLOR[m.status] ?? ''}`} title={m.service_name}>
      {MEMBERSHIP_LABEL[m.status]}
      {extra}
    </span>
  )
}

/** Khách hàng: search, list, add/edit, detail with memberships + appointments, CSV/Excel import. */
export function CustomersPage() {
  const [rows, setRows] = useState<Customer[] | null>(null)
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ id: string | null; draft: Draft } | null>(null)
  const [detail, setDetail] = useState<{ customer: Customer; memberships: Membership[]; appointments: Appointment[] } | null>(null)
  const [importing, setImporting] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setRows(await biz.listCustomers({ q, limit: 200 }))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [q])
  useEffect(() => {
    const t = setTimeout(() => void load(), 250)
    return () => clearTimeout(t)
  }, [load])

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    setBusy(true)
    setError(null)
    try {
      if (editing.id) await biz.updateCustomer(editing.id, toInput(editing.draft))
      else await biz.createCustomer(toInput(editing.draft))
      setEditing(null)
      await load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const open = async (c: Customer) => {
    try {
      const [memberships, appointments] = await Promise.all([biz.customerMemberships(c.id), biz.customerAppointments(c.id)])
      setDetail({ customer: c, memberships, appointments })
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const remove = async (c: Customer) => {
    if (!window.confirm(`Xoá khách ${c.name}? Gói và lịch hẹn của khách cũng bị xoá.`)) return
    try {
      await biz.deleteCustomer(c.id)
      setDetail(null)
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-6">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-2xl font-semibold">Khách hàng</h1>
        <Button onClick={() => setImporting(true)}>Nhập từ Excel/CSV</Button>
        <Button variant="primary" onClick={() => setEditing({ id: null, draft: { ...EMPTY } })}>
          Thêm khách
        </Button>
      </div>
      <input className={`${field} mb-3`} placeholder="Tìm theo tên hoặc số điện thoại…" aria-label="Tìm khách" value={q} onChange={(e) => setQ(e.target.value)} />
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {rows === null ? (
        <p className="text-sm text-zinc-500">Đang tải…</p>
      ) : rows.length === 0 ? (
        <p className="rounded-md border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700">Chưa có khách nào. Thêm tay hoặc nhập từ file.</p>
      ) : (
        <ul className="divide-y divide-zinc-100 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900" data-testid="customer-list">
          {rows.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => void open(c)}>
                <span className="font-medium">{c.name}</span>
                <span className="ml-2 font-mono text-xs text-zinc-500">{c.phone}</span>
                {c.tags.length > 0 && <span className="ml-2 text-[11px] text-zinc-500">{c.tags.join(' · ')}</span>}
              </button>
              <MembershipBadge m={c.membership} />
              <span className="text-xs text-zinc-500">{c.last_visit_at ? `Đến: ${formatDateVi(c.last_visit_at, true)}` : 'Chưa đến'}</span>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <Modal title={editing.id ? 'Sửa khách' : 'Thêm khách'} onClose={() => setEditing(null)}>
          <form onSubmit={(e) => void save(e)} className="space-y-3">
            <div>
              <label htmlFor="c-name" className={label}>Tên</label>
              <input id="c-name" className={field} value={editing.draft.name} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, name: e.target.value } })} required autoFocus />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="c-phone" className={label}>Số điện thoại</label>
                <input id="c-phone" className={field} value={editing.draft.phone} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, phone: e.target.value } })} />
              </div>
              <div>
                <label htmlFor="c-tags" className={label}>Nhãn (phẩy)</label>
                <input id="c-tags" className={field} placeholder="vip, khách quen" value={editing.draft.tags} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, tags: e.target.value } })} />
              </div>
              <div>
                <label htmlFor="c-zalo" className={label}>Zalo ID</label>
                <input id="c-zalo" className={field} value={editing.draft.zalo_id} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, zalo_id: e.target.value } })} />
              </div>
              <div>
                <label htmlFor="c-fb" className={label}>Facebook PSID</label>
                <input id="c-fb" className={field} value={editing.draft.fb_psid} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, fb_psid: e.target.value } })} />
              </div>
            </div>
            <div>
              <label htmlFor="c-notes" className={label}>Ghi chú</label>
              <textarea id="c-notes" className={`${field} h-20`} value={editing.draft.notes} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, notes: e.target.value } })} />
            </div>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setEditing(null)}>Huỷ</Button>
              <Button type="submit" variant="primary" disabled={busy || !editing.draft.name.trim()}>
                {busy ? 'Đang lưu…' : 'Lưu'}
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {detail && (
        <Modal title={detail.customer.name} onClose={() => setDetail(null)}>
          <div className="max-h-[70vh] space-y-3 overflow-y-auto text-sm" data-testid="customer-detail">
            <p className="font-mono text-xs text-zinc-500">
              {detail.customer.phone || 'không có SĐT'} · nguồn: {detail.customer.source}
              {detail.customer.tags.length > 0 && ` · ${detail.customer.tags.join(', ')}`}
            </p>
            {detail.customer.notes && <p className="whitespace-pre-wrap rounded bg-zinc-50 p-2 text-xs dark:bg-zinc-800">{detail.customer.notes}</p>}
            <section>
              <h3 className="text-xs font-semibold uppercase text-zinc-500">Gói đã mua</h3>
              {detail.memberships.length === 0 ? (
                <p className="text-xs text-zinc-500">Chưa có gói.</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {detail.memberships.map((m) => (
                    <li key={m.id} className="flex flex-wrap items-center gap-2 rounded border border-zinc-200 px-2 py-1 dark:border-zinc-800">
                      <span className="mr-auto">{m.service_name}</span>
                      <span className={`rounded px-1.5 py-0.5 text-[11px] ${STATUS_COLOR[m.status]}`}>{MEMBERSHIP_LABEL[m.status]}</span>
                      <span className="text-xs text-zinc-500">
                        {m.start_date} → {m.end_date ?? '∞'}
                        {m.sessions_total ? ` · ${m.sessions_used}/${m.sessions_total} buổi` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section>
              <h3 className="text-xs font-semibold uppercase text-zinc-500">Lịch hẹn gần đây</h3>
              {detail.appointments.length === 0 ? (
                <p className="text-xs text-zinc-500">Chưa có lịch hẹn.</p>
              ) : (
                <ul className="mt-1 space-y-1 text-xs">
                  {detail.appointments.slice(0, 8).map((a) => (
                    <li key={a.id}>
                      {formatDateVi(a.start_at)} · {a.service_name ?? 'Dịch vụ'} {a.staff_name ? `· ${a.staff_name}` : ''} · {a.status}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" className="text-rose-600" onClick={() => void remove(detail.customer)}>
                Xoá
              </Button>
              <Button
                onClick={() => {
                  setEditing({ id: detail.customer.id, draft: toDraft(detail.customer) })
                  setDetail(null)
                }}
              >
                Sửa
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {importing && <ImportWizard entity="customers" onClose={() => setImporting(false)} onDone={() => void load()} />}
    </div>
  )
}
