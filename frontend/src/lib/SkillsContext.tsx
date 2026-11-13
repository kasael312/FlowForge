import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { Skill } from '../api/types'

export interface SkillsState {
  /** `null` while loading. */
  skills: Skill[] | null
  reload: () => Promise<void>
}

/** Exported for tests; use SkillsProvider in the app. */
export const SkillsContext = createContext<SkillsState>({ skills: null, reload: async () => {} })

/** Loads the skill list once for the editor (sidebar skill picker, node badges). */
export function SkillsProvider({ children }: { children: ReactNode }) {
  const [skills, setSkills] = useState<Skill[] | null>(null)
  const reload = useCallback(async () => {
    try {
      setSkills(await api.listSkills())
    } catch {
      setSkills([])
    }
  }, [])
  useEffect(() => {
    void reload()
  }, [reload])
  return <SkillsContext.Provider value={{ skills, reload }}>{children}</SkillsContext.Provider>
}

export function useSkills(): Skill[] | null {
  return useContext(SkillsContext).skills
}

/** Display names for a node's skill ids (unknown ids keep their id). */
export function skillNames(skills: Skill[] | null, ids: string[] | null | undefined): string[] {
  return (ids ?? []).map((id) => skills?.find((s) => s.id === id)?.name ?? id)
}
