import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Flow, Run } from '../api/types'
import { InputForm, defaultValues, validateValues, type InputValues } from '../components/InputForm'
import { Button, ErrorBanner, StatusBadge } from '../components/ui'
import { formatDate } from '../lib/format'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

/** Start a run: inputs generated from the flow's schema, optional repository, task text. */
export function RunPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [flow, setFlow] = useState<Flow | null>(null)
  const [recent, setRecent] = useState<Run[]>([])
  const [useRepo, setUseRepo] = useState(false)
  const [repoUrl, setRepoUrl] = useState('')
  const [branch, setBranch] = useState('main')
  const [task, setTask] = useState('')
  const [values, setValues] = useState<InputValues>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .getFlow(id)
      .then((f) => {
        setFlow(f)
        setValues(defaultValues(f.input_schema ?? []))
      })
      .catch((e: Error) => setError(e.message))
    api
      .listRuns({ flow_id: id, limit: 10 })
      .then((runs) => {
        setRecent(runs)
        const last = runs[0]
        if (last?.repo_url) {
          setUseRepo(true)
          setRepoUrl((v) => v || last.repo_url)
          setBranch((v) => (v === 'main' ? last.branch : v))
        }
      })
      .catch(() => undefined)
  }, [id])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!flow) return
    const problem = validateValues(flow.input_schema ?? [], values)
    if (problem) {
      setError(problem)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const run = await api.startRun(id, {
        repo_url: useRepo ? repoUrl.trim() : '',
        branch: branch.trim() || 'main',
        task,
        inputs: values,
      })
      navigate(`/runs/${run.id}`)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const schema = flow?.input_schema ?? []

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-4 flex items-baseline gap-3">
        <h1 className="text-2xl font-semibold">Run {flow ? `“${flow.name}”` : ''}</h1>
        {flow && (
          <Link to={`/flows/${flow.id}`} className="text-sm text-zinc-500 underline">
            edit flow
          </Link>
        )}
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {flow && flow.nodes.length === 0 && (
        <p className="mb-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">This flow has no steps yet. Add some in the editor first.</p>
      )}
      <form onSubmit={(e) => void submit(e)} className="space-y-4 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        {schema.length > 0 && (
          <section>
            <h2 className="mb-2 text-sm font-semibold">Inputs</h2>
            <InputForm schema={schema} values={values} onChange={setValues} />
          </section>
        )}
        <div>
          <label htmlFor="task" className={label}>Task</label>
          <textarea id="task" className={`${field} h-24`} placeholder="Describe what the workflow should do (available to prompts as {{task}})." value={task} onChange={(e) => setTask(e.target.value)} />
        </div>
        <section>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={useRepo} onChange={(e) => setUseRepo(e.target.checked)} /> Work on a Git repository
          </label>
          {useRepo ? (
            <div className="mt-2 grid gap-3 sm:grid-cols-[2fr_1fr]">
              <div>
                <label htmlFor="repo-url" className={label}>Repository URL</label>
                <input id="repo-url" required className={`${field} font-mono`} placeholder="https://github.com/owner/repo" value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} />
              </div>
              <div>
                <label htmlFor="branch" className={label}>Branch</label>
                <input id="branch" required className={`${field} font-mono`} value={branch} onChange={(e) => setBranch(e.target.value)} />
              </div>
            </div>
          ) : (
            <p className="mt-1 text-xs text-zinc-500">Without a repository the run gets an empty workspace with input/ and output/ folders.</p>
          )}
        </section>
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={busy || !flow || flow.nodes.length === 0}>
            {busy ? 'Starting…' : 'Start run'}
          </Button>
        </div>
      </form>

      {recent.length > 0 && (
        <section className="mt-6">
          <h2 className="mb-2 text-sm font-semibold text-zinc-600 dark:text-zinc-400">Recent runs</h2>
          <ul className="divide-y divide-zinc-200 text-sm dark:divide-zinc-800">
            {recent.map((r) => (
              <li key={r.id} className="flex items-center gap-3 py-1.5">
                <StatusBadge status={r.status} />
                <Link to={`/runs/${r.id}`} className="font-mono text-xs hover:underline">
                  {r.id.slice(0, 8)}
                </Link>
                <span className="truncate text-zinc-500">{r.task_input || r.repo_url || (r.trigger_id ? 'triggered run' : '')}</span>
                <span className="ml-auto text-xs text-zinc-500">{formatDate(r.created_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
