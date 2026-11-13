import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { APPOINTMENT_LABEL, biz, type AppointmentStatus, type Overview } from '../../api/biz'
import { billing, type BillingState } from '../../api/billing'
import { ErrorBanner } from '../../components/ui'
import { formatDateVi } from '../../lib/format'
import { useWorkspace } from '../../lib/WorkspaceContext'

function Tile({ label, value, hint, to }: { label: string; value: string; hint?: string; to?: string }) {
  const body = (
    <div className="h-full rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 text-3xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-zinc-500">{hint}</div>}
    </div>
  )
  return to ? (
    <Link to={to} className="block hover:opacity-90">
      {body}
    </Link>
  ) : (
    body
  )
}

/** Tổng quan: the owner's home — today, no-shows, expiring packages, inbox and time saved by AI. */
export function OverviewPage() {
  const { current } = useWorkspace()
  const [data, setData] = useState<Overview | null>(null)
  const [bill, setBill] = useState<BillingState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      setData(await biz.overview())
      setBill(await billing.get().catch(() => null))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
    const t = setInterval(() => void load(), 60_000)
    return () => clearInterval(t)
  }, [load])

  const dismiss = async (id: string) => {
    try {
      await biz.markNotificationRead(id)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="mx-auto max-w-6xl p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">Tổng quan</h1>
          <p className="text-sm text-zinc-500">
            {current?.name}
            {data ? ` · ${data.date}` : ''}
          </p>
        </div>
        <Link to="/calendar" className="text-sm underline">
          Mở lịch
        </Link>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {bill && bill.warnings.length > 0 && (
        <div className={`mb-4 rounded-md border px-3 py-2 text-sm ${bill.blocked ? 'border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-100' : 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100'}`} role="status" data-testid="billing-banner">
          {bill.warnings[0]}{' '}
          <Link to="/settings/billing" className="font-medium underline">
            Gói & thanh toán
          </Link>
        </div>
      )}
      {!data ? (
        <p className="text-sm text-zinc-500">Đang tải…</p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" data-testid="overview-tiles">
            <Tile label="Lịch hôm nay" value={String(data.appointments_today)} hint={`${data.arrived_today} đã đến`} to="/calendar" />
            <Tile label="Tỉ lệ không đến" value={data.no_show_rate === null ? '—' : `${Math.round(data.no_show_rate * 100)}%`} hint={`${data.no_show_30d} lượt trong 30 ngày`} />
            <Tile label="Gói sắp hết hạn" value={String(data.expiring_memberships)} hint="trong 7 ngày hoặc còn 2 buổi" to="/services" />
            <Tile label="Tin chưa trả lời" value={String(data.unread_conversations)} hint={data.ai_suggestions ? `${data.ai_suggestions} gợi ý AI chờ duyệt` : 'AI đã lo hết'} to="/inbox" />
            <Tile label="Giờ tiết kiệm tuần này" value={`${data.hours_saved_week}h`} hint={`${data.ai_messages_week} tin AI đã xử lý × 1,5 phút`} />
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-2">
            <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="text-sm font-semibold">Lịch còn lại hôm nay</h2>
              {data.upcoming_today.length === 0 ? (
                <p className="mt-2 text-sm text-zinc-500">Không còn lịch nào hôm nay.</p>
              ) : (
                <ul className="mt-2 divide-y divide-zinc-100 text-sm dark:divide-zinc-800" data-testid="upcoming">
                  {data.upcoming_today.map((a) => (
                    <li key={a.id} className="flex items-center gap-3 py-1.5">
                      <span className="w-12 font-mono text-xs">{a.time}</span>
                      <span className="min-w-0 flex-1 truncate">
                        <b>{a.customer_name}</b>
                        {a.service_name ? ` · ${a.service_name}` : ''}
                        {a.staff_name ? ` · ${a.staff_name}` : ''}
                      </span>
                      <span className="text-xs text-zinc-500">{APPOINTMENT_LABEL[a.status as AppointmentStatus] ?? a.status}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="text-sm font-semibold">Thông báo</h2>
              {data.notifications.length === 0 ? (
                <p className="mt-2 text-sm text-zinc-500">Chưa có thông báo mới. Báo cáo cuối ngày sẽ xuất hiện ở đây.</p>
              ) : (
                <ul className="mt-2 space-y-2" data-testid="notifications">
                  {data.notifications.map((n) => (
                    <li key={n.id} className={`rounded border p-2 text-sm ${n.kind === 'alert' ? 'border-rose-300 dark:border-rose-800' : 'border-zinc-200 dark:border-zinc-800'}`}>
                      <div className="flex items-center justify-between gap-2">
                        <b>{n.title}</b>
                        <span className="text-[11px] text-zinc-500">{formatDateVi(n.created_at)}</span>
                      </div>
                      <pre className="mt-1 whitespace-pre-wrap font-sans text-xs text-zinc-600 dark:text-zinc-400">{n.body}</pre>
                      <button type="button" className="mt-1 text-xs text-sky-700 hover:underline dark:text-sky-400" onClick={() => void dismiss(n.id)}>
                        Đã đọc
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
          <p className="mt-4 text-xs text-zinc-500">
            {data.customers} khách hàng, {data.new_customers_week} khách mới tuần này.
          </p>
        </>
      )}
    </div>
  )
}
