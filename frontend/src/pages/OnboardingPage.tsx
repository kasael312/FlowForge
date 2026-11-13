import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { OnboardingAnswers, OnboardingQuestions } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { Button, ErrorBanner } from '../components/ui'
import { useWorkspace } from '../lib/WorkspaceContext'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-sm font-medium text-zinc-700 dark:text-zinc-300'

/** Four questions after sign-up → creates the workspace (Vietnamese, phone-friendly). */
export function OnboardingPage() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const { current, reload } = useWorkspace()
  const [questions, setQuestions] = useState<OnboardingQuestions | null>(null)
  const [answers, setAnswers] = useState<OnboardingAnswers>({ name: '', industry: 'gym', branch_count: 1, main_channel: 'zalo', current_software: '' })
  const [branchCount, setBranchCount] = useState('1')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .onboardingQuestions()
      .then((q) => {
        setQuestions(q)
        const first = q.questions.find((x) => x.key === 'industry')?.default
        if (typeof first === 'string') setAnswers((a) => ({ ...a, industry: first }))
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.onboard({ ...answers, branch_count: Math.min(50, Math.max(1, Number(branchCount) || 1)) })
      await reload()
      navigate('/', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const industries = questions?.industries ?? []
  const channel = questions?.questions.find((q) => q.key === 'main_channel')

  return (
    <div className="mx-auto max-w-lg p-4 sm:p-8">
      <h1 className="text-2xl font-semibold">Thiết lập cơ sở của bạn</h1>
      <p className="mt-1 text-sm text-zinc-500">Trả lời 4 câu ngắn, FlowForge sẽ chuẩn bị sẵn các việc tự động phù hợp. Bạn đổi được mọi thứ sau trong Cài đặt.</p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <form onSubmit={(e) => void submit(e)} className="mt-6 space-y-5">
        <div>
          <label htmlFor="ob-name" className={label}>Tên cơ sở</label>
          <input id="ob-name" className={field} placeholder="Ví dụ: Gym Sức Sống, Spa Hoa Sen" value={answers.name} onChange={(e) => setAnswers({ ...answers, name: e.target.value })} required autoFocus />
        </div>
        <fieldset>
          <legend className={label}>1. Bạn kinh doanh loại hình nào?</legend>
          <div className="mt-2 grid gap-2 sm:grid-cols-3" data-testid="industry-options">
            {industries.map((i) => (
              <label key={i.key} className={`cursor-pointer rounded-lg border p-3 text-sm ${answers.industry === i.key ? 'border-zinc-900 bg-zinc-50 dark:border-zinc-100 dark:bg-zinc-800' : 'border-zinc-300 dark:border-zinc-700'}`}>
                <input type="radio" name="industry" value={i.key} className="mr-2" checked={answers.industry === i.key} onChange={() => setAnswers({ ...answers, industry: i.key })} />
                <span className="font-medium">{i.label}</span>
                <p className="mt-1 text-xs text-zinc-500">{i.description}</p>
              </label>
            ))}
            {industries.length === 0 && <p className="text-sm text-zinc-500">Đang tải…</p>}
          </div>
        </fieldset>
        <div>
          <label htmlFor="ob-branches" className={label}>2. Bạn có bao nhiêu chi nhánh?</label>
          <input id="ob-branches" type="number" min={1} max={50} className={field} value={branchCount} onChange={(e) => setBranchCount(e.target.value)} />
        </div>
        <div>
          <label htmlFor="ob-channel" className={label}>3. {channel?.label ?? 'Khách hàng nhắn tin cho bạn chủ yếu qua kênh nào?'}</label>
          <select id="ob-channel" className={field} value={answers.main_channel} onChange={(e) => setAnswers({ ...answers, main_channel: e.target.value as OnboardingAnswers['main_channel'] })}>
            {(channel?.options ?? [{ value: 'zalo', label: 'Zalo OA' }, { value: 'facebook', label: 'Facebook Messenger' }, { value: 'both', label: 'Cả hai' }]).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="ob-software" className={label}>4. Bạn đang dùng phần mềm quản lý nào? (không bắt buộc)</label>
          <input id="ob-software" className={field} placeholder="Ví dụ: Excel, KiotViet, phần mềm riêng…" value={answers.current_software} onChange={(e) => setAnswers({ ...answers, current_software: e.target.value })} />
        </div>
        <div className="flex items-center justify-between gap-3">
          {(user?.is_admin || current) && (
            <Link to="/" className="text-sm text-zinc-500 underline">
              Bỏ qua
            </Link>
          )}
          <Button type="submit" variant="primary" disabled={busy || !answers.name.trim()} className="ml-auto">
            {busy ? 'Đang tạo…' : 'Bắt đầu'}
          </Button>
        </div>
      </form>
    </div>
  )
}
