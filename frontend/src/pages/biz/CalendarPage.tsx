import { useCallback, useEffect, useMemo, useState, type DragEvent, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import { APPOINTMENT_LABEL, biz, type Appointment, type AppointmentStatus, type Customer, type Service, type Staff } from '../../api/biz'
import { Button, ErrorBanner } from '../../components/ui'
import { useWorkspace } from '../../lib/WorkspaceContext'

const HOUR_START = 6
const HOUR_END = 22
const PX_PER_HOUR = 56
const STEP_MIN = 15
const DOW = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN']
const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

const STATUS_CLS: Record<AppointmentStatus, string> = {
  booked: 'border-sky-300 bg-sky-100 text-sky-900 dark:border-sky-700 dark:bg-sky-950 dark:text-sky-100',
  confirmed: 'border-emerald-300 bg-emerald-100 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100',
  arrived: 'border-emerald-500 bg-emerald-200 text-emerald-900 dark:border-emerald-500 dark:bg-emerald-900 dark:text-emerald-50',
  done: 'border-zinc-300 bg-zinc-200 text-zinc-700 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200',
  cancelled: 'border-zinc-200 bg-zinc-50 text-zinc-400 line-through dark:border-zinc-800 dark:bg-zinc-900',
  no_show: 'border-rose-300 bg-rose-100 text-rose-900 dark:border-rose-700 dark:bg-rose-950 dark:text-rose-100',
}

const pad = (n: number) => String(n).padStart(2, '0')
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const parseYmd = (s: string) => {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1)
}
const addDays = (d: Date, n: number) => {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}
const mondayOf = (d: Date) => addDays(d, -((d.getDay() + 6) % 7))
const minutesOfDay = (iso: string) => {
  const d = new Date(iso)
  return d.getHours() * 60 + d.getMinutes()
}
const hhmm = (iso: string) => {
  const d = new Date(iso)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}
const dmy = (d: Date) => `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`

interface Column {
  key: string
  label: string
  sub?: string
  date: Date
  /** Day view: the staff member of this column (`null` = unassigned). Week view: undefined (keep staff). */
  staffId?: string | null
}

/** Lịch: day / week grid by staff and branch, drag-drop rescheduling, arrived / no-show marking, quick booking. */
export function CalendarPage() {
  const { current: workspace } = useWorkspace()
  const [view, setView] = useState<'day' | 'week'>('week')
  const [date, setDate] = useState<string>(() => ymd(new Date()))
  const [staffFilter, setStaffFilter] = useState('')
  const [branchFilter, setBranchFilter] = useState('')
  const [staff, setStaff] = useState<Staff[]>([])
  const [services, setServices] = useState<Service[]>([])
  const [appointments, setAppointments] = useState<Appointment[]>([])
  const [selected, setSelected] = useState<Appointment | null>(null)
  const [booking, setBooking] = useState<{ date: string; time: string; staffId: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const day = useMemo(() => parseYmd(date), [date])
  const weekStart = useMemo(() => mondayOf(day), [day])
  const branches = workspace?.branches ?? []

  const load = useCallback(async () => {
    try {
      const params: Parameters<typeof biz.listAppointments>[0] = { staff_id: staffFilter || undefined, branch_id: branchFilter || undefined }
      if (view === 'day') params.day = date
      else {
        params.start = weekStart.toISOString()
        params.end = addDays(weekStart, 7).toISOString()
      }
      setAppointments(await biz.listAppointments(params))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [view, date, weekStart, staffFilter, branchFilter])

  useEffect(() => {
    void Promise.all([biz.listStaff(), biz.listServices()])
      .then(([s, sv]) => {
        setStaff(s)
        setServices(sv)
      })
      .catch((e: Error) => setError(e.message))
  }, [])
  useEffect(() => {
    void load()
    const t = setInterval(() => void load(), 30_000)
    return () => clearInterval(t)
  }, [load])

  const visibleStaff = staff.filter((s) => (!branchFilter || s.branch_id === branchFilter) && (!staffFilter || s.id === staffFilter))
  const columns: Column[] = useMemo(() => {
    if (view === 'week') return Array.from({ length: 7 }, (_, i) => ({ key: `d${i}`, label: DOW[i] ?? '', sub: dmy(addDays(weekStart, i)), date: addDays(weekStart, i) }))
    const cols: Column[] = visibleStaff.map((s) => ({ key: s.id, label: s.name, date: day, staffId: s.id }))
    if (!staffFilter) cols.push({ key: 'none', label: 'Chưa phân', date: day, staffId: null })
    return cols
    // eslint-disable-next-line react-hooks/exhaustive-deps -- visibleStaff derives from staff + filters
  }, [view, weekStart, day, staff, staffFilter, branchFilter])

  const inColumn = (a: Appointment, col: Column) => {
    if (view === 'week') return ymd(new Date(a.start_at)) === ymd(col.date)
    return (a.staff_id ?? null) === col.staffId
  }

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const move = async (id: string, body: Record<string, unknown>) => {
    try {
      await biz.updateAppointment(id, body)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && window.confirm('Nhân viên này đã có lịch trùng giờ. Vẫn chuyển?')) {
        await biz.updateAppointment(id, body, true)
        return
      }
      throw e
    }
  }
  const onDrop = (e: DragEvent<HTMLDivElement>, col: Column) => {
    e.preventDefault()
    const id = e.dataTransfer.getData('text/plain')
    const a = appointments.find((x) => x.id === id)
    if (!a) return
    const rect = e.currentTarget.getBoundingClientRect()
    const y = Math.max(0, e.clientY - rect.top)
    const minutes = HOUR_START * 60 + Math.round(((y / PX_PER_HOUR) * 60) / STEP_MIN) * STEP_MIN
    const start = new Date(col.date)
    start.setHours(0, minutes, 0, 0)
    const duration = new Date(a.end_at).getTime() - new Date(a.start_at).getTime()
    const body: Record<string, unknown> = { start_at: start.toISOString(), end_at: new Date(start.getTime() + duration).toISOString() }
    if (col.staffId) body.staff_id = col.staffId
    void run(() => move(id, body))
  }
  const setStatus = (a: Appointment, status: AppointmentStatus) =>
    run(async () => {
      await biz.updateAppointment(a.id, { status })
      setSelected(null)
    })

  const shift = (n: number) => setDate(ymd(addDays(day, view === 'day' ? n : 7 * n)))
  const hours = Array.from({ length: HOUR_END - HOUR_START }, (_, i) => HOUR_START + i)
  const now = new Date()
  const nowTop = ((now.getHours() * 60 + now.getMinutes() - HOUR_START * 60) / 60) * PX_PER_HOUR
  const title = view === 'day' ? `${DOW[(day.getDay() + 6) % 7]}, ${pad(day.getDate())}/${pad(day.getMonth() + 1)}/${day.getFullYear()}` : `Tuần ${dmy(weekStart)} – ${dmy(addDays(weekStart, 6))}`

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col p-3 sm:p-4 md:h-screen">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-xl font-semibold">Lịch</h1>
        <div className="flex items-center gap-1">
          <Button className="px-2 py-1" onClick={() => shift(-1)} aria-label="Trước">
            ‹
          </Button>
          <Button className="px-2 py-1" onClick={() => setDate(ymd(new Date()))}>
            Hôm nay
          </Button>
          <Button className="px-2 py-1" onClick={() => shift(1)} aria-label="Sau">
            ›
          </Button>
        </div>
        <span className="text-sm font-medium" data-testid="calendar-title">
          {title}
        </span>
        <input type="date" aria-label="Chọn ngày" className="rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <select aria-label="Chế độ xem" className="rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900" value={view} onChange={(e) => setView(e.target.value as 'day' | 'week')}>
            <option value="day">Ngày</option>
            <option value="week">Tuần</option>
          </select>
          {branches.length > 1 && (
            <select aria-label="Chi nhánh" className="rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900" value={branchFilter} onChange={(e) => setBranchFilter(e.target.value)}>
              <option value="">Mọi chi nhánh</option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          )}
          <select aria-label="Nhân viên" className="rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900" value={staffFilter} onChange={(e) => setStaffFilter(e.target.value)}>
            <option value="">Mọi nhân viên</option>
            {staff.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <Button variant="primary" onClick={() => setBooking({ date, time: '09:00', staffId: staffFilter })}>
            + Đặt lịch
          </Button>
        </div>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800" data-testid="calendar-grid">
        <div className="flex min-w-full">
          <div className="sticky left-0 z-10 w-12 shrink-0 bg-white dark:bg-zinc-950">
            <div className="h-10 border-b border-zinc-200 dark:border-zinc-800" />
            {hours.map((h) => (
              <div key={h} className="border-b border-zinc-100 pr-1 text-right text-[10px] text-zinc-500 dark:border-zinc-800" style={{ height: PX_PER_HOUR }}>
                {pad(h)}:00
              </div>
            ))}
          </div>
          {columns.map((col) => {
            const items = appointments.filter((a) => inColumn(a, col))
            const isToday = ymd(col.date) === ymd(now)
            return (
              <div key={col.key} className="min-w-[9.5rem] flex-1 border-l border-zinc-200 dark:border-zinc-800" data-testid={`col-${col.key}`}>
                <div className={`sticky top-0 z-10 flex h-10 flex-col justify-center border-b border-zinc-200 bg-white px-2 text-xs font-semibold dark:border-zinc-800 dark:bg-zinc-950 ${isToday && view === 'week' ? 'text-sky-700 dark:text-sky-300' : ''}`}>
                  <span className="truncate">{col.label}</span>
                  {col.sub && <span className="text-[10px] font-normal text-zinc-500">{col.sub}</span>}
                </div>
                <div
                  className="relative"
                  style={{ height: hours.length * PX_PER_HOUR }}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => onDrop(e, col)}
                  onDoubleClick={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect()
                    const minutes = HOUR_START * 60 + Math.floor(((e.clientY - rect.top) / PX_PER_HOUR) * 4) * 15
                    setBooking({ date: ymd(col.date), time: `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`, staffId: col.staffId ?? staffFilter })
                  }}
                >
                  {hours.map((h) => (
                    <div key={h} className="border-b border-zinc-100 dark:border-zinc-800" style={{ height: PX_PER_HOUR }} />
                  ))}
                  {isToday && nowTop >= 0 && nowTop <= hours.length * PX_PER_HOUR && <div className="pointer-events-none absolute inset-x-0 border-t-2 border-rose-500" style={{ top: nowTop }} />}
                  {items.map((a) => {
                    const top = ((minutesOfDay(a.start_at) - HOUR_START * 60) / 60) * PX_PER_HOUR
                    const height = Math.max(22, ((new Date(a.end_at).getTime() - new Date(a.start_at).getTime()) / 3_600_000) * PX_PER_HOUR - 2)
                    return (
                      <button
                        type="button"
                        key={a.id}
                        draggable={a.status !== 'cancelled' && a.status !== 'done'}
                        onDragStart={(e) => e.dataTransfer.setData('text/plain', a.id)}
                        onClick={() => setSelected(a)}
                        className={`absolute inset-x-1 overflow-hidden rounded border px-1.5 py-0.5 text-left text-[11px] leading-tight shadow-sm ${STATUS_CLS[a.status]}`}
                        style={{ top, height }}
                        title={`${hhmm(a.start_at)}–${hhmm(a.end_at)} ${a.customer_name}`}
                        data-testid="appointment"
                      >
                        <span className="font-semibold">{hhmm(a.start_at)}</span> {a.customer_name}
                        {a.service_name && <span className="block truncate opacity-80">{a.service_name}</span>}
                        {view === 'week' && a.staff_name && <span className="block truncate opacity-70">{a.staff_name}</span>}
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      </div>
      <p className="mt-2 text-[11px] text-zinc-500">Kéo thả lịch để đổi giờ hoặc nhân viên. Bấm đúp vào ô trống để đặt nhanh. Bấm vào lịch để đánh dấu đã đến / không đến.</p>

      {selected && (
        <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/40 p-4 sm:items-center" onClick={() => setSelected(null)}>
          <div className="w-full max-w-md rounded-lg bg-white p-4 shadow-xl dark:bg-zinc-900" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Chi tiết lịch">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h2 className="text-lg font-semibold">{selected.customer_name}</h2>
                <p className="text-sm text-zinc-500">
                  {hhmm(selected.start_at)}–{hhmm(selected.end_at)} · {selected.service_name ?? 'dịch vụ'} · {selected.staff_name ?? 'chưa phân'}
                </p>
                <p className="mt-1 text-xs">
                  Trạng thái: <b>{APPOINTMENT_LABEL[selected.status]}</b> · nguồn {selected.source === 'ai' ? 'AI đặt' : selected.source === 'staff' ? 'nhân viên' : 'thủ công'}
                </p>
                {selected.notes && <p className="mt-1 text-xs text-zinc-500">{selected.notes}</p>}
              </div>
              <button type="button" className="text-sm text-zinc-500" onClick={() => setSelected(null)}>
                Đóng
              </button>
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button variant="primary" disabled={busy || selected.status === 'arrived'} onClick={() => void setStatus(selected, 'arrived')}>
                Đã đến
              </Button>
              <Button disabled={busy || selected.status === 'no_show'} onClick={() => void setStatus(selected, 'no_show')}>
                Không đến
              </Button>
              <Button disabled={busy || selected.status === 'confirmed'} onClick={() => void setStatus(selected, 'confirmed')}>
                Xác nhận
              </Button>
              <Button disabled={busy || selected.status === 'done'} onClick={() => void setStatus(selected, 'done')}>
                Hoàn thành
              </Button>
              <Button variant="ghost" className="text-rose-600" disabled={busy || selected.status === 'cancelled'} onClick={() => void setStatus(selected, 'cancelled')}>
                Huỷ lịch
              </Button>
            </div>
          </div>
        </div>
      )}

      {booking && (
        <BookingModal
          initial={booking}
          staff={staff}
          services={services}
          branches={branches}
          busy={busy}
          onClose={() => setBooking(null)}
          onSubmit={(body) =>
            run(async () => {
              try {
                await biz.createAppointment(body)
              } catch (e) {
                if (e instanceof ApiError && e.status === 409 && window.confirm('Nhân viên này đã có lịch trùng giờ. Vẫn đặt?')) await biz.createAppointment(body, true)
                else throw e
              }
              setBooking(null)
            })
          }
        />
      )}
    </div>
  )
}

function BookingModal({
  initial,
  staff,
  services,
  branches,
  busy,
  onClose,
  onSubmit,
}: {
  initial: { date: string; time: string; staffId: string }
  staff: Staff[]
  services: Service[]
  branches: { id: string; name: string }[]
  busy: boolean
  onClose: () => void
  onSubmit: (body: Record<string, unknown>) => Promise<void>
}) {
  const [query, setQuery] = useState('')
  const [customers, setCustomers] = useState<Customer[]>([])
  const [customerId, setCustomerId] = useState('')
  const [serviceId, setServiceId] = useState('')
  const [staffId, setStaffId] = useState(initial.staffId)
  const [branchId, setBranchId] = useState(branches[0]?.id ?? '')
  const [date, setDate] = useState(initial.date)
  const [time, setTime] = useState(initial.time)

  useEffect(() => {
    const t = setTimeout(() => {
      void biz
        .listCustomers({ q: query || undefined, limit: 20 })
        .then(setCustomers)
        .catch(() => setCustomers([]))
    }, 200)
    return () => clearTimeout(t)
  }, [query])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!customerId) return
    const start = parseYmd(date)
    const [h, m] = time.split(':').map(Number)
    start.setHours(h ?? 9, m ?? 0, 0, 0)
    void onSubmit({ customer_id: customerId, service_id: serviceId || null, staff_id: staffId || null, branch_id: branchId || null, start_at: start.toISOString() })
  }

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/40 p-4 sm:items-center" onClick={onClose}>
      <form onSubmit={submit} className="w-full max-w-md space-y-3 rounded-lg bg-white p-4 shadow-xl dark:bg-zinc-900" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Đặt lịch">
        <h2 className="text-lg font-semibold">Đặt lịch</h2>
        <div>
          <label htmlFor="bk-q" className={label}>
            Khách hàng
          </label>
          <input id="bk-q" className={field} placeholder="Tìm theo tên hoặc SĐT…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select aria-label="Chọn khách" className={field} value={customerId} onChange={(e) => setCustomerId(e.target.value)} required>
            <option value="">— chọn khách —</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} {c.phone ? `· ${c.phone}` : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label htmlFor="bk-service" className={label}>
              Dịch vụ
            </label>
            <select id="bk-service" className={field} value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
              <option value="">— không —</option>
              {services.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="bk-staff" className={label}>
              Nhân viên
            </label>
            <select id="bk-staff" className={field} value={staffId} onChange={(e) => setStaffId(e.target.value)}>
              <option value="">— chưa phân —</option>
              {staff.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="bk-date" className={label}>
              Ngày
            </label>
            <input id="bk-date" type="date" className={field} value={date} onChange={(e) => setDate(e.target.value)} required />
          </div>
          <div>
            <label htmlFor="bk-time" className={label}>
              Giờ
            </label>
            <input id="bk-time" type="time" step={900} className={field} value={time} onChange={(e) => setTime(e.target.value)} required />
          </div>
          {branches.length > 1 && (
            <div className="col-span-2">
              <label htmlFor="bk-branch" className={label}>
                Chi nhánh
              </label>
              <select id="bk-branch" className={field} value={branchId} onChange={(e) => setBranchId(e.target.value)}>
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose}>
            Huỷ
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !customerId}>
            Đặt lịch
          </Button>
        </div>
      </form>
    </div>
  )
}
