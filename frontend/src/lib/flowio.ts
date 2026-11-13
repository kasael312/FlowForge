/** Import / export of flows as JSON files. */

import { INPUT_TYPES, STEP_TYPES, type FlowEdge, type FlowInput, type FlowNode, type InputField } from '../api/types'
import { defaultStepData } from './steps'

export const EXPORT_VERSION = 1

export interface FlowExport {
  flowforge: number
  name: string
  description: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  input_schema?: InputField[]
}

export function toExport(flow: FlowInput): FlowExport {
  return { flowforge: EXPORT_VERSION, name: flow.name, description: flow.description, nodes: flow.nodes, edges: flow.edges, input_schema: flow.input_schema ?? [] }
}

export function exportFilename(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'flow'
  return `${slug}.flow.json`
}

/** Trigger a browser download of the JSON document. */
export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export class ImportError extends Error {}

/**
 * Parse an exported (or hand-written) flow document into a FlowInput.
 * Structural problems throw ImportError; unknown node fields are dropped and
 * missing step fields get defaults so older exports keep working.
 */
export function parseImport(text: string): FlowInput {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    throw new ImportError('The file is not valid JSON.')
  }
  if (!raw || typeof raw !== 'object') throw new ImportError('The file does not contain a flow object.')
  const doc = raw as Partial<FlowExport>
  if (!Array.isArray(doc.nodes) || !Array.isArray(doc.edges)) throw new ImportError('A flow needs "nodes" and "edges" arrays.')

  const nodes: FlowNode[] = doc.nodes.map((n, i) => {
    const node = n as Partial<FlowNode>
    if (!node || typeof node.id !== 'string' || !node.id) throw new ImportError(`Node #${i + 1} has no id.`)
    const data = (node.data ?? {}) as Partial<FlowNode['data']>
    const type = STEP_TYPES.includes(data.type as never) ? (data.type as FlowNode['data']['type']) : 'code'
    const pos = node.position && typeof node.position.x === 'number' && typeof node.position.y === 'number' ? node.position : { x: i * 260, y: 0 }
    return { id: node.id, type: 'step', position: pos, data: { ...defaultStepData(type), ...data, type } }
  })
  const ids = new Set(nodes.map((n) => n.id))
  if (ids.size !== nodes.length) throw new ImportError('Node ids must be unique.')

  const edges: FlowEdge[] = doc.edges.map((e, i) => {
    const edge = e as Partial<FlowEdge>
    if (!edge || typeof edge.source !== 'string' || typeof edge.target !== 'string') throw new ImportError(`Edge #${i + 1} needs "source" and "target".`)
    if (!ids.has(edge.source) || !ids.has(edge.target)) throw new ImportError(`Edge #${i + 1} references an unknown node.`)
    const handle = edge.sourceHandle === 'true' || edge.sourceHandle === 'false' ? edge.sourceHandle : null
    return { id: typeof edge.id === 'string' && edge.id ? edge.id : `${edge.source}->${edge.target}${handle ? `:${handle}` : ''}`, source: edge.source, target: edge.target, sourceHandle: handle }
  })

  const input_schema: InputField[] = Array.isArray(doc.input_schema)
    ? doc.input_schema
        .filter((f) => !!f && typeof (f as Partial<InputField>).name === 'string')
        .map((raw) => (raw as Partial<InputField>))
        .map((f) => ({
          name: String(f.name),
          label: typeof f.label === 'string' ? f.label : '',
          type: INPUT_TYPES.includes(f.type as never) ? (f.type as InputField['type']) : 'text',
          required: Boolean(f.required),
          default: f.default ?? null,
          options: Array.isArray(f.options) ? f.options.map(String) : [],
          placeholder: typeof f.placeholder === 'string' ? f.placeholder : '',
          description: typeof f.description === 'string' ? f.description : '',
        }))
    : []
  return {
    name: typeof doc.name === 'string' && doc.name.trim() ? doc.name.trim() : 'Imported flow',
    description: typeof doc.description === 'string' ? doc.description : '',
    nodes,
    edges,
    input_schema,
  }
}
