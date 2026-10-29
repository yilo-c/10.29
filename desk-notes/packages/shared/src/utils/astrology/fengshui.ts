/**
 * 轻量风水建议 — 根据 Qimen 分析结果为各领域提供阳宅布局参考
 * 路径A：基于 domain + 方位 + 时辰的映射表，不涉及完整玄空飞星计算
 */

export interface FengshuiAdvice {
  /** 办公/书桌朝向建议 */
  deskOrientation: string
  /** 座位靠山建议 */
  seatPlacement: string
  /** 催旺物品/颜色 */
  enhancement: string
  /** 避忌 */
  avoid: string
  /** 额外提示（如植物、灯光等） */
  extra?: string
}

const domainAdvice: Record<string, FengshuiAdvice> = {
  career: {
    deskOrientation: '办公桌朝吉方，背后靠实墙，左高右低（左青龙右白虎）',
    seatPlacement: '座位后方不宜靠窗或走道，宜靠实体墙增强贵人运',
    enhancement: '桌面左方放置水晶柱或金属摆件，催旺事业运',
    avoid: '忌座位背对门，忌横梁压顶，忌面对尖角',
    extra: '可在办公位西北方放置白色陶瓷器皿，增强领导力',
  },
  wealth: {
    deskOrientation: '收银台/办公桌朝吉方，明堂开阔，前方不宜有遮挡',
    seatPlacement: '座位宜在财位（进门对角线位置），背后靠实体墙',
    enhancement: '财位放置绿色植物（富贵竹、发财树）或紫水晶洞',
    avoid: '忌财位被压（放重物），忌财位凌乱，忌水龙头直冲',
    extra: '可在正南位置放黄色水晶或金色摆件，增强偏财运',
  },
  relationship: {
    deskOrientation: '卧室/约会场所宜选房屋东南方，床朝吉方',
    seatPlacement: '卧室床头靠实墙，双方位置对称，不压梁',
    enhancement: '卧室使用暖色调灯光，放置粉色水晶或成双摆件',
    avoid: '忌卧室有镜子对床，忌床头靠窗，忌横梁压床头',
    extra: '客厅正南放置鲜花（红色/粉色），增强桃花能量',
  },
  travel: {
    deskOrientation: '出行前面向吉方启程，车上挂平安符',
    seatPlacement: '长途旅行座位选靠窗或过道，忌最后一排',
    enhancement: '随身携带黄色或白色物品（钱包、围巾），增强出行平安',
    avoid: '忌向凶方出发，忌在日破/月破日出行',
    extra: '可在行李箱挂红色小挂件，辟邪保平安',
  },
  study: {
    deskOrientation: '书桌朝吉方，面向明亮开敞方向，背靠实体墙',
    seatPlacement: '座位在文昌位（房屋东南），背后靠墙不宜背对门窗',
    enhancement: '书桌左方放置文昌塔或四支毛笔，增强文运',
    avoid: '忌书桌背门（无人守护感），忌头顶横梁，忌座位在厕所门口',
    extra: '可在书桌上方安装暖色台灯，增强专注力',
  },
  life: {
    deskOrientation: '客厅沙发宜在房屋吉方，呈U形围合布局',
    seatPlacement: '主人位置宜背靠实墙，面向入口（有守势）',
    enhancement: '客厅放置圆形茶几，象征圆满和谐',
    avoid: '忌客厅杂乱不通风，忌大门直对后门（穿堂煞）',
    extra: '可在玄关放置绿植或屏风，化解门冲煞',
  },
  other: {
    deskOrientation: '选择空间方正的位置，避免不规则角落',
    seatPlacement: '座位背后宜靠墙，上方不宜有横梁',
    enhancement: '根据目的选择催旺物品（事业发展→金属，感情→粉色）',
    avoid: '忌在厕所门口、楼梯口长期停留工作',
    extra: '保持空间整洁通风，光线充足为吉',
  },
}

/** 根据 domain 和 lucky direction 获取风水建议 */
export function getFengshuiAdvice(domain: string, luckyDirection: string): FengshuiAdvice {
  const base = domainAdvice[domain] || domainAdvice.other
  // 将方位嵌入建议文本
  const advice: FengshuiAdvice = {
    deskOrientation: base.deskOrientation.replace('吉方', luckyDirection),
    seatPlacement: base.seatPlacement,
    enhancement: base.enhancement,
    avoid: base.avoid,
    extra: base.extra,
  }
  return advice
}

/** 所有领域占位相同，返回补充说明 */
export function getAdditionalTips(luckyDirection: string): string[] {
  const tips: Record<string, string[]> = {
    '正东': ['正东属木，宜用绿色/青色增强', '可放置植物催旺生气'],
    '正南': ['正南属火，宜用红色/紫色增强', '保持明亮，忌杂乱'],
    '正西': ['正西属金，宜用白色/金色增强', '可放置金属摆件'],
    '正北': ['正北属水，宜用蓝色/黑色增强', '可放置鱼缸或水景'],
    '东南': ['东南属木，文昌位所在，利学业事业', '宜放置绿色植物'],
    '西南': ['西南属土，女主人的方位，宜稳定', '宜用黄色/棕色增强'],
    '西北': ['西北属金，男主人/领导位', '宜保持庄重整洁'],
    '东北': ['东北属土，旺丁位，利人口', '宜用暖色调增强'],
  }
  return tips[luckyDirection] || ['保持空间整洁通风，光线充足为吉']
}
