import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  CATEGORY_LABEL,
  MEMBERSHIP_LABEL,
  STAFF_ROLE_LABEL,
  biz,
  type Customer,
  type Membership,
  type MembershipStatus,
  type Service,
  type ServiceCategory,
  type ServiceInput,
  type Staff,
  type StaffRole,
} from '../../api/biz'
import { ImportWizard } from '../../components/biz/ImportWizard'
import { Button, ErrorBanner, Modal } from '../../components/ui'
import { useWorkspace } from '../../lib/WorkspaceContext'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'
const DAYS: [string, string][] = [['mon', 'T2'], ['tue', 'T3'], ['wed', 'T4'], ['thu', 'T5'], ['fri', 'T6'], ['sat', 'T7'], ['sun', 'CN']]
const money = (n: number) => `${Math.round(n).toLocaleString('vi-VN')}đ`

type Tab = 'services' | 'memberships' | 'staff'
const TABS: [Tab, string][] = [['services', 'Dịch vụ & gói'], ['memberships', 'Gói đã bán'], ['staff', 'Nhân viên']]

const EMPTY_SERVICE: ServiceInput = { name: '', category: 'gym_package', duration_min: 60, price: 0, sessions: 0, validity_days: 30, description: '', sale_notes: '', active: true }

/** Gói & dịch vụ: services, sold memberships and staff with working hours. */
export function ServicesPage() {
  const { current } = useWorkspace()
  const [tab, setTab] = useState<Tab>('services')
  const [services, setServices] = useState<Service[] | null>(null)
  const [memberships, setMemberships] = useState<Membership[] | null>(null)
  const [staff, setStaff] = useState<Staff[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState<'services' | 'memberships' | null>(null)
  const [svcEdit, setSvcEdit] = useState<{ id: string | null; draft: ServiceInput } | null>(null)
  const [staffEdit, setStaffEdit] = useState<{ id: string | null; draft: Omit<Staff, 'id' | 'created_at'> } | null>(null)
  const [sell, setSell] = useState<{ customers: Customer[]; customer_id: string; service_id: string; start_date: string; amount_paid: string } | null>(null)
  const [memberFilter, setMemberFilter] = useState<MembershipStatus | ''>('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, m, st] = await Promise.all([biz.listServices(true), biz.listMemberships(), biz.listStaff(true)])
      setServices(s)
      setMemberships(m)
      setStaff(st)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
      return true
    } catch (e) {
      setError((e as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }

  const saveService = async (e: FormEvent) => {
    e.preventDefault()
    if (!svcEdit) return
    const body = { ...svcEdit.draft, name: svcEdit.draft.name.trim() }
    if (await run(() => (svcEdit.id ? biz.updateService(svcEdit.id, body) : biz.createService(body)))) setSvcEdit(null)
  }
  const saveStaff = async (e: FormEvent) => {
    e.preventDefault()
    if (!staffEdit) return
    const body = { ...staffEdit.draft, name: staffEdit.draft.name.trim(), branch_id: staffEdit.draft.branch_id || null }
    if (await run(() => (staffEdit.id ? biz.updateStaff(staffEdit.id, body) : biz.createStaff(body)))) setStaffEdit(null)
  }
  const openSell = async () => {
    try {
      const customers = await biz.listCustomers({ limit: 500 })
      setSell({ customers, customer_id: customers[0]?.id ?? '', service_id: services?.[0]?.id ?? '', start_date: new Date().toISOString().slice(0, 10), amount_paid: String(services?.[0]?.price ?? 0) })
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const saveSell = async (e: FormEvent) => {
    e.preventDefault()
    if (!sell) return
    if (await run(() => biz.createMembership({ customer_id: sell.customer_id, service_id: sell.service_id, start_date: sell.start_date, amount_paid: Number(sell.amount_paid) || 0 }))) {
      setSell(null)
      setTab('memberships')
    }
  }

  const branches = current?.branches ?? []
  const branchName = (id: string | null) => branches.find((b) => b.id === id)?.name ?? ''
  const hoursText = (h: Record<string, string[][]>) => DAYS.filter(([d]) => (h[d] ?? []).length).map(([d, l]) => `${l} ${(h[d] ?? []).map((s) => s.join('-')).join(', ')}`).join(' · ') || 'Chưa đặt giờ làm'
  const shownMemberships = (memberships ?? []).filter((m) => !memberFilter || m.status === memberFilter)

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-6">
      <h1 className="text-2xl font-semibold">Gói & dịch vụ</h1>
      <div className="mt-3 flex gap-1 border-b border-zinc-200 dark:border-zinc-800">
        {TABS.map(([t, l]) => (
          <button key={t} type="button" onClick={() => setTab(t)} className={`px-3 py-1.5 text-sm ${tab === t ? 'border-b-2 border-zinc-900 font-medium dark:border-zinc-100' : 'text-zinc-500'}`}>
            {l}
          </button>
        ))}
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />

      {tab === 'services' && (
        <section className="mt-3">
          <div className="mb-2 flex flex-wrap justify-end gap-2">
            <Button onClick={() => setImporting('services')}>Nhập từ file</Button>
            <Button onClick={() => void openSell()} disabled={!services?.length}>
              Bán gói cho khách
            </Button>
            <Button variant="primary" onClick={() => setSvcEdit({ id: null, draft: { ...EMPTY_SERVICE, category: current?.industry === 'spa' ? 'spa_treatment' : 'gym_package' } })}>
              Thêm dịch vụ
            </Button>
          </div>
          {services === null ? (
            <p className="text-sm text-zinc-500">Đang tải…</p>
          ) : (
            <ul className="divide-y divide-zinc-100 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900" data-testid="service-list">
              {services.map((s) => (
                <li key={s.id} className={`flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm ${s.active ? '' : 'opacity-50'}`}>
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{s.name}</span>
                    <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] dark:bg-zinc-800">{CATEGORY_LABEL[s.category]}</span>
                    <p className="text-xs text-zinc-500">
                      {money(s.price)}
                      {s.sessions ? ` · ${s.sessions} buổi` : ''}
                      {s.validity_days ? ` · ${s.validity_days} ngày` : ''}
                      {s.duration_min ? ` · ${s.duration_min} phút` : ''}
                      {!s.active && ' · đã ẩn'}
                    </p>
                  </div>
                  <button type="button" className="text-xs text-sky-700 hover:underline dark:text-sky-400" onClick={() => setSvcEdit({ id: s.id, draft: { name: s.name, category: s.category, duration_min: s.duration_min, price: s.price, sessions: s.sessions, validity_days: s.validity_days, description: s.description, sale_notes: s.sale_notes, active: s.active } })}>
                    Sửa
                  </button>
                  <button type="button" className="text-xs text-rose-600 hover:underline" onClick={() => window.confirm(`Xoá / ẩn "${s.name}"?`) && void run(() => biz.deleteService(s.id))}>
                    Xoá
                  </button>
                </li>
              ))}
              {services.length === 0 && <li className="px-3 py-4 text-sm text-zinc-500">Chưa có dịch vụ nào.</li>}
            </ul>
          )}
        </section>
      )}

      {tab === 'memberships' && (
        <section className="mt-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <select aria-label="Lọc trạng thái" className="rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900" value={memberFilter} onChange={(e) => setMemberFilter(e.target.value as MembershipStatus | '')}>
              <option value="">Tất cả trạng thái</option>
              {(Object.keys(MEMBERSHIP_LABEL) as MembershipStatus[]).map((k) => (
                <option key={k} value={k}>
                  {MEMBERSHIP_LABEL[k]}
                </option>
              ))}
            </select>
            <span className="mr-auto text-xs text-zinc-500">{shownMemberships.length} gói</span>
            <Button onClick={() => setImporting('memberships')}>Nhập từ file</Button>
            <Button variant="primary" onClick={() => void openSell()} disabled={!services?.length}>
              Bán gói
            </Button>
          </div>
          <ul className="divide-y divide-zinc-100 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900" data-testid="membership-list">
            {shownMemberships.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{m.customer_name}</span> · {m.service_name}
                  <p className="text-xs text-zinc-500">
                    {m.start_date} → {m.end_date ?? 'không hạn'}
                    {m.sessions_total ? ` · ${m.sessions_used}/${m.sessions_total} buổi` : ''}
                    {m.days_left != null ? (m.days_left >= 0 ? ` · còn ${m.days_left} ngày` : ` · quá hạn ${-m.days_left} ngày`) : ''} · {money(m.amount_paid)}
                  </p>
                </div>
                <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] dark:bg-zinc-800">{MEMBERSHIP_LABEL[m.status]}</span>
                {m.sessions_total > 0 && m.status !== 'expired' && (
                  <Button className="px-2 py-0.5 text-xs" disabled={busy} onClick={() => void run(() => biz.useSession(m.id))}>
                    Trừ 1 buổi
                  </Button>
                )}
                <Button className="px-2 py-0.5 text-xs" disabled={busy} onClick={() => void run(() => biz.updateMembership(m.id, { status: m.status === 'frozen' ? 'active' : 'frozen' }))}>
                  {m.status === 'frozen' ? 'Mở lại' : 'Tạm dừng'}
                </Button>
              </li>
            ))}
            {shownMemberships.length === 0 && <li className="px-3 py-4 text-sm text-zinc-500">Chưa có gói nào.</li>}
          </ul>
        </section>
      )}

      {tab === 'staff' && (
        <section className="mt-3">
          <div className="mb-2 flex justify-end">
            <Button variant="primary" onClick={() => setStaffEdit({ id: null, draft: { name: '', role: current?.industry === 'spa' ? 'therapist' : 'pt', working_hours: Object.fromEntries(DAYS.slice(0, 6).map(([d]) => [d, [['08:00', '12:00'], ['14:00', '20:00']]])), branch_id: branches[0]?.id ?? null, phone: '', active: true } })}>
              Thêm nhân viên
            </Button>
          </div>
          <ul className="divide-y divide-zinc-100 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900" data-testid="staff-list">
            {(staff ?? []).map((s) => (
              <li key={s.id} className={`flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm ${s.active ? '' : 'opacity-50'}`}>
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{s.name}</span>
                  <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] dark:bg-zinc-800">{STAFF_ROLE_LABEL[s.role]}</span>
                  {s.branch_id && <span className="ml-2 text-xs text-zinc-500">{branchName(s.branch_id)}</span>}
                  <p className="text-xs text-zinc-500">{hoursText(s.working_hours)}</p>
                </div>
                <button type="button" className="text-xs text-sky-700 hover:underline dark:text-sky-400" onClick={() => setStaffEdit({ id: s.id, draft: { name: s.name, role: s.role, working_hours: s.working_hours, branch_id: s.branch_id, phone: s.phone, active: s.active } })}>
                  Sửa
                </button>
                {s.active && (
                  <button type="button" className="text-xs text-rose-600 hover:underline" onClick={() => window.confirm(`Ngừng làm việc: ${s.name}?`) && void run(() => biz.deleteStaff(s.id))}>
                    Nghỉ việc
                  </button>
                )}
              </li>
            ))}
            {(staff ?? []).length === 0 && <li className="px-3 py-4 text-sm text-zinc-500">Chưa có nhân viên.</li>}
          </ul>
        </section>
      )}

      {svcEdit && (
        <Modal title={svcEdit.id ? 'Sửa dịch vụ' : 'Thêm dịch vụ'} onClose={() => setSvcEdit(null)}>
          <form onSubmit={(e) => void saveService(e)} className="space-y-3">
            <div>
              <label htmlFor="s-name" className={label}>Tên</label>
              <input id="s-name" className={field} value={svcEdit.draft.name} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, name: e.target.value } })} required autoFocus />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="s-cat" className={label}>Loại</label>
                <select id="s-cat" className={field} value={svcEdit.draft.category} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, category: e.target.value as ServiceCategory } })}>
                  {(Object.keys(CATEGORY_LABEL) as ServiceCategory[]).map((k) => (
                    <option key={k} value={k}>
                      {CATEGORY_LABEL[k]}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="s-price" className={label}>Giá (đ)</label>
                <input id="s-price" type="number" min={0} className={field} value={svcEdit.draft.price} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, price: Number(e.target.value) } })} />
              </div>
              <div>
                <label htmlFor="s-sessions" className={label}>Số buổi (0 = theo thời gian)</label>
                <input id="s-sessions" type="number" min={0} className={field} value={svcEdit.draft.sessions} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, sessions: Number(e.target.value) } })} />
              </div>
              <div>
                <label htmlFor="s-days" className={label}>Hiệu lực (ngày)</label>
                <input id="s-days" type="number" min={0} className={field} value={svcEdit.draft.validity_days} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, validity_days: Number(e.target.value) } })} />
              </div>
              <div>
                <label htmlFor="s-dur" className={label}>Thời lượng mỗi buổi (phút)</label>
                <input id="s-dur" type="number" min={0} className={field} value={svcEdit.draft.duration_min} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, duration_min: Number(e.target.value) } })} />
              </div>
              <label className="mt-5 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={svcEdit.draft.active} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, active: e.target.checked } })} /> Đang bán
              </label>
            </div>
            <div>
              <label htmlFor="s-desc" className={label}>Mô tả cho khách</label>
              <textarea id="s-desc" className={`${field} h-16`} value={svcEdit.draft.description} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, description: e.target.value } })} />
            </div>
            <div>
              <label htmlFor="s-notes" className={label}>Ghi chú riêng cho AI (khách không thấy)</label>
              <textarea id="s-notes" className={`${field} h-16`} placeholder="Ví dụ: khách hỏi giảm giá thì gợi ý gói 3 tháng" value={svcEdit.draft.sale_notes} onChange={(e) => setSvcEdit({ ...svcEdit, draft: { ...svcEdit.draft, sale_notes: e.target.value } })} />
            </div>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setSvcEdit(null)}>Huỷ</Button>
              <Button type="submit" variant="primary" disabled={busy || !svcEdit.draft.name.trim()}>
                Lưu
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {staffEdit && (
        <Modal title={staffEdit.id ? 'Sửa nhân viên' : 'Thêm nhân viên'} onClose={() => setStaffEdit(null)}>
          <form onSubmit={(e) => void saveStaff(e)} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label htmlFor="st-name" className={label}>Tên</label>
                <input id="st-name" className={field} value={staffEdit.draft.name} onChange={(e) => setStaffEdit({ ...staffEdit, draft: { ...staffEdit.draft, name: e.target.value } })} required autoFocus />
              </div>
              <div>
                <label htmlFor="st-role" className={label}>Vai trò</label>
                <select id="st-role" className={field} value={staffEdit.draft.role} onChange={(e) => setStaffEdit({ ...staffEdit, draft: { ...staffEdit.draft, role: e.target.value as StaffRole } })}>
                  {(Object.keys(STAFF_ROLE_LABEL) as StaffRole[]).map((k) => (
                    <option key={k} value={k}>
                      {STAFF_ROLE_LABEL[k]}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="st-branch" className={label}>Chi nhánh</label>
                <select id="st-branch" className={field} value={staffEdit.draft.branch_id ?? ''} onChange={(e) => setStaffEdit({ ...staffEdit, draft: { ...staffEdit.draft, branch_id: e.target.value || null } })}>
                  <option value="">—</option>
                  {branches.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="col-span-2">
                <label htmlFor="st-phone" className={label}>Điện thoại</label>
                <input id="st-phone" className={field} value={staffEdit.draft.phone} onChange={(e) => setStaffEdit({ ...staffEdit, draft: { ...staffEdit.draft, phone: e.target.value } })} />
              </div>
            </div>
            <fieldset>
              <legend className={label}>Giờ làm (HH:MM-HH:MM, nhiều ca cách nhau bằng dấu phẩy; để trống = nghỉ)</legend>
              <div className="mt-1 grid grid-cols-[2.5rem_1fr] gap-x-2 gap-y-1">
                {DAYS.map(([d, l]) => (
                  <label key={d} className="contents text-xs">
                    <span className="self-center">{l}</span>
                    <input
                      aria-label={`Giờ làm ${l}`}
                      className="rounded border border-zinc-300 px-2 py-1 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
                      placeholder="08:00-12:00, 14:00-20:00"
                      value={(staffEdit.draft.working_hours[d] ?? []).map((s) => s.join('-')).join(', ')}
                      onChange={(e) => {
                        const spans = e.target.value
                          .split(',')
                          .map((s) => s.trim())
                          .filter(Boolean)
                          .map((s) => s.split('-').map((x) => x.trim()))
                        const wh = { ...staffEdit.draft.working_hours }
                        if (spans.length) wh[d] = spans
                        else delete wh[d]
                        setStaffEdit({ ...staffEdit, draft: { ...staffEdit.draft, working_hours: wh } })
                      }}
                    />
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setStaffEdit(null)}>Huỷ</Button>
              <Button type="submit" variant="primary" disabled={busy || !staffEdit.draft.name.trim()}>
                Lưu
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {sell && (
        <Modal title="Bán gói cho khách" onClose={() => setSell(null)}>
          <form onSubmit={(e) => void saveSell(e)} className="space-y-3">
            <div>
              <label htmlFor="sell-customer" className={label}>Khách</label>
              <select id="sell-customer" className={field} value={sell.customer_id} onChange={(e) => setSell({ ...sell, customer_id: e.target.value })}>
                {sell.customers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} {c.phone ? `· ${c.phone}` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="sell-service" className={label}>Gói / dịch vụ</label>
              <select id="sell-service" className={field} value={sell.service_id} onChange={(e) => setSell({ ...sell, service_id: e.target.value, amount_paid: String(services?.find((s) => s.id === e.target.value)?.price ?? 0) })}>
                {(services ?? []).filter((s) => s.active).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {money(s.price)}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="sell-start" className={label}>Ngày bắt đầu</label>
                <input id="sell-start" type="date" className={field} value={sell.start_date} onChange={(e) => setSell({ ...sell, start_date: e.target.value })} />
              </div>
              <div>
                <label htmlFor="sell-paid" className={label}>Đã thanh toán (đ)</label>
                <input id="sell-paid" type="number" min={0} className={field} value={sell.amount_paid} onChange={(e) => setSell({ ...sell, amount_paid: e.target.value })} />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setSell(null)}>Huỷ</Button>
              <Button type="submit" variant="primary" disabled={busy || !sell.customer_id || !sell.service_id}>
                Lưu
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {importing && <ImportWizard entity={importing} onClose={() => setImporting(null)} onDone={() => void load()} />}
    </div>
  )
}
