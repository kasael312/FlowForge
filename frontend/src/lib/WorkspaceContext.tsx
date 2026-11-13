import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { Workspace } from '../api/types'
import { useAuth } from '../auth/AuthContext'

export interface WorkspaceState {
  /** `undefined` while loading; `null` when the user has no workspace yet. */
  current: Workspace | null | undefined
  all: Workspace[]
  reload: () => Promise<void>
  switchTo: (id: string) => Promise<void>
}

export const WorkspaceContext = createContext<WorkspaceState>({ current: undefined, all: [], reload: async () => {}, switchTo: async () => {} })

/** Loads the user's workspaces once logged in and exposes the active one. */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [all, setAll] = useState<Workspace[]>([])
  const [current, setCurrent] = useState<Workspace | null | undefined>(undefined)
  // Which user the current value was loaded for: until it matches, consumers see `undefined` (loading).
  const [loadedFor, setLoadedFor] = useState<string | null>(null)

  const reload = useCallback(async () => {
    if (!user) {
      setLoadedFor(null)
      setAll([])
      setCurrent(null)
      return
    }
    try {
      const list = await api.listWorkspaces()
      setAll(list)
      const active = list.find((w) => w.id === user.current_workspace_id) ?? list[0] ?? null
      setCurrent(active)
    } catch {
      setAll([])
      setCurrent(null)
    } finally {
      setLoadedFor(user.id)
    }
  }, [user])

  useEffect(() => {
    void reload()
  }, [reload])

  const switchTo = useCallback(
    async (id: string) => {
      const ws = await api.switchWorkspace(id)
      setCurrent(ws)
      setAll((prev) => prev.map((w) => (w.id === ws.id ? ws : w)))
    },
    [],
  )

  // A stale value from a previous user (or none yet) must read as "loading", otherwise the
  // workspace guard bounces a fresh login or a full page reload to onboarding.
  const exposed = user && loadedFor !== user.id ? undefined : current
  const value = useMemo(() => ({ current: exposed, all, reload, switchTo }), [exposed, all, reload, switchTo])
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace(): WorkspaceState {
  return useContext(WorkspaceContext)
}
