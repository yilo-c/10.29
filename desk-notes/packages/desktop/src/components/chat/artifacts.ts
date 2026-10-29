export interface ArtifactBase {
  id: string
  title?: string
  status?: 'ready' | 'running' | 'failed'
  source?: string
}

export interface MarkdownArtifact extends ArtifactBase {
  type: 'markdown'
  content: string
}

export interface MermaidArtifact extends ArtifactBase {
  type: 'mermaid'
  definition: string
}

export interface ChartArtifact extends ArtifactBase {
  type: 'chart'
  chartType: 'bar' | 'line'
  labels: string[]
  series: Array<{ name: string; values: number[]; color?: string }>
}

export interface TableArtifact extends ArtifactBase {
  type: 'table'
  columns: string[]
  rows: string[][]
}

export interface ImageArtifact extends ArtifactBase {
  type: 'image'
  src: string
  alt: string
}

export interface ReportArtifact extends ArtifactBase {
  type: 'report'
  content: string
  format?: 'markdown' | 'text'
}

export type ChatArtifact =
  | MarkdownArtifact
  | MermaidArtifact
  | ChartArtifact
  | TableArtifact
  | ImageArtifact
  | ReportArtifact

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function safeImageSource(value: unknown): string | null {
  const source = stringValue(value)
  if (!source) return null
  if (source.startsWith('/') || source.startsWith('./') || source.startsWith('../')) return source
  if (/^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(source)) return source
  try {
    const protocol = new URL(source).protocol
    return protocol === 'https:' ? source : null
  } catch {
    return null
  }
}

function baseArtifact(value: Record<string, unknown>, index: number): ArtifactBase {
  return {
    id: stringValue(value.id) ?? `artifact-${index}`,
    ...(stringValue(value.title) ? { title: value.title as string } : {}),
    ...(value.status === 'ready' || value.status === 'running' || value.status === 'failed' ? { status: value.status } : {}),
    ...(stringValue(value.source) ? { source: value.source as string } : {}),
  }
}

/** Convert untrusted backend artifact payloads into renderable artifacts. */
export function normalizeChatArtifacts(value: unknown): ChatArtifact[] {
  if (!Array.isArray(value)) return []

  return value.slice(0, 50).flatMap((item, index): ChatArtifact[] => {
    if (!isRecord(item) || typeof item.type !== 'string') return []
    const base = baseArtifact(item, index)
    if (item.type === 'markdown' || item.type === 'report') {
      const content = stringValue(item.content)
      return content ? [{ ...base, type: item.type, content, ...(item.type === 'report' && (item.format === 'markdown' || item.format === 'text') ? { format: item.format } : {}) }] : []
    }
    if (item.type === 'mermaid') {
      const definition = stringValue(item.definition)
      return definition ? [{ ...base, type: 'mermaid', definition }] : []
    }
    if (item.type === 'table') {
      const columns = Array.isArray(item.columns) ? item.columns.filter((column): column is string => typeof column === 'string' && column.length > 0) : []
      const rows = Array.isArray(item.rows)
        ? item.rows.filter(Array.isArray).map((row) => row.map((cell) => typeof cell === 'string' ? cell : String(cell ?? '')).slice(0, columns.length))
        : []
      return columns.length ? [{ ...base, type: 'table', columns, rows }] : []
    }
    if (item.type === 'chart' && (item.chartType === 'bar' || item.chartType === 'line')) {
      const labels = Array.isArray(item.labels) ? item.labels.filter((label): label is string => typeof label === 'string') : []
      const series = Array.isArray(item.series) ? item.series.flatMap((entry): ChartArtifact['series'] => {
        if (!isRecord(entry) || typeof entry.name !== 'string' || !Array.isArray(entry.values)) return []
        const values = entry.values.map((value) => typeof value === 'number' && Number.isFinite(value) ? value : null)
        if (values.some((value) => value === null)) return []
        return [{ name: entry.name, values: values as number[], ...(typeof entry.color === 'string' && /^#[\da-f]{3,8}$/i.test(entry.color) ? { color: entry.color } : {}) }]
      }) : []
      return labels.length && series.length ? [{ ...base, type: 'chart', chartType: item.chartType, labels, series }] : []
    }
    if (item.type === 'image') {
      const src = safeImageSource(item.src)
      const alt = stringValue(item.alt)
      return src && alt ? [{ ...base, type: 'image', src, alt }] : []
    }
    return []
  })
}
