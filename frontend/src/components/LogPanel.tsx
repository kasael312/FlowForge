import { useState } from 'react'
import type { LogEntry, StepLog } from '../lib/runLog'
import { STEP_META } from '../lib/steps'
import type { StepType } from '../api/types'
import { formatDuration } from '../lib/format'
import { StatusBadge } from './ui'

function Entry({ entry }: { entry: LogEntry }) {
  switch (entry.kind) {
    case 'text':
      return <div className="whitespace-pre-wrap rounded bg-white px-3 py-2 text-sm dark:bg-zinc-900">{entry.text}</div>
    case 'tool_call':
      return (
        <div className="rounded border border-sky-200 bg-sky-50 px-3 py-2 font-mono text-xs dark:border-sky-900 dark:bg-sky-950">
          <span className="font-semibold text-sky-700 dark:text-sky-300">▶ {entry.tool}</span>{' '}
          <span className="text-zinc-600 dark:text-zinc-400">{summarizeInput(entry.input)}</span>
        </div>
      )
    case 'tool_result':
      return <ToolResult entry={entry} />
    case 'log':
      return (
        <div className={`px-3 py-1 text-xs ${entry.level === 'error' ? 'text-rose-600' : 'text-zinc-500'}`}>
          {entry.message}
        </div>
      )
  }
}

function summarizeInput(input: Record<string, unknown>): string {
  return Object.entries(input)
    .map(([k, v]) => {
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      return `${k}=${s.length > 80 ? `${s.slice(0, 80)}…` : s}`
    })
    .join(' ')
}

function ToolResult({ entry }: { entry: Extract<LogEntry, { kind: 'tool_result' }> }) {
  const [open, setOpen] = useState(false)
  const lines = entry.output.split('\n')
  const long = lines.length > 12 || entry.output.length > 1200
  const shown = open || !long ? entry.output : `${lines.slice(0, 12).join('\n')}\n…`
  return (
    <div className={`rounded border px-3 py-2 font-mono text-xs ${entry.isError ? 'border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950' : 'border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900'}`}>
      <div className="mb-1 flex items-center gap-2 text-[11px] text-zinc-500">
        <span>{entry.isError ? '✖' : '✔'} {entry.tool}</span>
        {entry.duration !== undefined && <span>{formatDuration(entry.duration)}</span>}
        {long && (
          <button type="button" className="ml-auto underline" onClick={() => setOpen(!open)}>
            {open ? 'collapse' : `expand (${lines.length} lines)`}
          </button>
        )}
      </div>
      <pre className="whitespace-pre-wrap break-words">{shown}</pre>
    </div>
  )
}

export function StepLogCard({ step, defaultOpen }: { step: StepLog; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const meta = STEP_META[step.type as StepType]
  return (
    <section className="rounded-lg border border-zinc-200 dark:border-zinc-800" data-testid={`step-${step.key}`}>
      <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left" onClick={() => setOpen(!open)}>
        <span className={`h-2.5 w-2.5 rounded-full ${meta?.color ?? 'bg-zinc-400'}`} />
        <span className="font-medium">{step.label}</span>
        <span className="font-mono text-xs text-zinc-500">{step.stepId}{step.attempt > 1 ? ` · attempt ${step.attempt}` : ''}</span>
        <span className="ml-auto flex items-center gap-2 text-xs text-zinc-500">
          {step.duration !== undefined && formatDuration(step.duration)}
          <StatusBadge status={step.status} />
          <span>{open ? '▾' : '▸'}</span>
        </span>
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-zinc-200 bg-zinc-100/60 p-2 dark:border-zinc-800 dark:bg-zinc-950/40">
          {step.entries.length === 0 && step.status === 'running' && <div className="px-3 py-1 text-xs text-zinc-500">Waiting for the model…</div>}
          {step.entries.map((e, i) => (
            <Entry key={i} entry={e} />
          ))}
          {step.error && <div className="px-3 py-1 text-xs text-rose-600">{step.error}</div>}
        </div>
      )}
    </section>
  )
}
