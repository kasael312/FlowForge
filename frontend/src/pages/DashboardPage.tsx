import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Stats } from '../api/types'
import { CostByModelChart } from '../components/CostByModelChart'
import { ErrorBanner, StatusBadge } from '../components/ui'
import { formatDate, formatUsd } from '../lib/format'

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 text-3xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-zinc-500">{hint}</div>}
    </div>
  )
}

/** Overview: counts, success rate and the latest runs. */
export function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .stats()
      .then((s) => !cancelled && setStats(s))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <Link to="/flows" className="text-sm underline">
          Go to flows
        </Link>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {stats === null && !error ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : stats ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Tile label="Flows" value={String(stats.flows)} />
            <Tile label="Runs today" value={String(stats.runs_today)} hint={`${stats.runs_total} total · ${stats.runs_active} active`} />
            <Tile label="Success rate" value={stats.success_rate === null ? '—' : `${Math.round(stats.success_rate * 100)}%`} hint="of finished runs" />
            <Tile label="Active runs" value={String(stats.runs_active)} />
            <Tile label="LLM spend" value={formatUsd(stats.total_cost_usd ?? 0)} hint="all runs, estimated" />
          </div>
          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold text-zinc-600 dark:text-zinc-400">Cost by model</h2>
            <CostByModelChart rows={stats.cost_by_model ?? []} />
          </section>
          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold text-zinc-600 dark:text-zinc-400">Recent runs</h2>
            {stats.recent_runs.length === 0 ? (
              <p className="rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">
                No runs yet. Open a flow and start one.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-zinc-500">
                  <tr>
                    <th className="py-2">Run</th>
                    <th>Status</th>
                    <th>Task</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recent_runs.map((r) => (
                    <tr key={r.id} className="border-t border-zinc-200 dark:border-zinc-800">
                      <td className="py-2 font-mono text-xs">
                        <Link to={`/runs/${r.id}`} className="hover:underline">
                          {r.id.slice(0, 8)}
                        </Link>
                      </td>
                      <td><StatusBadge status={r.status} /></td>
                      <td className="max-w-md truncate text-zinc-600 dark:text-zinc-400">{r.task_input || r.repo_url}</td>
                      <td className="text-zinc-500">{formatDate(r.started_at ?? r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      ) : null}
    </div>
  )
}
