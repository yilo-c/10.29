/**
 * 奇门场景关键词建议
 * 根据待办文本关键词自动推断奇门遁甲领域和场景
 */
import type { QimenDomain, QimenEvent } from '../../types'

interface KeywordEntry {
  domain: QimenDomain
  scenario?: string
  keywords: string[]
}

const KEYWORD_MAP: KeywordEntry[] = [
  // 事业
  { domain: 'career', scenario: 'jobInterview', keywords: ['面试', '面試', '求职', '找工作', '应聘'] },
  { domain: 'career', scenario: 'salary', keywords: ['加薪', '涨薪', '谈薪', '谈判', '薪资'] },
  { domain: 'career', scenario: 'signing', keywords: ['签合同', '签约', '签约', '合同'] },
  { domain: 'career', scenario: 'bid', keywords: ['投标', '竞标', '招标', '投标书'] },
  { domain: 'career', scenario: 'promotion', keywords: ['升职', '晋升', '提拔', '升迁', '晋级'] },
  { domain: 'career', scenario: 'startup', keywords: ['创业', '开公司', '注册公司', '合伙'] },
  { domain: 'career', scenario: 'team', keywords: ['团队', '招人', '招聘', '组建团队'] },
  { domain: 'career', keywords: ['工作', '上班', '出差', '汇报', '述职', '绩效', '开会', '会议', '项目', '任务', '客户'] },

  // 求财
  { domain: 'wealth', scenario: 'invest', keywords: ['投资', '理财', '买入', '建仓'] },
  { domain: 'wealth', scenario: 'debt', keywords: ['催款', '收款', '讨债', '债务', '欠款'] },
  { domain: 'wealth', scenario: 'cooperate', keywords: ['合作', '合伙', '合资', '入股'] },
  { domain: 'wealth', scenario: 'stock', keywords: ['股票', '基金', '炒股', '股市', 'A股'] },
  { domain: 'wealth', scenario: 'project', keywords: ['项目款', '回款', '拨款', '融资'] },
  { domain: 'wealth', keywords: ['赚钱', '搞钱', '财运', '副业', '生意', '买卖', '交易'] },

  // 感情
  { domain: 'relationship', scenario: 'date', keywords: ['约会', '相亲', '见面', '奔现'] },
  { domain: 'relationship', scenario: 'matchmake', keywords: ['介绍对象', '找对象', '脱单'] },
  { domain: 'relationship', scenario: 'marry', keywords: ['结婚', '求婚', '订婚', '婚礼', '领证'] },
  { domain: 'relationship', scenario: 'reconcile', keywords: ['和好', '复合', '挽回', '道歉'] },
  { domain: 'relationship', keywords: ['感情', '恋爱', '对象', '女朋友', '男朋友', '伴侣'] },

  // 出行
  { domain: 'travel', scenario: 'travel', keywords: ['旅行', '旅游', '度假', '出游'] },
  { domain: 'travel', scenario: 'move', keywords: ['搬家', '搬迁', '迁居', '入住'] },
  { domain: 'travel', scenario: 'abroad', keywords: ['出国', '签证', '护照', '移民', '留学'] },
  { domain: 'travel', scenario: 'drive', keywords: ['自驾', '开车', '出行', '订机票', '订酒店'] },
  { domain: 'travel', keywords: ['出发', '行程', '旅途', '航班', '高铁'] },

  // 学业
  { domain: 'study', scenario: 'exam', keywords: ['考试', '考研', '高考', '考公', '考编', '考证', '笔试', '复试'] },
  { domain: 'study', scenario: 'schoolInterview', keywords: ['面试', '升学', '入学'] },
  { domain: 'study', scenario: 'school', keywords: ['上学', '报到', '入学', '开学'] },
  { domain: 'study', scenario: 'defense', keywords: ['答辩', '论文', '毕业', '毕业论文'] },
  { domain: 'study', keywords: ['学习', '复习', '备考', '上课', '作业', '考试'] },

  // 生活
  { domain: 'life', scenario: 'find', keywords: ['寻找', '找到', '丢失', '寻物', '招领'] },
  { domain: 'life', scenario: 'lawsuit', keywords: ['诉讼', '起诉', '打官司', '仲裁', '投诉'] },
  { domain: 'life', scenario: 'medical', keywords: ['看病', '体检', '手术', '住院', '复查', '就医'] },
  { domain: 'life', scenario: 'renovate', keywords: ['装修', '翻新', '改造', '维修'] },
  { domain: 'life', scenario: 'meeting', keywords: ['聚会', '聚餐', '约饭', '见面'] },
  { domain: 'life', keywords: ['买', '卖', '租房', '买房', '购物', '办证', '办理'] },
]

/**
 * 根据待办文本推断奇门领域和场景
 * 返回匹配的 QimenEvent，无匹配时返回 null
 *
 * 策略：扫描所有关键词，以"最长匹配关键词"为准
 *       长度相同则取条目中更靠前的
 */
export function suggestQimenEvent(text: string): QimenEvent | null {
  if (!text) return null

  let bestEntry: KeywordEntry | null = null
  let bestLen = 0

  for (const entry of KEYWORD_MAP) {
    for (const kw of entry.keywords) {
      if (text.includes(kw) && kw.length > bestLen) {
        bestEntry = entry
        bestLen = kw.length
      }
    }
  }

  if (!bestEntry) return null

  return {
    domain: bestEntry.domain,
    scenario: bestEntry.scenario,
    description: text,
  }
}

/**
 * 获取领域的中文标签（从 i18n 映射，供非组件环境使用）
 */
export function getDomainLabel(domain: QimenDomain): string {
  const labels: Record<QimenDomain, string> = {
    career: '事业',
    wealth: '求财',
    relationship: '感情',
    travel: '出行',
    study: '学业',
    life: '生活',
    other: '其他',
  }
  return labels[domain] || '其他'
}
