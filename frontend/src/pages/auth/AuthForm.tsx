import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { Button, ErrorBanner } from '../../components/ui'

interface Props {
  mode: 'login' | 'register'
  onSubmit: (email: string, password: string) => Promise<void>
}

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

/** Shared email + password form for login and registration (Vietnamese). */
export function AuthForm({ mode, onSubmit }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const isLogin = mode === 'login'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!isLogin && password.length < 8) {
      setError('Mật khẩu cần ít nhất 8 ký tự.')
      return
    }
    setBusy(true)
    try {
      await onSubmit(email.trim(), password)
    } catch (err) {
      setError(describe(err, mode))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto mt-16 w-full max-w-sm px-4">
      <h1 className="mb-1 text-2xl font-semibold">{isLogin ? 'Đăng nhập' : 'Tạo tài khoản'}</h1>
      <p className="mb-5 text-sm text-zinc-500">
        {isLogin ? (
          <>
            Chưa có tài khoản? <Link to="/register" className="underline">Đăng ký</Link>
          </>
        ) : (
          <>
            Đã có tài khoản? <Link to="/login" className="underline">Đăng nhập</Link>
          </>
        )}
      </p>
      <form onSubmit={(e) => void submit(e)} className="space-y-3" noValidate>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <div>
          <label htmlFor="email" className={label}>Email</label>
          <input id="email" type="email" autoComplete="email" required className={field} value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label htmlFor="password" className={label}>Mật khẩu</label>
          <input
            id="password"
            type="password"
            autoComplete={isLogin ? 'current-password' : 'new-password'}
            required
            minLength={isLogin ? undefined : 8}
            className={field}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {!isLogin && <p className="mt-1 text-[11px] text-zinc-500">Ít nhất 8 ký tự.</p>}
        </div>
        <Button type="submit" variant="primary" className="w-full" disabled={busy || !email || !password}>
          {busy ? 'Đang xử lý…' : isLogin ? 'Đăng nhập' : 'Tạo tài khoản'}
        </Button>
      </form>
    </div>
  )
}

function describe(err: unknown, mode: 'login' | 'register'): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return 'Email hoặc mật khẩu không đúng.'
    if (err.status === 403) return 'Hiện không nhận đăng ký mới. Hãy liên hệ quản trị viên.'
    if (err.status === 409) return 'Email này đã được đăng ký. Hãy đăng nhập.'
    if (err.status === 422) return err.message.includes('email') ? 'Email không hợp lệ.' : err.message
    if (err.status === 429) return 'Bạn nhập sai quá nhiều lần, vui lòng thử lại sau ít phút.'
    if (err.status >= 500) return 'Máy chủ đang bận. Vui lòng thử lại sau.'
    return err.message
  }
  return mode === 'login' ? 'Không đăng nhập được. Kiểm tra kết nối mạng.' : 'Không tạo được tài khoản. Kiểm tra kết nối mạng.'
}
