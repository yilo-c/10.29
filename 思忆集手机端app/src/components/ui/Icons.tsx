import React from 'react'

/**
 * 图标系统 / Icon system
 *
 * 风格：圆润双色（参考小米笔记的视觉语言）
 *   - 主线用 currentColor 描边，跟随文字颜色
 *   - 点缀部分用强调色填充，形成双色层次
 *   - 统一 2px 圆头描边（原 1.5px 偏细，小尺寸下发虚）
 *   - 所有转角用 round cap/join，避免生硬直角
 *
 * 双色的两条实现路径：
 *   1. accent 为 true 时，点缀部分使用 var(--accent)（主题强调色）
 *   2. 不传时，点缀部分用 currentColor + 0.18 透明度，保持单色但仍有层次
 *      这样在纯色场景（如深色顶栏）里不会突兀
 */

interface IconProps {
  size?: number
  className?: string
  /** 是否用主题强调色作为点缀色（默认 false，走 currentColor 淡化） */
  accent?: boolean
  /** 主线粗细，默认 2 */
  strokeWidth?: number
}

/* ── 基础图标 ── */

export const IconCheck: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M4 12.5l5 5L20 6.5" />
  </svg>
)

export const IconPlus: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M12 5v14M5 12h14" />
  </svg>
)

export const IconSearch: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <circle cx="11" cy="11" r="7" />
    <path d="M20 20l-3.5-3.5" />
  </svg>
)

export const IconX: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M7 7l10 10M17 7L7 17" />
  </svg>
)

export const IconMore: React.FC<IconProps> = ({ size = 24, className }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <circle cx="12" cy="5" r="1.75" />
    <circle cx="12" cy="12" r="1.75" />
    <circle cx="12" cy="19" r="1.75" />
  </svg>
)

export const IconCalendar: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <rect x="3.5" y="5" width="17" height="15.5" rx="3.5" />
    <path d="M8 3v3.5M16 3v3.5" />
    {/* 日期格：放正位置，不再悬在左上角 */}
    <rect x="7.5" y="12.5" width="3.6" height="3.6" rx="1.1"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 1 : 0.22} stroke="none" />
    <rect x="13" y="12.5" width="3.6" height="3.6" rx="1.1"
      fill="currentColor" fillOpacity="0.12" stroke="none" />
  </svg>
)

export const IconSort: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    {/* 三条递减横线：表达"排序"本身 */}
    <path d="M4 6.5h10M4 12h7M4 17.5h4" />
    {/* 下箭头：与横线右端对齐，长度递减与箭头方向一致 */}
    <path d="M18 6.5v10.5M15 14.5l3 3 3-3" strokeOpacity="0.8" />
    {/* 蓝色实心点置于箭头起点，作为视觉锚点 */}
    <circle cx="18" cy="6" r="1.1"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 1 : 0.35} stroke="none" />
  </svg>
)

export const IconFolder: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M3.5 7.5a2.5 2.5 0 012.5-2.5h3.2l2 2.5h7.8a2.5 2.5 0 012.5 2.5v7.5a2.5 2.5 0 01-2.5 2.5H6a2.5 2.5 0 01-2.5-2.5V7.5z" />
    <rect x="7" y="11.5" width="10" height="2" rx="1"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 0.9 : 0.15} stroke="none" />
  </svg>
)

export const IconSettings: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <circle cx="12" cy="12" r="3.2" />
    <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 8a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06A1.65 1.65 0 009 3.68 1.65 1.65 0 0010 2.17V2a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V8a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z" />
    <circle cx="12" cy="12" r="1.2"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 1 : 0.2} stroke="none" />
  </svg>
)

export const IconCompass: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <path d="M15.5 8.5l-2 5-5 2 2-5 5-2z"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 0.85 : 0.18} />
  </svg>
)

export const IconNote: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M5 4.5h9.5L19 9v10.5a2 2 0 01-2 2H5a2 2 0 01-2-2v-13a2 2 0 012-2z" />
    <path d="M14 4.5V9h4.5" />
    {/* 内容行：灰线为主，蓝点作焦点（统一规则：蓝仅用于实心小元素） */}
    <path d="M10 13h4M10 16.5h2.5" strokeOpacity="0.5" />
    <circle cx="7.8" cy="13" r="1"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 1 : 0.35} stroke="none" />
  </svg>
)

export const IconTodo: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <rect x="3.5" y="4.5" width="17" height="15" rx="3.5" />
    <path d="M7.5 12.5l2.5 2.5 5-5.5"
      stroke={accent ? 'var(--accent)' : 'currentColor'} strokeOpacity={accent ? 1 : 0.55} />
  </svg>
)

export const IconChevronDown: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M6 9.5l6 6 6-6" />
  </svg>
)

export const IconChevronUp: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M6 14.5l6-6 6 6" />
  </svg>
)

export const IconChevronLeft: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M14.5 6l-6 6 6 6" />
  </svg>
)

export const IconChevronRight: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M9.5 6l6 6-6 6" />
  </svg>
)

export const IconTrash: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l.8 12.2A2 2 0 009.3 21h5.4a2 2 0 002-1.8L17.5 7" />
    <path d="M10 11v6M14 11v6"
      stroke={accent ? 'var(--accent)' : 'currentColor'} strokeOpacity={accent ? 1 : 0.4} />
  </svg>
)

export const IconBell: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M18 8.5a6 6 0 10-12 0c0 5-2 6.5-2 6.5h16s-2-1.5-2-6.5z" />
    <path d="M10 19a2.2 2.2 0 004 0" />
    <circle cx="12" cy="3.5" r="1.3"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 1 : 0.5} stroke="none" />
  </svg>
)

export const IconGlobe: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a15 15 0 010 18M12 3a15 15 0 000 18" />
    <circle cx="12" cy="12" r="2"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 0.9 : 0.15} stroke="none" />
  </svg>
)

export const IconSend: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M21 3L10.5 13.5M21 3l-6.8 18-3.7-7.5L3 9.8 21 3z" />
  </svg>
)

export const IconSparkles: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <path d="M12 3.5l1.9 4.9 4.9 1.9-4.9 1.9L12 17.1l-1.9-4.9L5.2 10.3l4.9-1.9L12 3.5z"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 0.85 : 0.18} />
    {/* 小星：靠近主星右下，实心填充统一风格 */}
    <path d="M18 14.6l.9 2.3 2.3.9-2.3.9-.9 2.3-.9-2.3-2.3-.9 2.3-.9.9-2.3z"
      fill={accent ? 'var(--accent)' : 'currentColor'}
      fillOpacity={accent ? 0.85 : 0.3}
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 0.85 : 0.3} />
  </svg>
)

export const IconBook: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    {/* 书脊：左侧明显加粗的一条竖线 + 内页细线，双线形成"书"的辨识 */}
    <path d="M5.5 3v18" strokeWidth={strokeWidth ? strokeWidth * 1.15 : 2.3} />
    <path d="M8 3.2v17.6" strokeOpacity="0.5" />
    {/* 封面轮廓：右侧顶到 19.5，上下圆角 */}
    <path d="M8 3.2h9.5a1.5 1.5 0 011.5 1.5v14.6a1.5 1.5 0 01-1.5 1.5H8" />
    {/* 书页厚度：底部向右延伸一小段，表达"多页" */}
    <path d="M8 20.8h11" strokeOpacity="0.35" />
    {/* 蓝色书签：小实心，统一规则 */}
    <path d="M15 3.2v4.8l-1.6-1.2-1.6 1.2V3.2"
      fill={accent ? 'var(--accent)' : 'currentColor'}
      fillOpacity={accent ? 1 : 0.4}
      stroke="none" />
  </svg>
)

export const IconToolbox: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth ?? 2} strokeLinecap="round" strokeLinejoin="round"
    className={className} style={{ flexShrink: 0, display: 'block' }} aria-hidden="true">
    <rect x="3" y="7.5" width="18" height="13" rx="3.5" />
    <path d="M8.5 7.5V6a2 2 0 012-2h3a2 2 0 012 2v1.5" />
    {/* 分隔线用灰，锁扣用小实心方块（与其他图标的蓝色规则一致） */}
    <path d="M3 13.5h18" strokeOpacity="0.45" />
    <rect x="10.4" y="11.9" width="3.2" height="3.2" rx="1"
      fill={accent ? 'var(--accent)' : 'currentColor'} fillOpacity={accent ? 1 : 0.4} stroke="none" />
  </svg>
)
