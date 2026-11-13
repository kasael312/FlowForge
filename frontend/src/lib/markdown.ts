/** Tiny Markdown → HTML renderer for previews (headings, lists, code, emphasis, links). Escapes HTML. */

function escape(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function inline(s: string): string {
  let out = escape(s)
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>')
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  return out
}

export function renderMarkdown(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  const html: string[] = []
  let list: 'ul' | 'ol' | null = null
  let para: string[] = []
  let code: string[] | null = null

  const flushPara = () => {
    if (para.length) html.push(`<p>${inline(para.join(' '))}</p>`)
    para = []
  }
  const closeList = () => {
    if (list) html.push(`</${list}>`)
    list = null
  }

  for (const raw of lines) {
    if (code !== null) {
      if (raw.startsWith('```')) {
        html.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`)
        code = null
      } else code.push(raw)
      continue
    }
    const line = raw.trimEnd()
    if (line.startsWith('```')) {
      flushPara()
      closeList()
      code = []
      continue
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) {
      flushPara()
      closeList()
      html.push(`<h${h[1]!.length}>${inline(h[2]!)}</h${h[1]!.length}>`)
      continue
    }
    const li = /^\s*(?:[-*]|(\d+)[.)])\s+(.*)$/.exec(line)
    if (li) {
      flushPara()
      const kind = li[1] ? 'ol' : 'ul'
      if (list !== kind) {
        closeList()
        html.push(`<${kind}>`)
        list = kind
      }
      const task = /^\[([ xX])\]\s+(.*)$/.exec(li[2]!)
      html.push(task ? `<li><input type="checkbox" disabled${task[1] !== ' ' ? ' checked' : ''}> ${inline(task[2]!)}</li>` : `<li>${inline(li[2]!)}</li>`)
      continue
    }
    if (!line.trim()) {
      flushPara()
      closeList()
      continue
    }
    if (list) closeList()
    para.push(line.trim())
  }
  if (code !== null) html.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`)
  flushPara()
  closeList()
  return html.join('\n')
}
