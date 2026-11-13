import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { FlowSummary, Run, RunStatus } from '../api/types'
import { ErrorBanner, StatusBadge } from '../components/ui'
import { formatDate } from '../lib/format'

const STATUSES: RunStatus[] = ['queued', 'running', 'success', 'failed', 'cancelled']
const select = 'rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'

/** Table of all runs with flow/status filters (kept in the URL). */
export function HistoryPage() {
  const [params, setParams] = useSearchParams()
  const flowId = params.get('flow') ?? ''
  const status = params.get('status') ?? ''
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listFlows().then(setFlows).catch(() => undefined)
  }, [])

  const load = useCallback(async () => {
    try {
      setRuns(await api.listRuns({ flow_id: flowId || undefined, status: status || undefined, limit: 200 }))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [flowId, status])

  useEffect(() => {
    void load()
  }, [load])

  // Poll while something is in flight.
  const anyActive = runs?.some((r) => r.status === 'queued' || r.status === 'running') ?? false
  useEffect(() => {
    if (!anyActive) return
    const t = setInterval(() => void load(), 3000)
    return () => clearInterval(t)
  }, [anyActive, load])

  const setFilter = (key: 'flow' | 'status', value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const flowName = (id: string) => flows.find((f) => f.id === id)?.name ?? id.slice(0, 8)

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">History</h1>
        <label className="ml-auto flex items-center gap-1 text-sm">
          Flow
          <select aria-label="Filter by flow" className={select} value={flowId} onChange={(e) => setFilter('flow', e.target.value)}>
            <option value="">All</option>
            {flows.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-sm">
          Status
          <select aria-label="Filter by status" className={select} value={status} onChange={(e) => setFilter('status', e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {runs === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : runs.length === 0 ? (
        <p className="rounded-md border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700">No runs match.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-2">Run</th>
              <th>Flow</th>
              <th>Status</th>
              <th>Task</th>
              <th>Repository</th>
              <th>Started</th>
              <th className="text-right">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-zinc-200 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900">
                <td className="py-2 font-mono text-xs">
                  <Link to={`/runs/${r.id}`} className="hover:underline">
                    {r.id.slice(0, 8)}
                  </Link>
                </td>
                <td>
                  <Link to={`/flows/${r.flow_id}`} className="hover:underline">
                    {flowName(r.flow_id)}
                  </Link>
                </td>
                <td><StatusBadge status={r.status} /></td>
                <td className="max-w-64 truncate text-zinc-600 dark:text-zinc-400" title={r.task_input}>{r.task_input || '—'}</td>
                <td className="max-w-56 truncate font-mono text-xs text-zinc-500" title={r.repo_url}>{r.repo_url.replace(/^https?:\/\//, '')}@{r.branch}</td>
                <td className="text-zinc-500">{formatDate(r.started_at ?? r.created_at)}</td>
                <td className="text-right">{r.cost_tokens.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
