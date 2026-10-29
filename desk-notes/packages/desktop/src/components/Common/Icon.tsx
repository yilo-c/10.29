import React from 'react'
import {
  AlignCenter, AlignJustify, AlignLeft, AlignRight, AlertCircle, AlertTriangle, AppWindow, Archive, ArrowDown, ArrowLeft,
  ArrowLeftRight, ArrowRight, ArrowUp, ArrowUpDown, BarChart3, Bell, Bold, Book, BookOpen,
  Bookmark, Bot, Brain, Briefcase, Building2, Calculator, Calendar, CalendarCheck,
  CalendarDays, CalendarPlus, Check, CheckCircle, CheckSquare, ChevronDown, ChevronLeft,
  ChevronRight, ChevronUp, Circle, ClipboardList, ClipboardPaste, Clock, Cloud, Code,
  Columns3, Coins, Compass, Contrast, Copy, Cpu, Database, Delete, Download, DownloadCloud,
  Eraser, ExternalLink, Eye, EyeOff, Feather, FileDown, FileInput, FilePen, FileText, Flag, FlaskConical, Folder,
  FolderMinus, FolderOpen, Gauge, Gavel, Gem, GitBranch, Globe, Globe2, GraduationCap,
  Grip, GripVertical, HardHat, Heading, HelpCircle, History, Home, Inbox, Info, Italic, Key, Keyboard,
  Landmark, Languages, Layers, LayoutGrid, Lightbulb, LineChart, Link2, Link2Off, List,
  ListChecks, ListOrdered, Loader2, Lock, LogIn, Map, MapPin, Maximize, Maximize2, Menu, MessageCircle,
  MessageSquare, Microscope, Minimize2, Minus, MinusCircle, MoreHorizontal, MoreVertical,
  Navigation, Network, Newspaper, Palette, Paperclip, Pause, Pen, PenLine, PenSquare,
  Pin, Play, Plug, Plus, Quote, Rocket, RotateCcw, RotateCw, Rss, Save, Search, Send,
  Settings, Share2, Shield, Sliders, Smile, Sparkles, Sprout, Square, Star, StickyNote, Sun,
  Table, Tag, Tags, Terminal, ThumbsDown, ThumbsUp, Timeline, FolderTree, TrendingUp, Trash, Trash2, TreePine, Underline, Upload, UploadCloud,
  Users, Video, Volume2, Wand2, Wifi, Workflow, Wrench, X, XCircle, Zap,
  type LucideIcon,
} from 'lucide-react'

type IconCmp = LucideIcon

/** FontAwesome 图标名 → lucide 组件映射（P3c 迁移单一事实来源） */
const FA_MAP: Record<string, IconCmp> = {
  'fa-xmark': X,
  'fa-check': Check,
  'fa-plus': Plus,
  'fa-note-sticky': StickyNote,
  'fa-trash-can': Trash2,
  'fa-spinner': Loader2,
  'fa-language': Languages,
  'fa-inbox': Inbox,
  'fa-robot': Bot,
  'fa-newspaper': Newspaper,
  'fa-magnifying-glass': Search,
  'fa-square-check': CheckSquare,
  'fa-comment-dots': MessageCircle,
  'fa-box-archive': Archive,
  'fa-rotate': RotateCw,
  'fa-wand-magic-sparkles': Wand2,
  'fa-compass': Compass,
  'fa-pen': Pen,
  'fa-link': Link2,
  'fa-clock': Clock,
  'fa-circle-check': CheckCircle,
  'fa-file-lines': FileText,
  'fa-circle': Circle,
  'fa-download': Download,
  'fa-triangle-exclamation': AlertTriangle,
  'fa-bookmark': Bookmark,
  'fa-folder': Folder,
  'fa-circle-exclamation': AlertCircle,
  'fa-lightbulb': Lightbulb,
  'fa-arrow-up': ArrowUp,
  'fa-circle-notch': Loader2,
  'fa-brain': Brain,
  'fa-globe': Globe,
  'fa-rotate-left': RotateCcw,
  'fa-thumbtack': Pin,
  'fa-chevron-right': ChevronRight,
  'fa-lock': Lock,
  'fa-gear': Settings,
  'fa-code': Code,
  'fa-list-check': ListChecks,
  'fa-layer-group': Layers,
  'fa-plug': Plug,
  'fa-bolt': Zap,
  'fa-chart-simple': BarChart3,
  'fa-calendar': Calendar,
  'fa-circle-half-stroke': Contrast,
  'fa-clock-rotate-left': History,
  'fa-chevron-left': ChevronLeft,
  'fa-window-minimize': Minus,
  'fa-check-square': CheckSquare,
  'fa-diagram-project': Workflow,
  'fa-location-arrow': Navigation,
  'fa-chevron-down': ChevronDown,
  'fa-arrow-up-right-from-square': ExternalLink,
  'fa-book': Book,
  'fa-cloud-arrow-down': DownloadCloud,
  'fa-graduation-cap': GraduationCap,
  'fa-star': Star,
  'fa-rotate-right': RotateCw,
  'fa-external-link-alt': ExternalLink,
  'fa-flag': Flag,
  'fa-key': Key,
  'fa-folder-minus': FolderMinus,
  'fa-broom': Sparkles,
  'fa-floppy-disk': Save,
  'fa-seedling': Sprout,
  'fa-grip-lines': Grip,
  'fa-sun': Sun,
  'fa-pen-to-square': PenSquare,
  'fa-gears': Settings,
  'fa-stop': Square,
  'fa-square-root-variable': Calculator,
  'fa-bell': Bell,
  'fa-comment': MessageCircle,
  'fa-database': Database,
  'fa-toolbox': Wrench,
  'fa-tree': TreePine,
  'fa-paper-plane': Send,
  'fa-folder-open': FolderOpen,
  'fa-markdown': FileText,
  'fa-cloud-arrow-up': UploadCloud,
  'fa-gauge-high': Gauge,
  'fa-trash': Trash,
  'fa-check-circle': CheckCircle,
  'fa-arrows-left-right': ArrowLeftRight,
  'fa-calendar-days': CalendarDays,
  'fa-paste': ClipboardPaste,
  'fa-microchip': Cpu,
  'fa-arrow-left': ArrowLeft,
  'fa-circle-question': HelpCircle,
  'fa-copy': Copy,
  'fa-shield-halved': Shield,
  'fa-table-cells-large': LayoutGrid,
  'fa-chevron-up': ChevronUp,
  'fa-sliders': Sliders,
  'fa-search': Search,
  'fa-terminal': Terminal,
  'fa-landmark': Landmark,
  'fa-vimeo': Video,
  'fa-wifi': Wifi,
  'fa-pause': Pause,
  'fa-keyboard': Keyboard,
  'fa-delete-left': Delete,
  'fa-link-slash': Link2Off,
  'fa-window-restore': AppWindow,
  'fa-message': MessageSquare,
  'fa-square-rss': Rss,
  'fa-compress': Minimize2,
  'fa-exclamation-triangle': AlertTriangle,
  'fa-hacker-news': TrendingUp,
  'fa-helmet-safety': HardHat,
  'fa-ellipsis-vertical': MoreVertical,
  'fa-calendar-check': CalendarCheck,
  'fa-flask': FlaskConical,
  'fa-ellipsis': MoreHorizontal,
  'fa-eraser': Eraser,
  'fa-users': Users,
  'fa-circle-nodes': Network,
  'fa-dev': Code,
  'fa-feather': Feather,
  'fa-code-branch': GitBranch,
  'fa-volume-high': Volume2,
  'fa-play': Play,
  'fa-wand-magic': Wand2,
  'fa-arrow-right': ArrowRight,
  'fa-quote-right': Quote,
  'fa-tags': Tags,
  'fa-arrow-right-arrow-left': ArrowLeftRight,
  'fa-github': Code,
  'fa-bars-staggered': AlignJustify,
  'fa-calendar-plus': CalendarPlus,
  'fa-pen-fancy': PenLine,
  'fa-map': Map,
  'fa-timeline': Timeline,
  'fa-folder-tree': FolderTree,
  'fa-arrows-rotate': RotateCw,
  'fa-arrow-down': ArrowDown,
  'fa-tag': Tag,
  'fa-gem': Gem,
  'fa-table-columns': Columns3,
  'fa-palette': Palette,
  'fa-reddit': MessageSquare,
  'fa-border-all': Table,
  'fa-book-open': BookOpen,
  'fa-wrench': Wrench,
  'fa-house-chimney': Home,
  'fa-rss': Rss,
  'fa-chart-line': LineChart,
  'fa-share-nodes': Share2,
  'fa-earth-asia': Globe2,
  'fa-eye': Eye,
  'fa-columns': Columns3,
  'fa-cloud': Cloud,
  'fa-th-large': LayoutGrid,
  'fa-arrow-right-to-bracket': LogIn,
  'fa-upload': Upload,
  'fa-list-ul': List,
  'fa-question': HelpCircle,
  'fa-quote-left': Quote,
  'fa-briefcase': Briefcase,
  'fa-rectangle-list': ClipboardList,
  'fa-expand': Maximize,
  'fa-grip-vertical': GripVertical,
  'fa-caret-right': ChevronRight,
  'fa-circle-minus': MinusCircle,
  'fa-arrow-up-wide-short': ArrowUpDown,
  'fa-microscope': Microscope,
  'fa-list': List,
  'fa-arrow-rotate-right': RotateCw,
  'fa-location-dot': MapPin,
  'fa-product-hunt': Rocket,
  'fa-info-circle': Info,
  'fa-up-right-and-down-left-from-center': Maximize2,
  'fa-paperclip': Paperclip,
  'fa-file-import': FileInput,
  'fa-bars': Menu,
  'fa-city': Building2,
  'fa-gavel': Gavel,
  'fa-magnifying-glass-arrow-right': Search,
  // 配置数组/动态拼接中出现的图标（P3c 查漏补充）
  'fa-clone': Copy,
  'fa-face-smile': Smile,
  'fa-file-pen': FilePen,
  'fa-eye-slash': EyeOff,
  'fa-shield': Shield,
  'fa-bold': Bold,
  'fa-calculator': Calculator,
  'fa-italic': Italic,
  'fa-list-ol': ListOrdered,
  'fa-circle-xmark': XCircle,
  'fa-file-arrow-down': FileDown,
  'fa-heading': Heading,
  'fa-underline': Underline,
  'fa-coins': Coins,
  'fa-circle-info': Info,
  'fa-minus': Minus,
  'fa-align-center': AlignCenter,
  'fa-align-right': AlignRight,
  'fa-square': Square,
  'fa-align-left': AlignLeft,
  'fa-thumbs-up': ThumbsUp,
  'fa-thumbs-down': ThumbsDown,
}

interface IconProps {
  /** FA 风格类名字符串，如 "fa-solid fa-brain"（兼容既有配置） */
  icon: string
  className?: string
  style?: React.CSSProperties
  title?: string
}

/**
 * 图标适配层（P3c）：接收 FA 类名字符串，渲染对应 lucide 图标。
 * size="1em" 跟随 font-size、stroke=currentColor 跟随文字颜色，与 FA 行为一致。
 * 字符串中的非 fa- 工具类（mr-1 / animate-spin 等）自动剥离并保留到 className。
 */
const FA_PREFIXES = new Set(['fa-solid', 'fa-regular', 'fa-brands'])

const Icon: React.FC<IconProps> = ({ icon, className = '', style, title }) => {
  const parts = icon.split(/\s+/).filter(Boolean)
  const faTokens = parts.filter(p => p.startsWith('fa-'))
  const utilClasses = parts.filter(p => !p.startsWith('fa-'))
  const key = faTokens.find(tk => !FA_PREFIXES.has(tk)) || null
  const Cmp = key ? FA_MAP[key] : undefined
  if (!Cmp) {
    // 未映射时保持原样（迁移期兜底）
    return <i className={`${icon} ${className}`.trim()} style={style} title={title} />
  }
  // FA 的 fa-spin 动画 → Tailwind animate-spin
  if (faTokens.includes('fa-spin') && !utilClasses.includes('animate-spin')) {
    utilClasses.push('animate-spin')
  }
  const finalClass = [...utilClasses, className].filter(Boolean).join(' ')
  return (
    <Cmp
      className={finalClass || undefined}
      style={style}
      size="1em"
      strokeWidth={2}
      aria-hidden
      // 保留 FA 语义键，供测试与样式钩子定位
      {...{ 'data-icon': key } as Record<string, string>}
    />
  )
}

export default Icon
