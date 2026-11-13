import { RulesEditor } from '../components/RulesEditor'

/** Global rules: constraints prepended to every LLM step of every flow. */
export function RulesPage() {
  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="text-2xl font-semibold">Rules</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Rules are short constraints added to the top of every LLM step's system prompt, in this order: <b>global → flow → step</b>, then persona, skills and the step's own prompt. Built-in
        rules were copied into your account; edit or disable them freely. Flow and step rules live in the flow editor (<i>Rules</i> button).
      </p>
      <RulesEditor scope="global" />
    </div>
  )
}
