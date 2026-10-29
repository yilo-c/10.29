/**
 * 时辰奇门信息
 * 基于时辰的简化奇门参考，不含完整排盘计算。
 * 仅供 UI 展示参考，不构成命理判断。
 */

interface QimenShiChenInfo {
  star: string
  door: string
  palace: string
  advice: string
}

const QIMEN_SHICHEN_MAP: Record<number, QimenShiChenInfo> = {
  0: { star: '天蓬星', door: '休门', palace: '坎一宫', advice: '宜静养休息，适合规划、思考、冥想类活动。不宜冲动决策。' },
  1: { star: '天芮星', door: '死门', palace: '坤二宫', advice: '宜静不宜动，适合学习、研究、整理。不宜出行、谈判。' },
  2: { star: '天冲星', door: '伤门', palace: '震三宫', advice: '宜果断行动，适合突破困境、启动项目。但需防冲动。' },
  3: { star: '天辅星', door: '杜门', palace: '巽四宫', advice: '宜静不宜动，适合文书策划、文化创作、教育学习类事项。' },
  4: { star: '天英星', door: '景门', palace: '离九宫', advice: '宜展示才华，适合演讲、会议、销售、品牌推广等。' },
  5: { star: '天禽星', door: '死门', palace: '中五宫', advice: '诸事平和，宜按部就班，稳中求进。适合处理日常事务。' },
  6: { star: '天心星', door: '开门', palace: '乾六宫', advice: '百事大吉，宜开张、签约、出行、求医。是行动的好时机。' },
  7: { star: '天柱星', door: '惊门', palace: '兑七宫', advice: '宜守不宜攻，适合沟通、协商、辩论。需防口舌是非。' },
  8: { star: '天任星', door: '生门', palace: '艮八宫', advice: '宜积极主动，适合求财、创业、拓展业务。万事可成。' },
  9: { star: '天英星', door: '景门', palace: '离九宫', advice: '宜展示才华，适合演讲、会议、销售、品牌推广等。' },
  10: { star: '天蓬星', door: '休门', palace: '坎一宫', advice: '宜静养休息，适合规划、思考、冥想类活动。不宜冲动决策。' },
  11: { star: '天芮星', door: '死门', palace: '坤二宫', advice: '宜静不宜动，适合学习、研究、整理。不宜出行、谈判。' },
  // 2-10 are same as above; 11 (23:00) maps back
}

const SHICHEN_MAP = [
  { name: '子', range: '23:00-00:59' },
  { name: '丑', range: '01:00-02:59' },
  { name: '寅', range: '03:00-04:59' },
  { name: '卯', range: '05:00-06:59' },
  { name: '辰', range: '07:00-08:59' },
  { name: '巳', range: '09:00-10:59' },
  { name: '午', range: '11:00-12:59' },
  { name: '未', range: '13:00-14:59' },
  { name: '申', range: '15:00-16:59' },
  { name: '酉', range: '17:00-18:59' },
  { name: '戌', range: '19:00-20:59' },
  { name: '亥', range: '21:00-22:59' },
]

export function getShiChen(hour: number): { name: string; range: string } {
  const idx = hour === 23 ? 0 : Math.floor((hour + 1) / 2)
  return SHICHEN_MAP[idx] || SHICHEN_MAP[0]
}

/**
 * Get simplified Qimen info for a given hour (0-23).
 * Maps each 2-hour shiChen to a star/door/palace.
 */
export function getQimenInfo(hour: number): QimenShiChenInfo | null {
  // 子时 (23:00-00:59) uses key 11 for 23, key 0 for 0
  if (hour === 23) return QIMEN_SHICHEN_MAP[11]
  // Use floor division: 0→0, 1→1, 2→2, etc.
  const idx = Math.floor((hour + 1) / 2)
  return QIMEN_SHICHEN_MAP[idx] || null
}
