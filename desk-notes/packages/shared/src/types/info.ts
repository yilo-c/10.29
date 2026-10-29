export type InfoCategory = 'ai' | 'dev' | 'tool' | 'internet' | 'domestic' | 'international' | 'local' | 'policy' | 'science' | 'general' | string

export interface RawSourceItem {
  id: string
  title: string
  url: string
  source: 'hackernews' | 'devto' | 'github' | 'reddit' | 'v2ex' | 'juejin' | string
  summary: string
  tags: string[]
  score: number
  publishedAt: number
  title_zh?: string
  summary_zh?: string
  category?: InfoCategory
}

export interface InfoItem {
  id: string
  title: string
  url: string
  source: 'hackernews' | 'devto' | 'github' | 'reddit' | string
  summary: string
  reason: string
  relevanceScore: number
  tags: string[]
  publishedAt: number
  title_zh?: string
  summary_zh?: string
  category?: InfoCategory
}

export const CATEGORY_META: Record<string, { label: string; icon: string; color: string }> = {
  ai: { label: 'AI 信息', icon: 'fa-solid fa-brain', color: '#a855f7' },
  dev: { label: '技术动态', icon: 'fa-solid fa-code', color: '#3b82f6' },
  tool: { label: '工具开源', icon: 'fa-solid fa-wrench', color: '#f59e0b' },
  internet: { label: '互联网资讯', icon: 'fa-solid fa-globe', color: '#06b6d4' },
  domestic: { label: '国内大势', icon: 'fa-solid fa-landmark', color: '#ef4444' },
  international: { label: '国际大势', icon: 'fa-solid fa-earth-asia', color: '#3b82f6' },
  local: { label: '同城', icon: 'fa-solid fa-city', color: '#10b981' },
  policy: { label: '政策', icon: 'fa-solid fa-gavel', color: '#f97316' },
  science: { label: '科技突破', icon: 'fa-solid fa-flask', color: '#10b981' },
  general: { label: '综合', icon: 'fa-solid fa-globe', color: '#6b7280' },
}
