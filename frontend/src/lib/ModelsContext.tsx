import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { LlmModel } from '../api/types'

/** Exported for tests; use ModelsProvider in the app. */
export const ModelsContext = createContext<LlmModel[] | null>(null)
const Ctx = ModelsContext

/** Loads the user's models once for the editor (sidebar dropdowns + node tags). */
export function ModelsProvider({ children }: { children: ReactNode }) {
  const [models, setModels] = useState<LlmModel[] | null>(null)
  useEffect(() => {
    let cancelled = false
    api
      .listModels()
      .then((m) => !cancelled && setModels(m))
      .catch(() => !cancelled && setModels([]))
    return () => {
      cancelled = true
    }
  }, [])
  return <Ctx.Provider value={models}>{children}</Ctx.Provider>
}

/** `null` while loading. */
export function useModels(): LlmModel[] | null {
  return useContext(Ctx)
}

export function modelLabel(models: LlmModel[] | null, id: string | null | undefined): string | null {
  if (!id) return null
  const m = models?.find((x) => x.id === id)
  return m ? m.label : `(deleted model)`
}
