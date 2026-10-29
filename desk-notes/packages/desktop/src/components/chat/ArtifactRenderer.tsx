import { useEffect, useId, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { normalizeChatArtifacts, type ChartArtifact, type ChatArtifact, type MermaidArtifact, type TableArtifact } from './artifacts'
import { sanitizeSvg } from '../../utils/sanitize'

interface ArtifactRendererProps {
  content?: string
  artifacts?: ChatArtifact[]
}

const chartColors = ['#2563eb', '#0f766e', '#d97706', '#be123c']

function MermaidBlock({ artifact }: { artifact: MermaidArtifact }) {
  const renderId = useId().replace(/:/g, '')
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    if (/<\/?script\b|javascript:|data:/i.test(artifact.definition)) {
      setError('图表内容包含不允许的链接或脚本')
      return () => { cancelled = true }
    }
    import('mermaid').then(({ default: mermaid }) => {
      if (cancelled) return
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'default' })
      return mermaid.render(`artifact-${renderId}`, artifact.definition)
    }).then((result) => {
      if (!cancelled && result) {
        setSvg(result.svg)
        setError(null)
      }
    }).catch(() => {
      if (!cancelled) setError('图表语法无法解析')
    })
    return () => { cancelled = true }
  }, [artifact.definition, renderId])

  if (error) return <pre className="text-xs text-[var(--danger)] whitespace-pre-wrap">{artifact.definition}</pre>
  if (!svg) return <div className="text-xs text-[var(--text-muted)]">正在绘制图表...</div>
  return <div className="overflow-x-auto" role="img" aria-label={artifact.title ?? '流程图'} dangerouslySetInnerHTML={{ __html: sanitizeSvg(svg) }} />
}

function ChartBlock({ artifact }: { artifact: ChartArtifact }) {
  if (artifact.labels.length === 0 || artifact.series.length === 0) return <div className="text-xs text-[var(--text-muted)]">图表数据为空</div>
  const maxValue = Math.max(1, ...artifact.series.flatMap((series) => series.values))
  const width = Math.max(360, artifact.labels.length * 72)
  const height = 220
  const plotHeight = 170
  const groupWidth = width / Math.max(1, artifact.labels.length)
  const seriesWidth = Math.max(8, (groupWidth * 0.62) / Math.max(1, artifact.series.length))

  return <div className="overflow-x-auto"><svg viewBox={`0 0 ${width} ${height}`} className="min-w-[360px] w-full h-56" role="img" aria-label={artifact.title ?? '数据图表'}>
    <line x1="28" y1="12" x2="28" y2={plotHeight + 12} stroke="var(--separator)" />
    <line x1="28" y1={plotHeight + 12} x2={width - 8} y2={plotHeight + 12} stroke="var(--separator)" />
    {artifact.labels.map((label, index) => {
      const x = 28 + groupWidth * index + groupWidth / 2
      return <text key={`${label}-${index}`} x={x} y={height - 10} textAnchor="middle" className="fill-[var(--text-muted)] text-xs">{label}</text>
    })}
    {artifact.series.flatMap((series, seriesIndex) => series.values.map((value, index) => {
      const barHeight = (Math.max(0, value) / maxValue) * plotHeight
      const x = 28 + groupWidth * index + groupWidth * 0.19 + seriesIndex * seriesWidth
      const y = plotHeight + 12 - barHeight
      return artifact.chartType === 'bar'
        ? <rect key={`${series.name}-${index}`} x={x} y={y} width={seriesWidth - 3} height={barHeight} fill={series.color ?? chartColors[seriesIndex % chartColors.length]} rx="2" />
        : <circle key={`${series.name}-${index}`} cx={x + seriesWidth / 2} cy={y} r="3" fill={series.color ?? chartColors[seriesIndex % chartColors.length]} />
    }))}
    {artifact.chartType === 'line' && artifact.series.map((series, seriesIndex) => {
      const points = series.values.map((value, index) => {
        const x = 28 + groupWidth * index + groupWidth / 2
        const y = plotHeight + 12 - (Math.max(0, value) / maxValue) * plotHeight
        return `${x},${y}`
      }).join(' ')
      return <polyline key={series.name} points={points} fill="none" stroke={series.color ?? chartColors[seriesIndex % chartColors.length]} strokeWidth="2" />
    })}
  </svg></div>
}

function TableBlock({ artifact }: { artifact: TableArtifact }) {
  if (artifact.columns.length === 0) return <div className="text-xs text-[var(--text-muted)]">表格数据不完整</div>
  return <div className="overflow-x-auto"><table className="w-full text-xs border-collapse"><thead><tr>{artifact.columns.map((column) => <th key={column} className="text-left px-2 py-1.5 border-b font-semibold">{column}</th>)}</tr></thead><tbody>{artifact.rows.map((row, rowIndex) => <tr key={rowIndex}>{artifact.columns.map((_, columnIndex) => <td key={columnIndex} className="px-2 py-1.5 border-b border-[var(--separator)]">{row[columnIndex] ?? ''}</td>)}</tr>)}</tbody></table></div>
}

function ArtifactCard({ artifact }: { artifact: ChatArtifact }) {
  if (artifact.type === 'mermaid') return <MermaidBlock artifact={artifact} />
  if (artifact.type === 'chart') return <ChartBlock artifact={artifact} />
  if (artifact.type === 'table') return <TableBlock artifact={artifact} />
  if (artifact.type === 'image') return <img src={artifact.src} alt={artifact.alt} loading="lazy" className="max-h-96 max-w-full rounded-lg object-contain" />
  if (artifact.type === 'markdown' || artifact.type === 'report') return <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content}</ReactMarkdown>
  return null
}

export function ArtifactRenderer({ content, artifacts = [] }: ArtifactRendererProps) {
  const safeArtifacts = normalizeChatArtifacts(artifacts)
  return <div className="flex flex-col gap-3">
    {content && <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>}
    {safeArtifacts.map((artifact) => <section key={artifact.id} className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3">
      {artifact.title && <h3 className="mb-2 text-xs font-semibold text-[var(--text-secondary)]">{artifact.title}</h3>}
      <ArtifactCard artifact={artifact} />
      {artifact.source && <div className="mt-2 text-xs text-[var(--text-muted)]">来源：{artifact.source}</div>}
    </section>)}
  </div>
}
