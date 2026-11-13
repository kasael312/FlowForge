import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'
import { useWorkspace } from '../lib/WorkspaceContext'

/** Route guard: renders children when logged in, else redirects to /login. */
export function RequireAuth() {
  const { user } = useAuth()
  const location = useLocation()
  if (user === undefined) return <p className="p-6 text-sm text-zinc-500">Đang tải…</p>
  if (user === null) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return <Outlet />
}

/** Inside the app shell: users without a workspace go through onboarding first (system admins may skip). */
export function RequireWorkspace() {
  const { user } = useAuth()
  const { current } = useWorkspace()
  const location = useLocation()
  if (current === undefined) return <p className="p-6 text-sm text-zinc-500">Đang tải…</p>
  if (current === null && !user?.is_admin && !location.pathname.startsWith('/onboarding')) return <Navigate to="/onboarding" replace />
  return <Outlet />
}

/** Owner-only pages (automations, connections, AI assistant, settings): staff are sent home. */
export function RequireOwner() {
  const { user } = useAuth()
  const { current } = useWorkspace()
  if (current === undefined) return <p className="p-6 text-sm text-zinc-500">Đang tải…</p>
  const allowed = Boolean(user?.is_admin) || current === null || current.role === 'owner'
  if (!allowed) return <Navigate to="/" replace />
  return <Outlet />
}

/** Technical FlowForge pages (canvas, runs, tools…) are for system admins only. */
export function RequireAdmin() {
  const { user } = useAuth()
  if (!user?.is_admin) return <Navigate to="/" replace />
  return <Outlet />
}

/** Inverse guard for /login and /register: send logged-in users home. */
export function RedirectIfAuthed() {
  const { user } = useAuth()
  if (user === undefined) return <p className="p-6 text-sm text-zinc-500">Đang tải…</p>
  if (user) return <Navigate to="/" replace />
  return <Outlet />
}
