import React from 'react'

/**
 * 图标系统 / Icon system
 *
 * 风格基准：Redmi / 小米笔记（2026-10-30 全面重做）
 *
 * 旧版问题（用户反馈「像老式电脑图标」的根因）：
 *   1. 主线 2px 偏粗，24px 下笔画糊成一团
 *   2. 图标内部塞了「灰线 + 彩色小点 + 半透明填充」三重装饰，
 *      小尺寸时变成脏点，失去轮廓清晰度
 *   3. 齿轮用几何生成的 8 齿路径，齿过密（24px 下发虚）
 *   4. 各图标内部装饰规则不统一（有的圆点 0.35、有的 0.15、有的 0.9）
 *
 * 新版规则（唯一，全部图标遵守）：
 *   - 线宽 1.75px，round cap + round join（圆润，不做直角）
 *   - 只保留「一个轮廓 + 最多一处强调」，不堆叠装饰
 *   - 强调处默认用 currentColor 半透明，accent=true 时才用主题蓝
 *   - 统一 24 视窗，四周留 2px 安全边距（图标在 20px 内成形）
 *   - 能用一笔表达就不画第二笔
 */

interface IconProps {
  size?: number
  className?: string
  /** 是否用主题强调色作为点缀色（默认 false，走 currentColor 淡化） */
  accent?: boolean
  /** 主线粗细，默认 1.75（1.75 在 24px 下最清晰，2px 偏糊） */
  strokeWidth?: number
}

/** 所有图标共用的 svg 属性，保证风格一致 */
const base = (size: number, className?: string) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none' as const,
  stroke: 'currentColor',
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  className,
  style: { flexShrink: 0, display: 'block' as const },
  'aria-hidden': true,
})

/* ── 基础图标 ── */

export const IconCheck: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4.5 12.5l5 5 10-11" />
  </svg>
)

export const IconPlus: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M12 5.5v13M5.5 12h13" />
  </svg>
)

export const IconSearch: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <circle cx="10.8" cy="10.8" r="6.3" />
    <path d="M15.5 15.5l4.2 4.2" />
  </svg>
)

export const IconX: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />
  </svg>
)

export const IconMore: React.FC<IconProps> = ({ size = 24, className }) => (
  <svg {...base(size, className)} strokeWidth={1.75}>
    <circle cx="5.5" cy="12" r="1.35" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.35" fill="currentColor" stroke="none" />
    <circle cx="18.5" cy="12" r="1.35" fill="currentColor" stroke="none" />
  </svg>
)

export const IconCalendar: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <rect x="3.8" y="5.5" width="16.4" height="14.7" rx="3.2" />
    <path d="M8 3.5v3.5M16 3.5v3.5" />
    {/* 单一强调：日期点，不再画复杂网格 */}
    <circle cx="12" cy="14.2" r="2.1"
      fill={accent ? 'var(--accent)' : 'currentColor'}
      fillOpacity={accent ? 1 : 0.22} stroke="none" />
  </svg>
)

export const IconSort: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4.5 7.5h15M4.5 12h10M4.5 16.5h6" />
    {accent && (
      <circle cx="19" cy="16.5" r="2.2" fill="var(--accent)" stroke="none" />
    )}
  </svg>
)

export const IconFolder: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M3.8 7.6a2.6 2.6 0 012.6-2.6h2.9l2 2.4h6.3a2.6 2.6 0 012.6 2.6v7.2a2.6 2.6 0 01-2.6 2.6H6.4a2.6 2.6 0 01-2.6-2.6V7.6z" />
    {/* 强调：单条横线表示「有内容」，比原来的色块更克制 */}
    <path d="M8.4 13.4h7.2"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.35} />
  </svg>
)

export const IconSettings: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    {/* 极简齿轮：外圈圆 + 8 根等距短齿，不再用复杂 path。
        24px 下齿数少、笔画短，比旧版几何生成路径清晰得多。 */}
    <circle cx="12" cy="12" r="6.6" />
    <circle cx="12" cy="12" r="2.4"
      fill={accent ? 'var(--accent)' : 'none'}
      stroke={accent ? 'var(--accent)' : 'currentColor'} />
    <path d="M12 2.9v2.5M12 18.6v2.5M21.1 12h-2.5M5.4 12H2.9M18.4 5.6l-1.8 1.8M7.4 16.6l-1.8 1.8M18.4 18.4l-1.8-1.8M7.4 7.4L5.6 5.6" />
  </svg>
)

export const IconCompass: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M15.2 8.8l-2.4 4-4 2.4 2.4-4 4-2.4z"
      fill={accent ? 'var(--accent)' : 'currentColor'}
      fillOpacity={accent ? 0.95 : 0.18} />
  </svg>
)

export const IconNote: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    {/* 便签本体：右上角折角，一笔成形 */}
    <path d="M5.2 4.8h8.6L18.8 10v9.2a2 2 0 01-2 2H5.2a2 2 0 01-2-2V6.8a2 2 0 012-2z" />
    <path d="M13.6 4.8V10h5.2" />
    {/* 强调：一条内容行，不再塞灰线加圆点 */}
    <path d="M7.4 14.2h6.4"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.38} />
  </svg>
)

export const IconTodo: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <rect x="3.6" y="4.8" width="16.8" height="14.4" rx="3.4" />
    <path d="M7.6 12.4l2.6 2.6 5.6-6"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.55} />
  </svg>
)

export const IconChevronDown: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M6.5 9.5l5.5 5.5 5.5-5.5" />
  </svg>
)

export const IconChevronUp: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M6.5 14.5L12 9l5.5 5.5" />
  </svg>
)

export const IconChevronLeft: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M14.5 6.5L9 12l5.5 5.5" />
  </svg>
)

export const IconChevronRight: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M9.5 6.5L15 12l-5.5 5.5" />
  </svg>
)

export const IconTrash: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4.2 7h15.6M9.6 7V4.8h4.8V7" />
    <path d="M6.4 7l.7 12.1a2 2 0 002 1.9h5.8a2 2 0 002-1.9L17.6 7"
      stroke={accent ? 'var(--accent)' : 'currentColor'} />
    <path d="M10.4 11v6M13.6 11v6"
      stroke="currentColor" strokeOpacity="0.3" />
  </svg>
)

export const IconBell: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M6.4 10.2a5.6 5.6 0 0111.2 0c0 3.3.8 4.8 1.6 5.7.4.5.1 1.3-.5 1.3H5.3c-.6 0-.9-.8-.5-1.3.8-.9 1.6-2.4 1.6-5.7z" />
    <path d="M10.3 19.6a2 2 0 003.4 0"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.45} />
  </svg>
)

export const IconGlobe: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17" strokeOpacity="0.4" />
    <path d="M12 3.5c2.4 2.4 3.6 5.3 3.6 8.5s-1.2 6.1-3.6 8.5c-2.4-2.4-3.6-5.3-3.6-8.5S9.6 5.9 12 3.5z"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.5} />
  </svg>
)

export const IconSend: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4.6 12L20 4.5l-3.6 15.2-4.6-5.4-7.2-2.3z" />
    <path d="M11.8 14.3L20 4.5" strokeOpacity="0.45" />
  </svg>
)

export const IconSparkles: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    {/* AI 火花：一大三小，四角星。小米/系统级 AI 图标的通用语言 */}
    <path d="M12 3.4l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1L5 10.6l5.1-1.9L12 3.4z"
      fill={accent ? 'var(--accent)' : 'currentColor'}
      fillOpacity={accent ? 0.95 : 0.16} />
    <path d="M18.4 15.6l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8.8-2.1z"
      fill={accent ? 'var(--accent)' : 'currentColor'}
      fillOpacity={accent ? 0.7 : 0.3} stroke="none" />
  </svg>
)

export const IconBook: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4.2 5.6a1.6 1.6 0 011.6-1.6h4.7a3.1 3.1 0 013.1 3.1v12.5" />
    <path d="M19.8 5.6a1.6 1.6 0 00-1.6-1.6h-4.7" />
    <path d="M4.2 5.6v12.6a1.6 1.6 0 001.6 1.6h4.7a3.1 3.1 0 013.1 1.4 3.1 3.1 0 013.1-1.4h2.9a1.6 1.6 0 001.6-1.6V5.6" />
    <path d="M12 7.1V20"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.4} />
  </svg>
)

export const IconToolbox: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <rect x="3.6" y="8.2" width="16.8" height="11.4" rx="3.2" />
    <path d="M8.8 8.2V6.4a1.8 1.8 0 011.8-1.8h2.8a1.8 1.8 0 011.8 1.8v1.8" />
    <path d="M3.6 13.4h16.8"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.35} />
  </svg>
)

/* ── 新增：侧边栏/抽屉与编辑类图标（2026-10-30 导航重构所需） ── */

/** 侧边栏（三横线）—— 用于 AI 历史会话抽屉入口 */
export const IconMenu: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4 7h16M4 12h16M4 17h11" />
  </svg>
)

/** 编辑笔 —— 用于笔记编辑入口 */
export const IconEdit: React.FC<IconProps> = ({ size = 24, className, accent, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4.4 19.6l.9-4.3L16.1 4.5a2 2 0 012.8 0l.6.6a2 2 0 010 2.8L8.7 18.7l-4.3.9z" />
    <path d="M14.8 5.9l3.3 3.3"
      stroke={accent ? 'var(--accent)' : 'currentColor'}
      strokeOpacity={accent ? 1 : 0.4} />
  </svg>
)

/** 列表/大纲 —— 编辑器格式栏 */
export const IconList: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M8.5 7h11.5M8.5 12h11.5M8.5 17h11.5" />
    <circle cx="4.6" cy="7" r="1.15" fill="currentColor" stroke="none" />
    <circle cx="4.6" cy="12" r="1.15" fill="currentColor" stroke="none" />
    <circle cx="4.6" cy="17" r="1.15" fill="currentColor" stroke="none" />
  </svg>
)

/** 待办清单 —— 编辑器格式栏 */
export const IconChecklist: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4 7.2l1.7 1.7L8.8 5.8" />
    <path d="M4 16.2l1.7 1.7 3.1-3.1" />
    <path d="M12 7.5h8M12 16.5h8" />
  </svg>
)

/** 加粗 */
export const IconBold: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 2.1}>
    <path d="M7.4 5h6.1a3.4 3.4 0 010 6.8H7.4zM7.4 11.8h6.9a3.6 3.6 0 010 7.2H7.4z" />
  </svg>
)

/** 斜体 */
export const IconItalic: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.9}>
    <path d="M15.5 5h-5M13.5 19h-5M14.2 5L9.8 19" />
  </svg>
)

/** 删除线 */
export const IconStrikethrough: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M4.5 12h15" />
    <path d="M16.2 7.4a4 4 0 00-3.7-1.9c-2.1 0-3.6 1.1-3.6 2.7 0 1.2.8 2 2.4 2.6M7.8 16.6a4.2 4.2 0 003.9 2c2.2 0 3.8-1.2 3.8-2.9" />
  </svg>
)

/** 标题 H1/H2 —— 用文字表达，避免与其他图标混淆 */
export const IconHeading: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.9}>
    <path d="M5.8 6.5v11M12.6 6.5v11M5.8 12h6.8" />
    <path d="M17 11.2l1.9-1.1v7.4" strokeOpacity="0.75" />
  </svg>
)

/** 引用 */
export const IconQuote: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M9.6 7.4C7 8.2 5.6 10.1 5.6 12.7c0 2.1 1.2 3.5 2.9 3.5 1.5 0 2.6-1 2.6-2.5 0-1.4-1-2.4-2.3-2.4-.3 0-.5 0-.7.1.2-1.2 1.1-2.1 2.4-2.6z" />
    <path d="M18.4 7.4c-2.6.8-4 2.7-4 5.3 0 2.1 1.2 3.5 2.9 3.5 1.5 0 2.6-1 2.6-2.5 0-1.4-1-2.4-2.3-2.4-.3 0-.5 0-.7.1.2-1.2 1.1-2.1 2.4-2.6z" />
  </svg>
)

/** 分隔线 */
export const IconDivider: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M3.5 12h17" />
    <path d="M6.5 7.5h11M6.5 16.5h11" strokeOpacity="0.3" />
  </svg>
)

/** 撤销 */
export const IconUndo: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M8.4 8.4L4.5 12l3.9 3.6" />
    <path d="M4.5 12h9.6a5 5 0 010 10h-2.3" />
  </svg>
)

/** 重做 */
export const IconRedo: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <path d="M15.6 8.4L19.5 12l-3.9 3.6" />
    <path d="M19.5 12H9.9a5 5 0 000 10h2.3" />
  </svg>
)

/** 折叠/收起的面板（用于编辑器格式栏收起） */
export const IconChevronDownCircle: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M8.8 10.8l3.2 3.2 3.2-3.2" />
  </svg>
)

/** 图片/附件 */
export const IconImage: React.FC<IconProps> = ({ size = 24, className, strokeWidth }) => (
  <svg {...base(size, className)} strokeWidth={strokeWidth ?? 1.75}>
    <rect x="3.8" y="5.2" width="16.4" height="13.6" rx="3.2" />
    <circle cx="9" cy="10.2" r="1.5" />
    <path d="M4.6 16.8l4.3-4.1a2 2 0 012.7 0l3.1 2.9M13.6 15.1l1.7-1.6a2 2 0 012.7 0l1.4 1.3" />
  </svg>
)
