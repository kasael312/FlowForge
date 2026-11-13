/** Presentation helpers for LLM model configurations. */

import type { ModelProvider } from '../api/types'

export const PROVIDER_LABEL: Record<ModelProvider, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  ollama: 'Ollama (local)',
  openai_compatible: 'OpenAI-compatible (Groq, DeepSeek, OpenRouter…)',
}

export const PROVIDER_HINT: Record<ModelProvider, string> = {
  anthropic: 'Uses your Anthropic key from API Keys (or the server key). Sampling temperature is not supported on Claude 5.x; use effort instead.',
  openai: 'Uses your OpenAI key from API Keys (or the server key).',
  ollama: 'Local models through http://localhost:11434. No key required; make sure the model is pulled.',
  openai_compatible: 'Any host exposing /chat/completions. Set the base URL (e.g. https://api.groq.com/openai/v1) and attach an "Other" key.',
}

export const MODEL_SUGGESTIONS: Record<ModelProvider, string[]> = {
  anthropic: ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'o3'],
  ollama: ['llama3.2', 'qwen2.5-coder', 'deepseek-coder-v2', 'mistral'],
  openai_compatible: ['llama-3.3-70b-versatile', 'deepseek-chat', 'openrouter/auto'],
}

/** Which stored-key provider a model provider draws its credentials from. */
export const KEY_PROVIDER_FOR: Record<ModelProvider, 'anthropic' | 'openai' | 'other' | null> = {
  anthropic: 'anthropic',
  openai: 'openai',
  ollama: null,
  openai_compatible: 'other',
}

/** Short tag shown on canvas nodes and run tables, e.g. "openai/gpt-4o". */
export function shortModel(label: string | null | undefined): string {
  if (!label) return ''
  return label.length > 28 ? `${label.slice(0, 27)}…` : label
}
