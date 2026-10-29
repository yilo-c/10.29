// KnowledgeGraph data constants — node type display labels, color mapping
// and Chinese-to-English type conversion. These are intentionally Chinese
// because the backend knowledge graph stores entity types in Chinese. They
// are data (taxonomy), not UI chrome, so they live outside the i18n scanner.

export const NODE_TYPE_COLORS: Record<string, string> = {
  '项目': '#4fc3f7',
  '概念': '#81c784',
  '人物': '#ff8a65',
  '地点': '#ce93d8',
  '事件': '#ffd54f',
  '组织': '#4db6ac',
  '日期': '#90a4ae',
  '标签': '#a1887f',
  '其他': '#78909c',
  // English aliases for type_display === English fall-through lookups
  project: '#4fc3f7',
  concept: '#81c784',
  person: '#ff8a65',
  location: '#ce93d8',
  event: '#ffd54f',
  organization: '#4db6ac',
  date: '#90a4ae',
  tag: '#a1887f',
}

export const NODE_TYPES = ['项目', '概念', '人物', '地点', '事件', '组织', '标签']

export const CHINESE_TO_TYPE: Record<string, string> = {
  '项目': 'project',
  '概念': 'concept',
  '人物': 'person',
  '地点': 'location',
  '事件': 'event',
  '组织': 'organization',
  '日期': 'date',
  '标签': 'tag',
}

export const DEFAULT_NODE_TYPE = '概念'
export const DEFAULT_EDGE_TYPE = '关联'
export const FALLBACK_COLOR_KEY = '其他'

export function nodeColor(type: string): string {
  // type may be English or Chinese display name; try Chinese-to-English map first
  const en = CHINESE_TO_TYPE[type]
  const colorKey = en || type
  return NODE_TYPE_COLORS[colorKey] || NODE_TYPE_COLORS[FALLBACK_COLOR_KEY]
}

// Build the taxonomy → localized display label map. Kept here (alongside the
// taxonomy data itself) so the i18n scanner does not flag the Chinese keys
// as hardcoded UI strings.
type Translator = (key: string, params?: Record<string, string | number>) => string
export function buildNodeTypeDisplay(t: Translator): Record<string, string> {
  return {
    '项目': t('knowledgeGraph.typeProject'),
    '概念': t('knowledgeGraph.typeConcept'),
    '人物': t('knowledgeGraph.typePerson'),
    '地点': t('knowledgeGraph.typeLocation'),
    '事件': t('knowledgeGraph.typeEvent'),
    '组织': t('knowledgeGraph.typeOrganization'),
    '日期': t('knowledgeGraph.typeDate'),
    '标签': t('knowledgeGraph.typeTag'),
    '其他': t('knowledgeGraph.typeOther'),
  }
}
