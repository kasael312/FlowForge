import { useState } from 'react'
import { api } from '../api/client'
import type { FlowNode, StepTryResult } from '../api/types'
import { Button, ErrorBanner } from './ui'

interface Props {
  flowId: string
  node: FlowNode
  onClose: () => void
}

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

/** Dry-run one step with simulated inputs; shows output and the exact prompts sent. */
export function TryStepPanel({ flowId, node, onClose }: Props) {
  const [task, setTask] = useState('Example task: add input validation to the signup form')
  const [prevOutput, setPrevOutput] = useState('')
  const [prevSuccess, setPrevSuccess] = useState(true)
  const [result, setResult] = useState<StepTryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showPrompts, setShowPrompts] = useState(false)

  const run = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(
        await api.tryStep(flowId, node.id, {
          node,
          task,
          prev_output: prevOutput,
          prev_success: prevSuccess,
          prev_exit_code: prevSuccess ? 0 : 1,
        }),
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const isCondition = node.data.type === 'condition'
  return (
    <section className="space-y-3 rounded-lg border border-teal-300 bg-teal-50/40 p-3 dark:border-teal-900 dark:bg-teal-950/30" aria-label="Try this step">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Try step “{node.data.label || node.id}”</h3>
        <button type="button" className="text-xs underline" onClick={onClose}>
          close
        </button>
      </div>
      <p className="text-[11px] text-zinc-500">
        {isCondition
          ? 'Evaluates the expression against the simulated previous output.'
          : 'Runs one model turn with tools disabled, using the current (unsaved) prompt. No repository is involved.'}
      </p>
      {!isCondition && (
        <div>
          <label htmlFor="try-task" className={label}>Task</label>
          <textarea id="try-task" className={`${field} h-16`} value={task} onChange={(e) => setTask(e.target.value)} />
        </div>
      )}
      <div>
        <label htmlFor="try-prev" className={label}>Previous step output</label>
        <textarea id="try-prev" className={`${field} h-16 font-mono text-xs`} placeholder="e.g. 12 passed, RESULT: PASS" value={prevOutput} onChange={(e) => setPrevOutput(e.target.value)} />
        <label className="mt-1 flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={prevSuccess} onChange={(e) => setPrevSuccess(e.target.checked)} /> previous step succeeded
        </label>
      </div>
      <div className="flex justify-end">
        <Button variant="primary" disabled={busy} onClick={() => void run()}>
          {busy ? 'Running…' : 'Run step'}
        </Button>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {result && (
        <div className="space-y-2" role="status">
          <div className={`text-xs font-semibold ${result.success ? 'text-emerald-600' : 'text-rose-600'}`}>
            {result.success ? '✔ success' : '✖ failed'}
            {result.tokens > 0 && <span className="ml-2 font-normal text-zinc-500">{result.tokens} tokens</span>}
          </div>
          {result.error && <p className="text-xs text-rose-600">{result.error}</p>}
          {result.output && <pre className="max-h-60 overflow-auto whitespace-pre-wrap rounded bg-white p-2 text-xs dark:bg-zinc-900">{result.output}</pre>}
          {result.system_prompt && (
            <div>
              <button type="button" className="text-[11px] underline" onClick={() => setShowPrompts(!showPrompts)}>
                {showPrompts ? 'hide prompts' : 'show the prompts that were sent'}
              </button>
              {showPrompts && (
                <div className="mt-1 space-y-1">
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[10px] dark:bg-zinc-900">{result.system_prompt}</pre>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[10px] dark:bg-zinc-900">{result.user_message}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
