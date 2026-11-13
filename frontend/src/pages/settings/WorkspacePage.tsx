import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import type { Branch, WorkspaceMember } from '../../api/types'
import { Button, ErrorBanner } from '../../components/ui'
import { useWorkspace } from '../../lib/WorkspaceContext'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

const ROLE_LABEL: Record<string, string> = { owner: 'Chủ cơ sở', staff: 'Nhân viên' }

/** Settings → Cơ sở: name, industry, branches and members (owner edits; staff reads). */
export function WorkspacePage() {
  const { current, all, reload, switchTo } = useWorkspace()
  const [name, setName] = useState('')
  const [industry, setIndustry] = useState('gym')
  const [timezone, setTimezone] = useState('Asia/Ho_Chi_Minh')
  const [branches, setBranches] = useState<Branch[]>([])
  const [members, setMembers] = useState<WorkspaceMember[]>([])
  const [industries, setIndustries] = useState<{ key: string; label: string }[]>([])
  const [invite, setInvite] = useState({ email: '', role: 'staff' })
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const isOwner = current?.role === 'owner'

  useEffect(() => {
    if (!current) return
    setName(current.name)
    setIndustry(current.industry)
    setTimezone(current.timezone)
    setBranches(current.branches)
  }, [current])

  const loadMembers = useCallback(async () => {
    if (!current) return
    try {
      setMembers(await api.listMembers())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [current])
  useEffect(() => {
    void loadMembers()
    api
      .onboardingQuestions()
      .then((q) => setIndustries(q.industries))
      .catch(() => setIndustries([]))
  }, [loadMembers])

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.updateWorkspace({ name: name.trim(), industry, timezone, branches })
      await reload()
      setNotice('Đã lưu.')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const addMember = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      await api.addMember(invite.email.trim(), invite.role)
      setInvite({ email: '', role: 'staff' })
      await loadMembers()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const setRole = async (m: WorkspaceMember, role: string) => {
    try {
      await api.updateMember(m.id, role)
      await Promise.all([loadMembers(), reload()])
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const removeMember = async (m: WorkspaceMember) => {
    if (!window.confirm(`Gỡ ${m.email} khỏi cơ sở?`)) return
    try {
      await api.removeMember(m.id)
      await loadMembers()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  if (current === undefined) return <p className="p-6 text-sm text-zinc-500">Đang tải…</p>
  if (current === null) return <p className="p-6 text-sm text-zinc-500">Bạn chưa có cơ sở nào.</p>

  return (
    <div className="mx-auto max-w-3xl p-4 sm:p-6">
      <h1 className="text-2xl font-semibold">Cơ sở</h1>
      <p className="text-sm text-zinc-500">Thông tin doanh nghiệp, chi nhánh và thành viên. Vai trò của bạn: <b>{ROLE_LABEL[current.role] ?? current.role}</b>.</p>
      {all.length > 1 && (
        <div className="mt-3">
          <label htmlFor="ws-switch" className={label}>Đang xem cơ sở</label>
          <select id="ws-switch" className={field} value={current.id} onChange={(e) => void switchTo(e.target.value)}>
            {all.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {notice && (
        <p className="mt-3 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" role="status">
          {notice}
        </p>
      )}

      <form onSubmit={(e) => void save(e)} className="mt-5 space-y-4 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="ws-name" className={label}>Tên cơ sở</label>
            <input id="ws-name" className={field} value={name} disabled={!isOwner} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div>
            <label htmlFor="ws-industry" className={label}>Loại hình</label>
            <select id="ws-industry" className={field} value={industry} disabled={!isOwner} onChange={(e) => setIndustry(e.target.value)}>
              {(industries.length ? industries : [{ key: current.industry, label: current.industry_label }]).map((i) => (
                <option key={i.key} value={i.key}>
                  {i.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="ws-tz" className={label}>Múi giờ</label>
            <input id="ws-tz" className={field} value={timezone} disabled={!isOwner} onChange={(e) => setTimezone(e.target.value)} />
          </div>
        </div>
        <fieldset>
          <div className="flex items-center justify-between">
            <legend className={label}>Chi nhánh ({branches.length})</legend>
            {isOwner && (
              <Button className="px-2 py-0.5 text-xs" onClick={() => setBranches([...branches, { id: '', name: `Chi nhánh ${branches.length + 1}`, address: '', phone: '' }])}>
                Thêm chi nhánh
              </Button>
            )}
          </div>
          <ul className="mt-2 space-y-2" data-testid="branches">
            {branches.map((b, i) => (
              <li key={b.id || `new-${i}`} className="grid gap-2 rounded border border-zinc-200 p-2 sm:grid-cols-[1fr_1fr_8rem_auto] dark:border-zinc-800">
                <input aria-label={`Tên chi nhánh ${i + 1}`} className={field} value={b.name} disabled={!isOwner} onChange={(e) => setBranches(branches.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                <input aria-label={`Địa chỉ chi nhánh ${i + 1}`} className={field} placeholder="Địa chỉ" value={b.address} disabled={!isOwner} onChange={(e) => setBranches(branches.map((x, j) => (j === i ? { ...x, address: e.target.value } : x)))} />
                <input aria-label={`Điện thoại chi nhánh ${i + 1}`} className={field} placeholder="Điện thoại" value={b.phone} disabled={!isOwner} onChange={(e) => setBranches(branches.map((x, j) => (j === i ? { ...x, phone: e.target.value } : x)))} />
                {isOwner && (
                  <button type="button" className="mt-1 text-xs text-rose-600" disabled={branches.length <= 1} onClick={() => setBranches(branches.filter((_, j) => j !== i))}>
                    Xoá
                  </button>
                )}
              </li>
            ))}
          </ul>
        </fieldset>
        {isOwner && (
          <div className="flex justify-end">
            <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
              {busy ? 'Đang lưu…' : 'Lưu'}
            </Button>
          </div>
        )}
      </form>

      <section className="mt-6 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <h2 className="font-semibold">Thành viên</h2>
        <ul className="mt-2 divide-y divide-zinc-100 dark:divide-zinc-800" data-testid="members">
          {members.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="mr-auto">{m.email}</span>
              {isOwner ? (
                <select aria-label={`Vai trò của ${m.email}`} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900" value={m.role} onChange={(e) => void setRole(m, e.target.value)}>
                  <option value="owner">Chủ cơ sở</option>
                  <option value="staff">Nhân viên</option>
                </select>
              ) : (
                <span className="text-xs text-zinc-500">{ROLE_LABEL[m.role] ?? m.role}</span>
              )}
              {isOwner && (
                <button type="button" className="text-xs text-rose-600" onClick={() => void removeMember(m)}>
                  Gỡ
                </button>
              )}
            </li>
          ))}
        </ul>
        {isOwner && (
          <form onSubmit={(e) => void addMember(e)} className="mt-3 flex flex-wrap items-end gap-2">
            <div className="min-w-48 flex-1">
              <label htmlFor="invite-email" className={label}>Email tài khoản (đã đăng ký)</label>
              <input id="invite-email" type="email" className={field} value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} required />
            </div>
            <div>
              <label htmlFor="invite-role" className={label}>Vai trò</label>
              <select id="invite-role" className={field} value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value })}>
                <option value="staff">Nhân viên</option>
                <option value="owner">Chủ cơ sở</option>
              </select>
            </div>
            <Button type="submit">Thêm</Button>
          </form>
        )}
      </section>
    </div>
  )
}
