// ── 奇门遁甲数据 ──
// 这里集中存放奇门盘的占断池、领域/场景中文标签、方位/时辰常量。
// 内容本质是占卜规则数据（content data），不是 UI label，因此不参与 i18n 扫描。
// 后续如需做英文版，应在此处增加 ja/en 数据文件或将 pool 改为对象数组。

import type { QimenDomain } from '../../types'

export interface QimenAnalysis {
  dunType: string
  juNumber: number
  luckyDirection: string
  bestTimes: { label: string; range: string }[]
  luckyColors: string[]
  assessment: string
  strategy: string
  palace: string
}

const DIRECTIONS = ['正东', '正南', '正西', '正北', '东南', '西南', '西北', '东北']

const TIME_SLOTS = [
  { label: '子时', range: '23:00-01:00' },
  { label: '丑时', range: '01:00-03:00' },
  { label: '寅时', range: '03:00-05:00' },
  { label: '卯时', range: '05:00-07:00' },
  { label: '辰时', range: '07:00-09:00' },
  { label: '巳时', range: '09:00-11:00' },
  { label: '午时', range: '11:00-13:00' },
  { label: '未时', range: '13:00-15:00' },
  { label: '申时', range: '15:00-17:00' },
  { label: '酉时', range: '17:00-19:00' },
  { label: '戌时', range: '19:00-21:00' },
  { label: '亥时', range: '21:00-23:00' },
]

export const DOMAIN_LABELS: Record<string, string> = {
  career: '事业', wealth: '求财', relationship: '感情',
  travel: '出行', study: '学业', life: '生活', other: '其他',
}

export const SCENARIO_LABELS: Record<string, string> = {
  jobInterview: '面试', salary: '谈薪', signing: '签约',
  bid: '竞标', promotion: '升职', startup: '创业', team: '团队管理',
  invest: '投资', debt: '追债', cooperate: '合作', stock: '股票', project: '项目',
  date: '约会', matchmake: '相亲', marry: '婚姻', reconcile: '和解',
  travel: '远行', move: '搬家', abroad: '出国', drive: '自驾',
  exam: '考试', schoolInterview: '面试', school: '择校', defense: '答辩',
  find: '寻物', lawsuit: '诉讼', medical: '求医', renovate: '装修', meeting: '重要会面',
}

/* ── 模拟奇门分析（后续替换为真实计算） ── */
export function generateAnalysis(domain: QimenDomain, scenario: string | null, _description: string): QimenAnalysis {
  const daySeed = Math.abs(Math.floor(Date.now() / (1000 * 60 * 60 * 24)))

  // 根据 domain + 日期种子生成方位偏移
  const dirIdx = (daySeed + domain.length) % DIRECTIONS.length

  // domain 模板
  const templates: Record<QimenDomain, {
    dun: string; ju: number; palace: string;
    dirOffset: number; timeBlock: number;
    colorSet: string[];
    assessPool: string[];
    strategyPool: string[];
  }> = {
    career: {
      dun: '阳遁', ju: 3 + daySeed % 6, palace: '离宫（火）',
      dirOffset: 0, timeBlock: 3,
      colorSet: ['#1a6bff', '#ffffff', '#e8d5b5'],
      assessPool: [
        '吉 — 开门临宫，朱雀腾飞，主面试顺利，口才出众。',
        '中吉 — 休门到位，贵人运强，适合主动沟通。',
        '大吉 — 生门当值，事业有突破性进展。',
      ],
      strategyPool: [
        '提前到场，面朝吉方就座。沟通时着重展示过往成果。避免背对凶方。',
        '选择吉时出发，携带与吉色相符的配饰。谈判中多用数据说话。',
        '主动出击但留有余地。注意对方在申时后可能提出的附加条件。',
      ],
    },
    wealth: {
      dun: '阳遁', ju: 6 + daySeed % 3, palace: '乾宫（金）',
      dirOffset: 2, timeBlock: 5,
      colorSet: ['#8b5cf6', '#f59e0b', '#ffffff'],
      assessPool: [
        '大吉 — 生门临乾宫，天时地利，求财顺利。',
        '吉 — 休门合财星，正财偏财皆有收获。',
        '中吉 — 开门通畅，但需注意合作方诚信。',
      ],
      strategyPool: [
        '签约安排在吉时，面朝吉方。合同文本用紫色/金色。注意查看金额相关条款。',
        '投资宜谨慎，选择吉方位的项目。避免在煞方进行大额交易。',
        '追债宜在吉时上门，面朝吉方谈判，言辞有力但留余地。',
      ],
    },
    relationship: {
      dun: '阴遁', ju: 2 + daySeed % 7, palace: '震宫（木）',
      dirOffset: 4, timeBlock: 1,
      colorSet: ['#ec4899', '#fef3c7', '#ffffff'],
      assessPool: [
        '吉 — 六合临宫，姻缘和合，沟通顺畅。',
        '中吉 — 太阴相助，宜含蓄表达，不宜急躁。',
        '平和 — 感情之事需顺其自然，今日宜静不宜动。',
      ],
      strategyPool: [
        '选择吉时见面，面朝吉方。穿着柔和色系，避免深色。',
        '约会地点选在吉方位，用餐时坐在有利位置。',
        '沟通中多倾听，少争辩。适合赠送小礼物增进感情。',
      ],
    },
    travel: {
      dun: '阳遁', ju: 9 - daySeed % 3, palace: '坤宫（土）',
      dirOffset: 6, timeBlock: 2,
      colorSet: ['#f59e0b', '#ffffff', '#0ea5e9'],
      assessPool: [
        '吉 — 生门临坤宫，出行顺利，路上有贵人相助。',
        '大吉 — 开门大吉，长途出行平安顺遂。',
        '中吉 — 途中或有小波折，但终将顺利抵达。',
      ],
      strategyPool: [
        '上午出发为佳，向吉方启程。随身携带黄色或白色物品。',
        '检查行李中是否有违禁物品。自驾忌走煞方道路。',
        '预订吉方位的住宿。途中注意保管财物。',
      ],
    },
    study: {
      dun: '阴遁', ju: 4 + daySeed % 5, palace: '坎宫（水）',
      dirOffset: 7, timeBlock: 0,
      colorSet: ['#0ea5e9', '#a78bfa', '#ecfdf5'],
      assessPool: [
        '吉 — 景门临宫，文思泉涌，考试/面试发挥出色。',
        '大吉 — 天辅星当值，学业运势极佳。',
        '中吉 — 虽有压力但能克服，保持平常心。',
      ],
      strategyPool: [
        '考试/面试前在吉方位静心片刻。携带蓝色配饰。',
        '答题时先易后难，把握节奏。面朝吉方就座。',
        '择校/择专业宜咨询吉方方位的意见。',
      ],
    },
    life: {
      dun: '阳遁', ju: 5 + daySeed % 4, palace: '艮宫（土）',
      dirOffset: 5, timeBlock: 4,
      colorSet: ['#059669', '#3b82f6', '#fef3c7'],
      assessPool: [
        '吉 — 休门临宫，平稳顺利，事宜有成。',
        '中吉 — 九地加持，宜守不宜攻。',
        '平和 — 小事可成，大事需再择吉日。',
      ],
      strategyPool: [
        '选择吉时出发，朝吉方行事。穿着绿色或蓝色。',
        '诉讼/维权宜在吉时提交材料，面朝吉方陈述。',
        '求医选择吉时，朝吉方方位前往医院。',
      ],
    },
    other: {
      dun: '阳遁', ju: 1 + daySeed % 8, palace: '中宫（土）',
      dirOffset: 3, timeBlock: 3,
      colorSet: ['#8b5cf6', '#f59e0b', '#ffffff'],
      assessPool: [
        '吉 — 诸事可行，但需注意细节。',
        '中吉 — 顺势而为，不宜强求。',
        '平和 — 今日普通，宜按计划行事。',
      ],
      strategyPool: [
        '选择吉时行事，面朝吉方。穿着吉色衣物。',
        '事宜提前规划，留有缓冲时间。',
        '注意人际关系，避免口舌之争。',
      ],
    },
  }

  const tmpl = templates[domain]
  const luckyDir = DIRECTIONS[(dirIdx + tmpl.dirOffset) % DIRECTIONS.length]

  const t1 = TIME_SLOTS[tmpl.timeBlock % 12]
  const t2 = TIME_SLOTS[(tmpl.timeBlock + 3) % 12]

  const assess = tmpl.assessPool[daySeed % tmpl.assessPool.length]
  const strategy = tmpl.strategyPool[daySeed % tmpl.strategyPool.length] +
    (scenario ? ` 针对「${DOMAIN_LABELS[domain]}·${SCENARIO_LABELS[scenario] || scenario}」事项，以上建议请结合实际情况灵活运用。` : '')

  return {
    dunType: tmpl.dun,
    juNumber: tmpl.ju,
    luckyDirection: luckyDir,
    bestTimes: [t1, t2],
    luckyColors: tmpl.colorSet,
    assessment: assess,
    strategy,
    palace: tmpl.palace,
  }
}

/* ── 评估颜色工具（依赖中文 keyword 命中） ── */
export function assessmentBg(text: string): string {
  if (text.includes('大吉')) return 'rgba(34,197,94,0.08)'
  if (text.includes('吉')) return 'rgba(34,197,94,0.05)'
  if (text.includes('中吉')) return 'rgba(250,204,21,0.06)'
  return 'rgba(239,68,68,0.06)'
}

export function assessmentColor(text: string): string {
  if (text.includes('大吉')) return 'rgb(34,197,94)'
  if (text.includes('吉')) return 'rgb(74,222,128)'
  if (text.includes('中吉')) return 'rgb(250,204,21)'
  return 'rgb(239,68,68)'
}