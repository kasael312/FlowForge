import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Artifact, RunDetail, StepRun } from '../api/types'
import { DiffView } from '../components/DiffView'
import { StepLogCard } from '../components/LogPanel'
import { Button, ErrorBanner, StatusBadge } from '../components/ui'
import { useRunLogs } from '../hooks/useRunLogs'
import { formatDate, formatDuration, formatUsd } from '../lib/format'
import { isTerminal } from '../lib/runLog'

type Tab = 'logs' | 'variables' | 'outputs' | 'artifacts' | 'diff' | 'steps'

/** Live view of one run: streamed logs per step, final diff and step table. */
export function RunDetailPage() {
  const { id = '' } = useParams()
  const [run, setRun] = useState<RunDetail | null>(null)
  const [steps, setSteps] = useState<StepRun[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('logs')
  const { state, connection } = useRunLogs(id)

  const refresh = useCallback(async () => {
    try {
      const [r, s, a] = await Promise.all([api.getRun(id), api.getRunSteps(id), api.listArtifacts(id).catch(() => [] as Artifact[])])
      setRun(r)
      setSteps(s)
      setArtifacts(a)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Once the stream reports a terminal status, reload the persisted run (diff, tokens).
  const terminal = isTerminal(state.status)
  useEffect(() => {
    if (terminal) void refresh()
  }, [terminal, refresh])

  const cancel = async () => {
    try {
      const cancelled = await api.cancelRun(id)
      setRun((prev) => ({ ...cancelled, final_diff: prev?.final_diff ?? null }))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const status = terminal ? state.status : (run?.status ?? state.status)
  const active = status === 'queued' || status === 'running' || status === 'waiting'
  const [comment, setComment] = useState('')
  const [deciding, setDeciding] = useState(false)
  const decide = async (decision: 'approve' | 'reject') => {
    setDeciding(true)
    try {
      const updated = await api.approveRun(id, { decision, comment })
      setRun((prev) => ({ ...updated, final_diff: prev?.final_diff ?? null }))
      setComment('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setDeciding(false)
    }
  }
  const approval = status === 'waiting' ? state.approval : null

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">
          Run <span className="font-mono text-lg text-zinc-500">{id.slice(0, 8)}</span>
        </h1>
        <StatusBadge status={status} />
        {connection === 'error' && !terminal && <span className="text-xs text-amber-600">log stream disconnected</span>}
        <div className="ml-auto flex gap-2">
          {run && (
            <Link to={`/flows/${run.flow_id}/run`}>
              <Button>Run again</Button>
            </Link>
          )}
          {active && (
            <Button variant="danger" onClick={() => void cancel()}>
              Cancel
            </Button>
          )}
          <Button disabled title="Available in the next step">
            Create PR
          </Button>
        </div>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {status === 'waiting' && (
        <section className="mb-4 rounded-lg border border-yellow-300 bg-yellow-50 p-4 dark:border-yellow-700 dark:bg-yellow-950" data-testid="approval-card">
          <h2 className="font-semibold text-yellow-900 dark:text-yellow-100">Waiting for your approval{approval ? ` — step ${approval.stepId}` : ''}</h2>
          {approval ? (
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-white/70 p-3 text-sm dark:bg-zinc-900/60">{approval.question}</pre>
          ) : (
            <p className="mt-1 text-sm text-yellow-800 dark:text-yellow-200">The run is paused at an approval step (question not available on this connection).</p>
          )}
          <label htmlFor="approval-comment" className="mt-3 block text-xs font-medium text-zinc-600 dark:text-zinc-300">Comment (optional, becomes the step output)</label>
          <textarea id="approval-comment" className="mt-1 w-full rounded border border-zinc-300 bg-white p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="mt-2 flex gap-2">
            <Button variant="primary" disabled={deciding} onClick={() => void decide('approve')}>Approve</Button>
            <Button variant="danger" disabled={deciding} onClick={() => void decide('reject')}>Reject</Button>
            {approval && approval.timeoutSeconds > 0 && <span className="self-center text-xs text-zinc-500">times out after {formatDuration(approval.timeoutSeconds)}</span>}
          </div>
        </section>
      )}
      {run && (
        <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
          <Meta k="Repository" v={<span className="font-mono text-xs">{run.repo_url || '— (empty workspace)'}</span>} />
          <Meta k="Branch" v={<span className="font-mono text-xs">{run.branch}{run.work_branch ? ` → ${run.work_branch}` : ''}</span>} />
          <Meta k="Started" v={formatDate(run.started_at ?? run.created_at)} />
          <Meta k="Tokens / cost" v={`${run.cost_tokens.toLocaleString()} · ${formatUsd(run.cost_usd)}`} />
          {run.sandbox && Object.keys(run.sandbox).length > 0 && (
            <Meta
              k="Sandbox"
              v={
                <span className="font-mono text-xs" data-testid="run-sandbox">
                  {[run.sandbox.cpus != null && `${run.sandbox.cpus} cpu`, run.sandbox.memory_mb != null && `${run.sandbox.memory_mb} MB`, run.sandbox.timeout_seconds != null && `${run.sandbox.timeout_seconds}s`, run.sandbox.network && `net: ${run.sandbox.network}${run.sandbox.network === 'allowlist' ? ` (${(run.sandbox.allowed_hosts ?? []).join(', ')})` : ''}`].filter(Boolean).join(' · ')}
                </span>
              }
            />
          )}
          {run.task_input && <Meta k="Task" v={<span className="whitespace-pre-wrap">{run.task_input}</span>} wide />}
          {run.inputs && Object.keys(run.inputs).length > 0 && (
            <Meta
              k={run.trigger_id ? 'Inputs (from trigger)' : 'Inputs'}
              wide
              v={
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-xs" data-testid="run-inputs">
                  {Object.entries(run.inputs).map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="font-mono text-zinc-500">{k}</dt>
                      <dd className="truncate">{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
                    </div>
                  ))}
                </dl>
              }
            />
          )}
          {(run.error || state.error) && <Meta k="Error" v={<span className="text-rose-600">{state.error ?? run.error}</span>} wide />}
        </dl>
      )}

      <div className="mb-3 flex gap-1 border-b border-zinc-200 dark:border-zinc-800">
        {(['logs', 'variables', 'outputs', 'artifacts', 'diff', 'steps'] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 text-sm capitalize ${tab === t ? 'border-b-2 border-zinc-900 font-medium dark:border-zinc-100' : 'text-zinc-500'}`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'logs' && (
        <div className="space-y-2" data-testid="log-panel">
          {state.runLogs.map((e, i) => (
            <div key={i} className={`text-xs ${e.kind === 'log' && e.level === 'error' ? 'text-rose-600' : 'text-zinc-500'}`}>
              {e.kind === 'log' ? e.message : ''}
            </div>
          ))}
          {state.steps.length === 0 && !terminal && <p className="text-sm text-zinc-500">Waiting for the first step…</p>}
          {state.steps.map((s, i) => (
            <StepLogCard key={s.key} step={s} defaultOpen={s.status === 'running' || i === state.steps.length - 1} />
          ))}
        </div>
      )}

      {tab === 'variables' && (
        <div data-testid="variables-panel">
          <p className="mb-2 text-xs text-zinc-500">Live variable context: <code>input</code>, <code>steps.&lt;id&gt;</code>, <code>items</code>, <code>prev</code>. Updates after every step.</p>
          {state.variables ? <VariableTree value={state.variables} /> : <p className="text-sm text-zinc-500">No variables yet{terminal ? ' (variables are only streamed while a run is live)' : ''}.</p>}
        </div>
      )}
      {tab === 'outputs' && (
        <div className="space-y-3" data-testid="outputs-panel">
          {(state.outputs.length > 0 ? state.outputs : (run?.outputs ?? [])).length === 0 ? (
            <p className="text-sm text-zinc-500">No outputs declared. Add an Output step to the flow.</p>
          ) : (
            (state.outputs.length > 0 ? state.outputs : (run?.outputs ?? [])).map((o, i) => {
              const out = o as { name?: string; kind?: string; value?: unknown; path?: string; delivered?: { tool: string; result: string } }
              return (
                <section key={i} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-medium">{out.name}</span>
                    <span className="rounded bg-zinc-200 px-1.5 text-[10px] uppercase dark:bg-zinc-700">{out.kind}</span>
                    {out.delivered && <span className="text-xs text-emerald-600">delivered via {out.delivered.tool}</span>}
                  </div>
                  {out.path && <p className="mt-1 font-mono text-xs">{out.path}</p>}
                  {out.value !== undefined && <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-zinc-50 p-2 text-xs dark:bg-zinc-900">{typeof out.value === 'string' ? out.value : JSON.stringify(out.value, null, 2)}</pre>}
                </section>
              )
            })
          )}
        </div>
      )}
      {tab === 'artifacts' && (
        <div data-testid="artifacts-panel">
          {(() => {
            const list = artifacts.length > 0 ? artifacts : state.artifacts.map((a) => ({ ...a, run_id: id, created_at: '' }))
            if (list.length === 0) return <p className="text-sm text-zinc-500">{active ? 'Files written to output/ appear here when the run finishes.' : 'No artifacts. Steps can write files under output/ to keep them.'}</p>
            return (
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-zinc-500">
                  <tr>
                    <th className="py-1">File</th>
                    <th>Type</th>
                    <th>Size</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((a) => (
                    <tr key={a.id} className="border-t border-zinc-200 dark:border-zinc-800">
                      <td className="py-1 font-mono text-xs">{a.name}</td>
                      <td className="text-xs text-zinc-500">{a.content_type}</td>
                      <td className="text-xs text-zinc-500">{a.size < 1024 ? `${a.size} B` : a.size < 1048576 ? `${(a.size / 1024).toFixed(1)} KB` : `${(a.size / 1048576).toFixed(1)} MB`}</td>
                      <td className="text-right">
                        <a className="text-xs underline" href={api.artifactDownloadUrl(id, a.id)} download>
                          Download
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          })()}
        </div>
      )}
      {tab === 'diff' && (active ? <p className="text-sm text-zinc-500">The diff is captured when the run finishes.</p> : <DiffView diff={run?.final_diff ?? ''} />)}

      {tab === 'steps' && (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-1">Step</th>
              <th>Type</th>
              <th>Attempt</th>
              <th>Status</th>
              <th>Model</th>
              <th>Persona / skills</th>
              <th>Duration</th>
              <th>Tokens in / out</th>
              <th>Cost</th>
              <th>Tool calls</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((s) => (
              <tr key={s.id} className="border-t border-zinc-200 dark:border-zinc-800">
                <td className="py-1 font-mono text-xs">{s.step_id}</td>
                <td>{s.step_type}</td>
                <td>{s.attempt}</td>
                <td><StatusBadge status={s.status} /></td>
                <td className="font-mono text-xs">{s.model ?? '—'}</td>
                <td className="text-xs" data-testid="step-skills">
                  {(s.skills ?? []).length === 0
                    ? '—'
                    : (s.skills ?? []).map((k) => (
                        <span key={k.name} className={`mr-1 inline-block rounded px-1.5 py-0.5 font-mono text-[10px] ${k.kind === 'persona' ? 'bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-100' : 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100'}`} title={k.kind}>
                          {k.name}
                        </span>
                      ))}
                </td>
                <td>{formatDuration(s.duration)}</td>
                <td>{s.model ? `${(s.input_tokens ?? 0).toLocaleString()} / ${(s.output_tokens ?? 0).toLocaleString()}` : '—'}</td>
                <td>{s.model ? formatUsd(s.cost_usd) : '—'}</td>
                <td>{s.tool_calls.length}</td>
              </tr>
            ))}
            {steps.length === 0 && (
              <tr>
                <td colSpan={10} className="py-2 text-zinc-500">No steps recorded yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}

function VariableTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || typeof value !== 'object') {
    return <span className={typeof value === 'string' ? 'text-emerald-700 dark:text-emerald-300' : 'text-sky-700 dark:text-sky-300'}>{typeof value === 'string' ? JSON.stringify(value) : String(value)}</span>
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value as Record<string, unknown>)
  if (entries.length === 0) return <span className="text-zinc-500">{Array.isArray(value) ? '[]' : '{}'}</span>
  return (
    <ul className={depth === 0 ? 'space-y-1 font-mono text-xs' : 'ml-4 border-l border-zinc-200 pl-2 dark:border-zinc-800'}>
      {entries.map(([k, v]) => (
        <li key={k}>
          <details open={depth < 1}>
            <summary className="cursor-pointer text-zinc-700 dark:text-zinc-300">
              {k}
              {typeof v !== 'object' || v === null ? <>: <VariableTree value={v} depth={depth + 1} /></> : <span className="text-zinc-400"> {Array.isArray(v) ? `[${v.length}]` : `{${Object.keys(v as object).length}}`}</span>}
            </summary>
            {typeof v === 'object' && v !== null && <VariableTree value={v} depth={depth + 1} />}
          </details>
        </li>
      ))}
    </ul>
  )
}

function Meta({ k, v, wide }: { k: string; v: React.ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? 'col-span-2 sm:col-span-4' : ''}>
      <dt className="text-xs text-zinc-500">{k}</dt>
      <dd>{v}</dd>
    </div>
  )
}
