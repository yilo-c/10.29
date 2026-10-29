export interface LunarDayInfo {
  /** 农历年月日，如 "二〇二六年三月廿九" */
  fullDate: string
  /** 农历月，如 "三月" */
  month: string
  /** 农历日，如 "廿九" */
  day: string
  /** 闰月标记 */
  isLeap: boolean
}

export interface GanZhiInfo {
  /** 年柱，如 "丙午" */
  year: string
  /** 月柱，如 "癸巳" */
  month: string
  /** 日柱，如 "己丑" */
  day: string
  /** 时柱，如 "甲子" */
  time: string
}

export interface ShenXiaoInfo {
  /** 生肖年，如 "马" */
  year: string
  /** 生辰肖对应的地支，如 "午" */
  yearZhi: string
}

export interface YiJiInfo {
  /** 宜做事项列表 */
  yi: string[]
  /** 忌做事项列表 */
  ji: string[]
}

export interface ChongShaInfo {
  /** 冲，如 "羊" */
  chong: string
  /** 煞，如 "东" */
  sha: string
}

export interface FangWeiInfo {
  /** 喜神方位 */
  xi: string
  /** 财神方位 */
  cai: string
  /** 福神方位 */
  fu: string
}

export interface XiuInfo {
  /** 二十八宿名称，如 "娄" */
  name: string
  /** 宿对应的动物，如 "狗" */
  animal: string
  /** 吉凶，如 "吉" */
  luck: string
  /** 所属宫，如 "西方白虎" */
  gong: string
}

export interface HuangliData {
  /** 公历日期字符串 */
  solarDate: string
  /** 农历信息 */
  lunar: LunarDayInfo
  /** 天干地支 */
  ganzhi: GanZhiInfo
  /** 生肖 */
  shengxiao: ShenXiaoInfo
  /** 宜忌 */
  yiJi: YiJiInfo
  /** 冲煞 */
  chongSha: ChongShaInfo
  /** 方位 */
  fangwei: FangWeiInfo
  /** 二十八宿 */
  xiu: XiuInfo
  /** 建除十二神 */
  jianChu: string
  /** 建除吉凶 */
  jianChuLuck: string
  /** 彭祖百忌 */
  pengZu: string
  /** 纳音 */
  naYin: string
  /** 节气（如当日是节气） */
  jieQi: string
  /** 农历节日列表 */
  festivals: string[]
  /** 其他节日列表 */
  otherFestivals: string[]
  /** 八字 */
  bazi: string
  /** 日干 */
  dayGan: string
  /** 日支 */
  dayZhi: string
  /** 星期 */
  weekDay: string
}
