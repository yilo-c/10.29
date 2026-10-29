import React, { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import remarkBreaks from 'remark-breaks'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { WIKI_LINK_REGEX, resolveWikiLink } from '../../utils/wikiLinks'
import { SiyiFileImage, SiyiFileLink } from './SiyiFileImage'
import { isSiyiFileUri } from '../../utils/siyiFileResolver'
import MermaidView from '../Common/MermaidView'

// 受控放行原样 HTML（Typora 迁入文档含 <u>/<sub>/<details>/<center> 等）：
// raw → sanitize → katex 顺序，sanitizer 负责剥掉 script/iframe 与全部 on* 事件。
// 放行面基于 github schema 扩展：无害排版标签 + katex/代码高亮/脚注 className +
// 相对路径与 siyi-file: 图片（a[href] 由 github schema 默认协议白名单限制，javascript: 天然被拒）。
const sanitizeSchema = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames || []),
    'u', 'sub', 'sup', 'details', 'summary', 'center', 'mark', 'kbd',
    'font', 'video', 'figure', 'figcaption',
  ],
  attributes: {
    ...defaultSchema.attributes,
    '*': [...(defaultSchema.attributes?.['*'] || []), 'className', 'style'],
    // style 值仍有 sanitize 默认协议/表达式防护；此处放开 style 属性本身以兼容
    // Typora 常见 <span style="color:...">，on* 属性不在白名单天然被剥。
    img: [...(defaultSchema.attributes?.img || []), 'src', 'alt', 'title', 'className'],
    input: [...(defaultSchema.attributes?.input || []), 'checked', 'disabled', 'type'],
  },
  protocols: {
    ...defaultSchema.protocols,
    src: ['http', 'https', 'siyi-file', 'data'],
  },
  clobberPrefix: defaultSchema.clobberPrefix ?? 'user-content-',
}

interface MdPreviewProps {
  content: string
  className?: string
}

const MdPreview: React.FC<MdPreviewProps> = ({ content, className }) => {
  const floatingNotes = useStore(s => s.floatingNotes)
  const { t } = useTranslation()

  const preview = useMemo(() => {
    // Preprocess [[wiki links]] —?markdown links
    const noteStubs = floatingNotes.map(n => ({ id: n.id, title: n.title }))
    const preprocessed = content.replace(WIKI_LINK_REGEX, (_full, title: string, display?: string) => {
      const linkTitle = title.trim()
      const displayText = (display || linkTitle).trim()
      const resolved = resolveWikiLink(linkTitle, noteStubs)
      if (resolved) {
        return `[${displayText}](#ref-${resolved.id})`
      }
      return `${displayText}`
    })

    return (
      <div className="prose prose-invert prose-sm max-w-none">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
          // 脚注：remark-footnotes@5 是空壳包（无入口），真实版本线止于 v4 且与 v10 生态脱节——登记为已知缺口
          rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema], rehypeKatex]}
          components={{
            code({ className: cn, children, ...props }) {
              const match = /language-(\w+)/.exec(cn || '')
              const codeStr = String(children).replace(/\n$/, '')
              if (match && match[1] === 'mermaid') {
                return <MermaidView code={codeStr} />
              }
              if (match) {
                return (
                  <SyntaxHighlighter
                    style={oneDark}
                    language={match[1]}
                    PreTag="div"
                    customStyle={{
                      margin: 0, borderRadius: '8px',
                      fontSize: '11px', background: 'rgba(0,0,0,0.3)',
                    }}
                  >
                    {codeStr}
                  </SyntaxHighlighter>
                )
              }
              return (
                <code className="bg-white/[0.08] px-1 rounded text-xs" {...props}>
                  {children}
                </code>
              )
            },
            a({ href, children }) {
              if (href && isSiyiFileUri(href)) {
                const label = typeof children === 'string' ? children : ''
                return <SiyiFileLink uri={href} label={label} />
              }
              return (
                <a href={href} target="_blank" rel="noopener noreferrer" className="text-fluent-blue underline">
                  {children}
                </a>
              )
            },
            img({ src, alt }) {
              return <SiyiFileImage src={src || ''} alt={alt || ''} />
            },
            blockquote({ children }) {
              return (
                <blockquote className="border-l-2 border-white/[0.12] pl-3 italic text-text-secondary my-1">
                  {children}
                </blockquote>
              )
            },
          }}
        >
          {preprocessed}
        </ReactMarkdown>
      </div>
    )
  }, [content, floatingNotes])

  return (
    <div className={`px-3 py-2 overflow-auto text-sm leading-relaxed ${className || ''}`}
      style={{ color: 'var(--text-primary)' }}>
      {content.trim() ? preview : (
        <span className="text-text-dim text-xs italic">{t('note.noContent')}</span>
      )}
    </div>
  )
}

export default React.memo(MdPreview)
