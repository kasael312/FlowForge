import type { ModelCost } from '../api/types'
import { formatUsd } from '../lib/format'

/**
 * Ranked horizontal bars of spend per model (one series, one hue, direct labels),
 * with the numbers in the same rows so the chart doubles as its table view.
 */
export function CostByModelChart({ rows }: { rows: ModelCost[] }) {
  const max = Math.max(0, ...rows.map((r) => r.cost_usd))
  if (rows.length === 0) {
    return <p className="rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">No LLM steps recorded yet.</p>
  }
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900" data-testid="cost-by-model">
      <ol className="space-y-2">
        {rows.map((r) => {
          const pct = max > 0 ? Math.max(1.5, (r.cost_usd / max) * 100) : 0
          return (
            <li key={r.model} className="grid grid-cols-[minmax(8rem,14rem)_1fr_auto] items-center gap-3 text-sm" title={`${r.model}: ${formatUsd(r.cost_usd)} · ${r.input_tokens.toLocaleString()} in / ${r.output_tokens.toLocaleString()} out · ${r.steps} steps`}>
              <span className="truncate font-mono text-xs text-zinc-700 dark:text-zinc-300">{r.model}</span>
              <span className="relative h-3 overflow-hidden rounded-[4px] bg-zinc-100 dark:bg-zinc-800">
                <span className="absolute inset-y-0 left-0 rounded-r-[4px] bg-[#2a78d6] dark:bg-[#3987e5]" style={{ width: `${pct}%` }} role="img" aria-label={`${r.model} ${formatUsd(r.cost_usd)}`} />
              </span>
              <span className="whitespace-nowrap text-right tabular-nums text-zinc-700 dark:text-zinc-300">
                {formatUsd(r.cost_usd)} <span className="text-xs text-zinc-500">· {r.steps} steps · {(r.input_tokens + r.output_tokens).toLocaleString()} tok</span>
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
