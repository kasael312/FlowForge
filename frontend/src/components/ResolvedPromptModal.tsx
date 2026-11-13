import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ResolvedPrompt } from '../api/types'
import { Button, Modal } from './ui'

const KIND_COLOR: Record<ResolvedPrompt['sections'][number]['kind'], string> = {
  base: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200',
  rule: 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-100',
  persona: 'bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-100',
  skill: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100',
  step: 'bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-100',
}

/** Shows the server-composed system prompt of one step, section by section. */
export function ResolvedPromptModal({ flowId, stepId, onClose }: { flowId: string; stepId: string; onClose: () => void }) {
  const [data, setData] = useState<ResolvedPrompt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [raw, setRaw] = useState(false)
  useEffect(() => {
    let cancelled = false
    api
      .resolvedPrompt(flowId, stepId)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [flowId, stepId])
  return (
    <Modal title={`Composed prompt · ${stepId}`} onClose={onClose}>
      <p className="mb-2 text-xs text-zinc-500">Order: base → rules (global, flow, step) → persona → skills → step prompt. Variables are rendered at run time; the saved flow is used, so save first.</p>
      {error && <p className="text-sm text-rose-600">{error}</p>}
      {!data && !error && <p className="text-sm text-zinc-500">Loading…</p>}
      {data && (
        <div className="max-h-[65vh] overflow-y-auto" data-testid="resolved-prompt">
          <div className="mb-2 flex gap-1">
            <button type="button" className={`rounded px-2 py-0.5 text-[11px] ${!raw ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900' : 'text-zinc-500'}`} onClick={() => setRaw(false)}>
              Sections
            </button>
            <button type="button" className={`rounded px-2 py-0.5 text-[11px] ${raw ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900' : 'text-zinc-500'}`} onClick={() => setRaw(true)}>
              Raw
            </button>
          </div>
          {raw ? (
            <pre className="whitespace-pre-wrap rounded bg-zinc-100 p-3 text-xs dark:bg-zinc-800">{data.system_prompt}</pre>
          ) : (
            <ol className="space-y-2">
              {data.sections.map((s, i) => (
                <li key={i} className="rounded border border-zinc-200 p-2 dark:border-zinc-800">
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${KIND_COLOR[s.kind]}`}>{s.kind}</span>
                    <span className="font-medium">{s.name}</span>
                  </div>
                  <pre className="whitespace-pre-wrap text-xs text-zinc-700 dark:text-zinc-300">{s.text}</pre>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
      <div className="mt-3 flex justify-end">
        <Button onClick={onClose}>Close</Button>
      </div>
    </Modal>
  )
}
