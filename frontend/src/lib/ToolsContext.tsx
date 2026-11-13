import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { ToolInfo } from '../api/types'

/** Exported for tests; use ToolsProvider in the app. */
export const ToolsContext = createContext<ToolInfo[] | null>(null)

/** Loads the tool registry once for the editor (sidebar tool picker). */
export function ToolsProvider({ children }: { children: ReactNode }) {
  const [tools, setTools] = useState<ToolInfo[] | null>(null)
  useEffect(() => {
    let cancelled = false
    api
      .listTools()
      .then((t) => !cancelled && setTools(t))
      .catch(() => !cancelled && setTools([]))
    return () => {
      cancelled = true
    }
  }, [])
  return <ToolsContext.Provider value={tools}>{children}</ToolsContext.Provider>
}

/** `null` while loading. */
export function useTools(): ToolInfo[] | null {
  return useContext(ToolsContext)
}
