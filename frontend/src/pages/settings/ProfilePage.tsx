import { useState, type FormEvent } from 'react'
import { ApiError, api } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import { Button, ErrorBanner } from '../../components/ui'
import { formatDateVi } from '../../lib/format'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

/** Tài khoản: account details and password change. */
export function ProfilePage() {
  const { user } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setDone(false)
    if (next.length < 8) return setError('Mật khẩu mới cần ít nhất 8 ký tự.')
    if (next !== confirm) return setError('Mật khẩu nhập lại không khớp.')
    setBusy(true)
    try {
      await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setDone(true)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 400 ? 'Mật khẩu hiện tại không đúng.' : (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl p-4 sm:p-6">
      <h1 className="mb-4 text-2xl font-semibold">Tài khoản</h1>
      <dl className="mb-6 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
        <dt className="text-zinc-500">Email</dt>
        <dd>{user?.email}</dd>
        <dt className="text-zinc-500">Tham gia từ</dt>
        <dd>{formatDateVi(user?.created_at, true)}</dd>
        {user?.is_admin && (
          <>
            <dt className="text-zinc-500">Vai trò</dt>
            <dd>Quản trị hệ thống</dd>
          </>
        )}
      </dl>

      <form onSubmit={(e) => void submit(e)} className="space-y-3 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <h2 className="text-sm font-semibold">Đổi mật khẩu</h2>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        {done && <p role="status" className="text-sm text-emerald-600">Đã đổi mật khẩu.</p>}
        <div>
          <label htmlFor="pw-current" className={label}>Mật khẩu hiện tại</label>
          <input id="pw-current" type="password" autoComplete="current-password" className={field} value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div>
          <label htmlFor="pw-new" className={label}>Mật khẩu mới</label>
          <input id="pw-new" type="password" autoComplete="new-password" className={field} value={next} onChange={(e) => setNext(e.target.value)} />
        </div>
        <div>
          <label htmlFor="pw-confirm" className={label}>Nhập lại mật khẩu mới</label>
          <input id="pw-confirm" type="password" autoComplete="new-password" className={field} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={busy || !current || !next || !confirm}>
            {busy ? 'Đang lưu…' : 'Đổi mật khẩu'}
          </Button>
        </div>
      </form>
    </div>
  )
}
