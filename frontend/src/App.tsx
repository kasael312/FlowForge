import { useEffect, useState } from 'react'

const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

type Health = 'checking' | 'ok' | 'down'

export default function App() {
  const [health, setHealth] = useState<Health>('checking')

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => (r.ok ? 'ok' : 'down') as Health)
      .catch(() => 'down' as Health)
      .then(setHealth)
  }, [])

  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-3xl font-semibold tracking-tight">FlowForge</h1>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">
        AI coding pipelines: design flows, run them on a repo, watch the logs.
      </p>
      <p className="mt-6 text-sm" data-testid="health">
        Backend: <span className="font-mono">{health}</span>
      </p>
    </main>
  )
}
