import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { SharedFlow } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { Button, ErrorBanner } from '../components/ui'
import { formatDate } from '../lib/format'
import { STEP_META } from '../lib/steps'

/** Public, read-only view of a flow shared by link, with a "clone" action. */
export function SharedFlowPage() {
  const { token = '' } = useParams()
  const { user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [flow, setFlow] = useState<SharedFlow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .getShared(token)
      .then(setFlow)
      .catch((e: Error) => setError(e instanceof ApiError && e.status === 404 ? 'This link is invalid or the flow is no longer shared.' : e.message))
  }, [token])

  const clone = async () => {
    if (!user) {
      navigate('/login', { state: { from: location.pathname } })
      return
    }
    setBusy(true)
    try {
      const created = await api.cloneShared(token)
      navigate(`/flows/${created.id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <p className="mb-2 text-xs uppercase tracking-wide text-zinc-500">Shared flow · read only</p>
      <ErrorBanner message={error} />
      {flow && (
        <>
          <h1 className="text-2xl font-semibold">{flow.name}</h1>
          {flow.description && <p className="mt-1 text-sm text-zinc-500">{flow.description}</p>}
          <p className="mt-1 text-xs text-zinc-500">Updated {formatDate(flow.updated_at)}</p>
          <ol className="mt-4 space-y-2">
            {flow.nodes.map((n) => (
              <li key={n.id} className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
                <div className="flex items-center gap-2">
                  <span className={`h-2.5 w-2.5 rounded-full ${STEP_META[n.data.type]?.color ?? 'bg-zinc-400'}`} />
                  <span className="font-medium">{n.data.label || n.id}</span>
                  <span className="text-xs uppercase text-zinc-500">{n.data.type}</span>
                  {n.data.on_fail !== 'stop' && <span className="ml-auto text-[11px] text-zinc-500">on fail: {n.data.on_fail}</span>}
                </div>
                {(n.data.system_prompt || n.data.command || n.data.expression) && (
                  <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-zinc-600 dark:text-zinc-400">{n.data.system_prompt || n.data.command || n.data.expression}</pre>
                )}
              </li>
            ))}
          </ol>
          <div className="mt-5 flex items-center gap-3">
            <Button variant="primary" disabled={busy} onClick={() => void clone()}>
              {busy ? 'Cloning…' : user ? 'Clone to my flows' : 'Sign in to clone'}
            </Button>
            {user && (
              <Link to="/flows" className="text-sm underline">
                My flows
              </Link>
            )}
          </div>
        </>
      )}
    </div>
  )
}
