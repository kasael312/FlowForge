import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type OnSelectionChangeParams,
} from '@xyflow/react'
import { api } from '../api/client'
import { BRANCH_HANDLES, STEP_TYPES, type Flow, type FlowEdge, type FlowNode, type GraphReport, type InputField, type SandboxSettings, type StepData, type StepType } from '../api/types'
import { STEP_META, defaultStepData, dropDanglingGotos, newNodeId, reconcileEdgesForType } from '../lib/steps'
import { downloadJson, exportFilename } from '../lib/flowio'
import { StepNode, type StepFlowNode } from '../components/StepNode'
import { StepSidebar } from '../components/StepSidebar'
import { TryStepPanel } from '../components/TryStepPanel'
import { InputSchemaEditor } from '../components/InputSchemaEditor'
import { TriggersPanel } from '../components/TriggersPanel'
import { SandboxSettingsEditor } from '../components/SandboxSettingsEditor'
import { Button, ErrorBanner, Modal } from '../components/ui'
import { ModelsProvider } from '../lib/ModelsContext'
import { SkillsProvider } from '../lib/SkillsContext'
import { ToolsProvider } from '../lib/ToolsContext'
import { RulesEditor } from '../components/RulesEditor'
import { ResolvedPromptModal } from '../components/ResolvedPromptModal'

const nodeTypes = { step: StepNode }

/** Convert API nodes/edges to React Flow objects (they are structurally compatible). */
function toRf(nodes: FlowNode[], edges: FlowEdge[]): { nodes: StepFlowNode[]; edges: Edge[] } {
  return {
    nodes: nodes.map((n) => ({ ...n, type: 'step' as const, data: n.data })) as StepFlowNode[],
    edges: edges.map((e) => ({ ...e, sourceHandle: e.sourceHandle ?? undefined, animated: false })),
  }
}

/** Strip React Flow runtime fields before saving. */
function toApi(nodes: StepFlowNode[], edges: Edge[]): { nodes: FlowNode[]; edges: FlowEdge[] } {
  return {
    nodes: nodes.map((n) => ({ id: n.id, type: 'step', position: n.position, data: n.data })),
    edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? null, label: typeof e.label === 'string' ? e.label : null })),
  }
}

export function FlowEditorPage() {
  return (
    <ModelsProvider>
      <ToolsProvider>
        <SkillsProvider>
          <FlowEditor />
        </SkillsProvider>
      </ToolsProvider>
    </ModelsProvider>
  )
}

function FlowEditor() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [flow, setFlow] = useState<Flow | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [nodes, setNodes] = useState<StepFlowNode[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<GraphReport | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [tryOpen, setTryOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [inputsOpen, setInputsOpen] = useState(false)
  const [triggersOpen, setTriggersOpen] = useState(false)
  const [inputSchema, setInputSchema] = useState<InputField[]>([])
  const [sandboxOpen, setSandboxOpen] = useState(false)
  const [rulesOpen, setRulesOpen] = useState<{ stepId: string | null } | null>(null)
  const [promptOpen, setPromptOpen] = useState<string | null>(null)
  const [sandbox, setSandbox] = useState<SandboxSettings>({})
  const location = useLocation()
  const [notice, setNotice] = useState<string | null>(() => (location.state as { notice?: string } | null)?.notice ?? null)
  const fileInput = useRef<HTMLInputElement>(null)

  const loadFlow = useCallback((f: Flow) => {
    setFlow(f)
    setName(f.name)
    setDescription(f.description)
    setInputSchema(f.input_schema ?? [])
    setSandbox(f.sandbox ?? {})
    const rf = toRf(f.nodes, f.edges)
    setNodes(rf.nodes)
    setEdges(rf.edges)
    setDirty(false)
    setReport(null)
  }, [])

  useEffect(() => {
    api.getFlow(id).then(loadFlow).catch((e: Error) => setError(e.message))
  }, [id, loadFlow])

  const markDirty = () => setDirty(true)

  const onNodesChange = useCallback((changes: NodeChange<StepFlowNode>[]) => {
    setNodes((ns) => applyNodeChanges(changes, ns))
    if (changes.some((c) => c.type !== 'select' && c.type !== 'dimensions')) setDirty(true)
  }, [])
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((es) => applyEdgeChanges(changes, es))
    if (changes.some((c) => c.type !== 'select')) setDirty(true)
  }, [])
  const onConnect = useCallback(
    (conn: Connection) => {
      setEdges((es) => {
        const source = nodes.find((n) => n.id === conn.source)
        const branch = source ? BRANCH_HANDLES[source.data.type] : undefined
        const handle = branch ? (conn.sourceHandle ?? branch[0]) : null
        const filtered = es.filter((e) => !(e.source === conn.source && (e.sourceHandle ?? null) === handle))
        return addEdge({ ...conn, sourceHandle: handle ?? undefined, id: `${conn.source}->${conn.target}${handle ? `:${handle}` : ''}` }, filtered)
      })
      markDirty()
    },
    [nodes],
  )
  const onSelectionChange = useCallback(({ nodes: sel }: OnSelectionChangeParams) => {
    setSelectedId(sel[0]?.id ?? null)
  }, [])

  const selected = useMemo(() => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId])
  const apiGraph = useMemo(() => toApi(nodes, edges), [nodes, edges])

  const addStep = (type: StepType) => {
    const nid = newNodeId(type, new Set(nodes.map((n) => n.id)))
    const last = nodes[nodes.length - 1]
    const position = last ? { x: last.position.x + 260, y: last.position.y } : { x: 80, y: 120 }
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), { id: nid, type: 'step', position, data: defaultStepData(type), selected: true }])
    setSelectedId(nid)
    markDirty()
  }

  const updateSelected = (data: StepData) => {
    if (!selected) return
    setNodes((ns) => ns.map((n) => (n.id === selected.id ? { ...n, data } : n)))
    markDirty()
  }

  const changeSelectedType = (type: StepType) => {
    if (!selected) return
    const base = defaultStepData(type, selected.data.label)
    const data: StepData = { ...base, label: selected.data.label, system_prompt: selected.data.system_prompt }
    setNodes((ns) => ns.map((n) => (n.id === selected.id ? { ...n, data } : n)))
    setEdges((es) => reconcileEdgesForType(selected.id, type, es as FlowEdge[]) as Edge[])
    markDirty()
  }

  const deleteSelected = () => {
    if (!selected) return
    const sid = selected.id
    setEdges((es) => es.filter((e) => e.source !== sid && e.target !== sid))
    setNodes((ns) => dropDanglingGotos(ns.filter((n) => n.id !== sid) as FlowNode[]) as StepFlowNode[])
    setSelectedId(null)
    setTryOpen(false)
    markDirty()
  }

  /** Validate on the server; returns true when there are no blocking errors. */
  const validate = async (): Promise<boolean> => {
    const rep = await api.validateFlow({ name: name || 'flow', description, ...apiGraph })
    setReport(rep)
    return rep.errors.length === 0
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      if (!(await validate())) return
      const saved = await api.updateFlow(id, { name, description, input_schema: inputSchema, sandbox, ...apiGraph })
      setFlow(saved)
      setDirty(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  /** Export from the server so models (by label), skills and rules travel with the graph. */
  const exportJson = async () => {
    try {
      downloadJson(exportFilename(name), await api.exportFlow(id))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  /** Import replaces this flow on the server (graph, inputs, sandbox, rules); models map by label, skills by name. */
  const importJson = async (file: File) => {
    setError(null)
    try {
      let doc: unknown
      try {
        doc = JSON.parse(await file.text())
      } catch {
        throw new Error('The file is not valid JSON.')
      }
      if (nodes.length > 0 && !window.confirm('Replace this flow with the imported one? This saves immediately.')) return
      const result = await api.importIntoFlow(id, doc)
      loadFlow(result.flow)
      setSelectedId(null)
      setReport(null)
      setDirty(false)
      setNotice(`Imported ${result.flow.nodes.length} steps from ${file.name}.${result.warnings.length ? `\nWarnings:\n- ${result.warnings.join('\n- ')}` : ''}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const saveAsTemplate = async () => {
    const tplName = window.prompt('Template name', name)
    if (!tplName) return
    try {
      await api.saveTemplate({ flow_id: id, name: tplName, description })
      setNotice(`Saved as template “${tplName}”. Find it under Templates.`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const setVisibility = async (visibility: Flow['visibility']) => {
    const previous = flow
    // optimistic: flip immediately, revert if the server refuses
    setFlow((f) => (f ? { ...f, visibility } : f))
    try {
      const saved = await api.updateFlow(id, { visibility })
      setFlow(saved)
    } catch (e) {
      setFlow(previous)
      setError((e as Error).message)
    }
  }

  if (error && !flow) {
    return (
      <div className="p-6">
        <ErrorBanner message={error} />
        <Link to="/flows" className="mt-3 inline-block text-sm underline">
          Back to flows
        </Link>
      </div>
    )
  }
  if (!flow) return <p className="p-6 text-sm text-zinc-500">Loading…</p>

  const shareUrl = flow.share_token ? `${window.location.origin}/shared/${flow.share_token}` : null

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col md:h-screen">
      <header className="flex flex-wrap items-center gap-2 border-b border-zinc-200 px-4 py-2 dark:border-zinc-800">
        <input
          aria-label="Flow name"
          className="min-w-48 rounded border border-transparent bg-transparent px-2 py-1 text-lg font-semibold hover:border-zinc-300 focus:border-zinc-400 focus:outline-none"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            markDirty()
          }}
        />
        <input
          aria-label="Flow description"
          placeholder="Description"
          className="min-w-64 flex-1 rounded border border-transparent bg-transparent px-2 py-1 text-sm text-zinc-600 hover:border-zinc-300 focus:border-zinc-400 focus:outline-none dark:text-zinc-400"
          value={description}
          onChange={(e) => {
            setDescription(e.target.value)
            markDirty()
          }}
        />
        <div className="flex items-center gap-1">
          <span className="mr-1 text-xs text-zinc-500">Add step:</span>
          {STEP_TYPES.map((t) => (
            <Button key={t} variant="ghost" className="px-2 py-1 text-xs" onClick={() => addStep(t)}>
              <span className={`mr-1 inline-block h-2 w-2 rounded-full ${STEP_META[t].color}`} />
              {STEP_META[t].label}
            </Button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" className="text-xs" onClick={() => setInputsOpen(true)}>
            Inputs{inputSchema.length > 0 ? ` (${inputSchema.length})` : ''}
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => setTriggersOpen(true)} disabled={dirty}>
            Triggers
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => setRulesOpen({ stepId: null })}>
            Rules
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => setSandboxOpen(true)}>
            Sandbox{sandbox.network && sandbox.network !== 'none' ? ` (${sandbox.network})` : ''}
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => void validate().catch((e: Error) => setError(e.message))}>
            Validate
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => fileInput.current?.click()}>
            Import
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="hidden"
            aria-label="Import flow file"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void importJson(f)
              e.target.value = ''
            }}
          />
          <Button variant="ghost" className="text-xs" onClick={() => void exportJson()}>
            Export
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => void saveAsTemplate()} disabled={dirty}>
            Save as template
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => setShareOpen(true)}>
            {flow.visibility === 'shared_link' ? 'Shared' : 'Share'}
          </Button>
          {dirty && <span className="text-xs text-amber-600">Unsaved changes</span>}
          <Button variant="primary" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
          <Button onClick={() => navigate(`/flows/${id}/run`)} disabled={dirty}>
            Run
          </Button>
        </div>
      </header>
      {(error || notice || (report && (report.errors.length > 0 || report.warnings.length > 0))) && (
        <div className="space-y-2 px-4 pt-2">
          <ErrorBanner message={error} onClose={() => setError(null)} />
          {notice && (
            <div role="status" className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
              {notice}
            </div>
          )}
          {report && report.errors.length > 0 && (
            <div role="alert" className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">
              <div className="font-medium">Fix these before saving:</div>
              <ul className="list-disc pl-5">
                {report.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          {report && report.warnings.length > 0 && (
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200" data-testid="warnings">
              <div className="font-medium">Warnings</div>
              <ul className="list-disc pl-5">
                {report.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          {report && report.errors.length === 0 && report.warnings.length === 0 && !dirty && null}
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onSelectionChange={onSelectionChange}
            fitView
            deleteKeyCode={['Backspace', 'Delete']}
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </div>
        {selected ? (
          <div className="flex w-80 flex-col overflow-y-auto border-l border-zinc-200 dark:border-zinc-800">
            {tryOpen && (
              <div className="p-3">
                <TryStepPanel flowId={id} node={{ id: selected.id, type: 'step', position: selected.position, data: selected.data }} onClose={() => setTryOpen(false)} />
              </div>
            )}
            <StepSidebar
              node={{ id: selected.id, type: 'step', position: selected.position, data: selected.data }}
              nodes={apiGraph.nodes}
              onChange={updateSelected}
              onChangeType={changeSelectedType}
              onDelete={deleteSelected}
              onTry={selected.data.type === 'shell' ? undefined : () => setTryOpen(true)}
              onRules={() => setRulesOpen({ stepId: selected.id })}
              onViewPrompt={() => setPromptOpen(selected.id)}
            />
          </div>
        ) : (
          <aside className="w-80 border-l border-zinc-200 p-4 text-sm text-zinc-500 dark:border-zinc-800">
            <p>Select a step to edit it, drag from a handle to connect steps.</p>
            <p className="mt-2">The flow starts at the only step without an incoming edge. Condition steps branch on their green (true) and red (false) handles.</p>
            <p className="mt-2">Loops are allowed only through a condition step or via “on failure → go to”.</p>
          </aside>
        )}
      </div>

      {inputsOpen && (
        <Modal title="Run inputs" onClose={() => setInputsOpen(false)}>
          <div className="max-h-[70vh] overflow-y-auto">
            <InputSchemaEditor
              schema={inputSchema}
              onChange={(sch) => {
                setInputSchema(sch)
                markDirty()
              }}
            />
          </div>
          <div className="mt-4 flex justify-end">
            <Button onClick={() => setInputsOpen(false)}>Done</Button>
          </div>
        </Modal>
      )}
      {sandboxOpen && (
        <Modal title="Sandbox limits" onClose={() => setSandboxOpen(false)}>
          <SandboxSettingsEditor
            value={sandbox}
            onChange={(v) => {
              setSandbox(v)
              markDirty()
            }}
          />
          <div className="mt-4 flex justify-end">
            <Button onClick={() => setSandboxOpen(false)}>Done</Button>
          </div>
        </Modal>
      )}
      {rulesOpen && (
        <Modal title="Rules for this flow" onClose={() => setRulesOpen(null)}>
          <div className="max-h-[70vh] overflow-y-auto">
            <RulesEditor scope="flow+step" flowId={id} steps={apiGraph.nodes.filter((n) => STEP_META[n.data.type].usesLlm).map((n) => ({ id: n.id, label: n.data.label || n.id }))} initialStepId={rulesOpen.stepId} />
          </div>
          <div className="mt-4 flex justify-end">
            <Button onClick={() => setRulesOpen(null)}>Done</Button>
          </div>
        </Modal>
      )}
      {promptOpen && <ResolvedPromptModal flowId={id} stepId={promptOpen} onClose={() => setPromptOpen(null)} />}
      {triggersOpen && (
        <Modal title="Triggers" onClose={() => setTriggersOpen(false)}>
          <div className="max-h-[70vh] overflow-y-auto">
            <TriggersPanel flowId={id} />
          </div>
          <div className="mt-4 flex justify-end">
            <Button onClick={() => setTriggersOpen(false)}>Done</Button>
          </div>
        </Modal>
      )}
      {shareOpen && (
        <Modal title="Share this flow" onClose={() => setShareOpen(false)}>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Anyone with the link can view the steps and clone them into their own account. They cannot edit your flow or run it.
          </p>
          <label className="mt-3 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={flow.visibility === 'shared_link'}
              onChange={(e) => void setVisibility(e.target.checked ? 'shared_link' : 'private')}
            />
            Enable link sharing
          </label>
          {flow.visibility === 'shared_link' && shareUrl && (
            <div className="mt-3 flex gap-2">
              <input readOnly aria-label="Share link" className="flex-1 rounded border border-zinc-300 bg-zinc-50 px-2 py-1 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900" value={shareUrl} onFocus={(e) => e.target.select()} />
              <Button
                onClick={() => {
                  void navigator.clipboard?.writeText(shareUrl)
                  setNotice('Share link copied.')
                  setShareOpen(false)
                }}
              >
                Copy
              </Button>
            </div>
          )}
          <div className="mt-4 flex justify-end">
            <Button onClick={() => setShareOpen(false)}>Done</Button>
          </div>
        </Modal>
      )}
    </div>
  )
}
