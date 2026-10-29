import React from 'react'

interface IconProps {
  size?: number
  className?: string
}

const Svg: React.FC<{ children: React.ReactNode; size: number; className?: string; viewBox?: string }> = ({
  children, size, className, viewBox = '0 0 24 24',
}) => (
  <svg
    width={size}
    height={size}
    viewBox={viewBox}
    fill="none"
    stroke="currentColor"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    {children}
  </svg>
)

export const IconCheck: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M20 6L9 17l-5-5" />
  </Svg>
)

export const IconPlus: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
)

export const IconSearch: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <circle cx="11" cy="11" r="8" />
    <path d="M21 21l-4.35-4.35" />
  </Svg>
)

export const IconX: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M18 6L6 18M6 6l12 12" />
  </Svg>
)

export const IconMore: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <circle cx="12" cy="5" r="1" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
  </Svg>
)

export const IconCalendar: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
    <path d="M16 2v4M8 2v4M3 10h18" />
  </Svg>
)

export const IconSort: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M8 6l4-4 4 4" />
    <path d="M8 18l4 4 4-4" />
    <path d="M12 2v20" />
  </Svg>
)

export const IconFolder: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" />
  </Svg>
)

export const IconSettings: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
  </Svg>
)

export const IconCompass: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <circle cx="12" cy="12" r="10" />
    <polygon points="16.24,7.76 14.12,14.12 7.76,16.24 9.88,9.88" />
  </Svg>
)

export const IconNote: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M14.5 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V7.5L14.5 2z" />
    <polyline points="14,2 14,8 20,8" />
    <path d="M8 13h8M8 17h8" />
  </Svg>
)

export const IconTodo: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M9 11l3 3L22 4" />
    <path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />
  </Svg>
)

export const IconChevronDown: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M6 9l6 6 6-6" />
  </Svg>
)

export const IconChevronUp: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M6 15l6-6 6 6" />
  </Svg>
)

export const IconTrash: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
  </Svg>
)

export const IconBell: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
    <path d="M13.73 21a2 2 0 01-3.46 0" />
  </Svg>
)

export const IconChevronLeft: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M15 18l-6-6 6-6" />
  </Svg>
)

export const IconChevronRight: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M9 18l6-6-6-6" />
  </Svg>
)

export const IconGlobe: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
  </Svg>
)

export const IconSend: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M22 2L11 13" />
    <path d="M22 2l-7 20-4-9-9-4 20-7z" />
  </Svg>
)

export const IconSparkles: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z" />
    <path d="M18.5 14.5L16 15l2.5.5.5 2.5.5-2.5L22 15l-2.5-.5L18.5 14.5z" />
    <path d="M6 14.5L3.5 15 6 15.5l.5 2.5.5-2.5L9.5 15 7 14.5 6.5 12 6 14.5z" />
  </Svg>
)

export const IconBook: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </Svg>
)

export const IconToolbox: React.FC<IconProps> = ({ size = 24, className }) => (
  <Svg size={size} className={className}>
    <path d="M7 11v8a2 2 0 002 2h6a2 2 0 002-2v-8" />
    <path d="M5 11h14V7a2 2 0 00-2-2H7a2 2 0 00-2 2v4z" />
    <path d="M12 7V3" />
  </Svg>
)
