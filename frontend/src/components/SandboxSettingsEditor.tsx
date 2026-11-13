import type { SandboxNetwork, SandboxSettings } from '../api/types'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-[11px] font-medium text-zinc-600 dark:text-zinc-400'

const NETWORK_HINT: Record<SandboxNetwork, string> = {
  none: 'No network at all. Safest; package installs will fail.',
  allowlist: 'Only the listed hosts are reachable, through an egress proxy on a private network (HTTP_PROXY/HTTPS_PROXY is set for pip, npm, curl, Python requests…).',
  bridge: 'Open internet access. Use only for trusted flows.',
}

/** Per-flow sandbox limits. Empty fields mean "server default"; the server also caps values. */
export function SandboxSettingsEditor({ value, onChange }: { value: SandboxSettings; onChange: (v: SandboxSettings) => void }) {
  const set = <K extends keyof SandboxSettings>(k: K, v: SandboxSettings[K]) => onChange({ ...value, [k]: v })
  const num = (s: string) => (s.trim() === '' ? null : Number(s))
  const network = value.network ?? 'none'
  return (
    <div className="space-y-3" data-testid="sandbox-editor">
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label htmlFor="sb-cpus" className={label}>CPUs</label>
          <input id="sb-cpus" type="number" step="0.25" min={0.25} className={field} placeholder="default" value={value.cpus ?? ''} onChange={(e) => set('cpus', num(e.target.value))} />
        </div>
        <div>
          <label htmlFor="sb-mem" className={label}>Memory (MB)</label>
          <input id="sb-mem" type="number" min={128} className={field} placeholder="default" value={value.memory_mb ?? ''} onChange={(e) => set('memory_mb', num(e.target.value))} />
        </div>
        <div>
          <label htmlFor="sb-timeout" className={label}>Time limit (seconds)</label>
          <input id="sb-timeout" type="number" min={30} className={field} placeholder="default" value={value.timeout_seconds ?? ''} onChange={(e) => set('timeout_seconds', num(e.target.value))} />
        </div>
      </div>
      <div>
        <label htmlFor="sb-network" className={label}>Network</label>
        <select id="sb-network" className={field} value={network} onChange={(e) => set('network', e.target.value as SandboxNetwork)}>
          <option value="none">none — no egress</option>
          <option value="allowlist">allowlist — only listed hosts</option>
          <option value="bridge">bridge — open</option>
        </select>
        <p className="mt-1 text-[11px] text-zinc-500">{NETWORK_HINT[network]}</p>
      </div>
      {network === 'allowlist' && (
        <div>
          <label htmlFor="sb-hosts" className={label}>Allowed hosts (one per line; *.example.com for subdomains)</label>
          <textarea
            id="sb-hosts"
            className={`${field} h-24 font-mono text-xs`}
            placeholder={'pypi.org\nfiles.pythonhosted.org\n*.github.com\nregistry.npmjs.org'}
            value={(value.allowed_hosts ?? []).join('\n')}
            onChange={(e) => set('allowed_hosts', e.target.value.split('\n').map((h) => h.trim()).filter(Boolean))}
          />
        </div>
      )}
      <p className="text-[11px] text-zinc-500">Files written under <code>output/</code> in the sandbox are kept as downloadable artifacts after the run.</p>
    </div>
  )
}
