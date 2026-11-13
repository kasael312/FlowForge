import { useEffect, useReducer, useState } from 'react'
import { api } from '../api/client'
import type { LogEvent } from '../api/types'
import { initialRunLogState, runLogReducer, type RunLogState } from '../lib/runLog'

export type Connection = 'connecting' | 'open' | 'closed' | 'error'

/**
 * Subscribe to `WS /runs/{id}/logs` and fold events into a RunLogState.
 * The socket is closed when the component unmounts or the run id changes.
 */
export function useRunLogs(runId: string | undefined): { state: RunLogState; connection: Connection } {
  const [state, dispatch] = useReducer(runLogReducer, initialRunLogState)
  const [connection, setConnection] = useState<Connection>('connecting')

  useEffect(() => {
    if (!runId) return
    const ws = new WebSocket(api.logsUrl(runId))
    let ended = false
    ws.onopen = () => setConnection('open')
    ws.onmessage = (msg: MessageEvent<string>) => {
      let ev: LogEvent
      try {
        ev = JSON.parse(msg.data) as LogEvent
      } catch {
        return
      }
      if (ev.event === 'end') ended = true
      dispatch(ev)
    }
    ws.onerror = () => setConnection('error')
    ws.onclose = () => setConnection(ended ? 'closed' : 'error')
    return () => {
      ws.onclose = null
      ws.onerror = null
      ws.close()
    }
  }, [runId])

  return { state, connection }
}
