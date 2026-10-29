import React, { memo, useEffect, useState } from 'react'

// 复用与 chat/ArtifactRenderer 一致的严格安全渲染策略：
// 动态加载 mermaid（避免打进主 chunk）、securityLevel:'strict'、按需 render 出 SVG。
let mermaidPromise: Promise<MermaidApi> | null = null

interface MermaidApi {
  initialize: (config: Record<string, unknown>) => void
  render: (id: string, code: string) => Promise<{ svg: string }> | string
}

function loadMermaid(): Promise<MermaidApi> {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((m) => {
      const mermaid = (m.default ?? m) as unknown as MermaidApi
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark' })
      return mermaid
    })
  }
  return mermaidPromise
}

// mermaid.render 要求全局唯一 id，避免同页多图 SVG 互相覆盖
let seq = 0

interface MermaidViewProps {
  code: string
}

/**
 * 笔记 / 预览内 ```mermaid 代码块的图表渲染。
 * 语义：加载/渲染中给占位；失败降级为原始代码块（不丢内容），与 Typora 往返「degraded」一致。
 * 外层容器恒带 `mermaid` 类名，保证渲染管线对账能识别该块已被接管。
 */
const MermaidView: React.FC<MermaidViewProps> = ({ code }) => {
  const [svg, setSvg] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setFailed(false)
    setSvg(null)
    let id: string
    loadMermaid()
      .then((mermaid) => {
        id = `siyi-mermaid-${++seq}`
        // 显式 Promise.resolve：mermaid.render 的真实签名返回
        // Promise<RenderResult> | string，直接 return 会让 then 重载推断
        // 选错分支（把 string 吞掉），下游 res.svg 报 TS2339。
        return Promise.resolve(mermaid.render(id, code))
      })
      .then((res) => {
        if (cancelled) return
        setSvg(typeof res === 'string' ? res : res.svg)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [code])

  if (failed) {
    return (
      <div className="mermaid my-1">
        <pre className="bg-white/[0.06] text-text-secondary text-xs rounded-lg p-2 overflow-auto">
          <code>{code}</code>
        </pre>
      </div>
    )
  }
  if (!svg) {
    return <div className="mermaid my-1 text-2xs text-text-dim italic">图表渲染中…</div>
  }
  return (
    <div className="mermaid my-2 text-center" data-testid="mermaid-diagram" dangerouslySetInnerHTML={{ __html: svg }} />
  )
}

export default memo(MermaidView)
