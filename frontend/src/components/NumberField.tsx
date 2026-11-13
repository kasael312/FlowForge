import { useState } from 'react'

interface Props {
  id: string
  value: number
  min: number
  max: number
  className?: string
  onCommit: (value: number) => void
}

/**
 * Number input that tolerates an empty field while typing. The parent value is
 * only updated when the text parses; on blur the field snaps back to the
 * clamped committed value.
 */
export function NumberField({ id, value, min, max, className, onCommit }: Props) {
  const [text, setText] = useState(String(value))
  const [lastValue, setLastValue] = useState(value)
  // Re-sync the draft when the committed value changes from outside (derived state, no effect).
  if (value !== lastValue) {
    setLastValue(value)
    setText(String(value))
  }

  const clamp = (n: number) => Math.min(max, Math.max(min, n))

  return (
    <input
      id={id}
      type="number"
      min={min}
      max={max}
      className={className}
      value={text}
      onChange={(e) => {
        const raw = e.target.value
        setText(raw)
        if (raw.trim() === '') return
        const n = Number(raw)
        if (Number.isFinite(n)) onCommit(clamp(Math.trunc(n)))
      }}
      onBlur={() => setText(String(value))}
    />
  )
}
