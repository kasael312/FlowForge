import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Sidebar } from './components/layout/Sidebar'
import { IconMenu } from './components/layout/Icons'

/** Application shell: fixed sidebar (drawer on mobile) + routed content. */
export default function App() {
  const [mobileOpen, setMobileOpen] = useState(false)
  return (
    <div className="flex min-h-screen">
      <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center gap-2 border-b border-zinc-200 bg-white px-3 md:hidden dark:border-zinc-800 dark:bg-zinc-950">
          <button type="button" onClick={() => setMobileOpen(true)} aria-label="Open navigation" className="rounded p-1.5 hover:bg-zinc-200 dark:hover:bg-zinc-800">
            <IconMenu />
          </button>
          <span className="text-base font-bold tracking-tight">FlowForge</span>
        </header>
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
