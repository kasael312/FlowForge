/** Minimal unified-diff renderer with per-line coloring. */
export function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <p className="text-sm text-zinc-500">No changes.</p>
  const lines = diff.split('\n')
  return (
    <pre className="overflow-x-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs leading-5 dark:border-zinc-800 dark:bg-zinc-900" data-testid="diff">
      {lines.map((line, i) => (
        <div key={i} className={lineClass(line)}>
          {line || ' '}
        </div>
      ))}
    </pre>
  )
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'font-semibold text-zinc-700 dark:text-zinc-300'
  if (line.startsWith('diff --git')) return 'mt-2 font-semibold text-zinc-900 dark:text-zinc-100'
  if (line.startsWith('@@')) return 'text-sky-700 dark:text-sky-300'
  if (line.startsWith('+')) return 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
  if (line.startsWith('-')) return 'bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200'
  return 'text-zinc-600 dark:text-zinc-400'
}
