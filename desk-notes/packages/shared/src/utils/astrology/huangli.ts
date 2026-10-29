/**
 * 黄历工具 - 封装 lunar-javascript 库
 * 使用 try-catch 包裹所有调用，确保库加载失败时不影响主应用。
 * @ts-ignore CJS 模块在 Vite 中通过预打包转为 ESM default export
 */
import lunar from 'lunar-javascript'
import type { HuangliData, GanZhiInfo, ShenXiaoInfo, YiJiInfo, ChongShaInfo, FangWeiInfo, XiuInfo, LunarDayInfo } from './types'

const { Solar } = lunar

/**
 * 获取指定日期的完整黄历数据
 */
export function getHuangli(date: Date): HuangliData | null {
  try {
    const solar = Solar.fromDate(date)
    const lunar_ = solar.getLunar()
    const bazi = lunar_.getEightChar()

    const lunarInfo: LunarDayInfo = {
      fullDate: lunar_.toFullString().split(' ')[0] || '',
      month: `${lunar_.getMonthInChinese()}月`,
      day: lunar_.getDayInChinese(),
      isLeap: lunar_.getMonthInChinese().startsWith('闰'),
    }

    const ganzhi: GanZhiInfo = {
      year: lunar_.getYearInGanZhi(),
      month: lunar_.getMonthInGanZhi(),
      day: lunar_.getDayInGanZhi(),
      time: bazi.getTime(),
    }

    const shengxiao: ShenXiaoInfo = {
      year: lunar_.getYearShengXiao(),
      yearZhi: bazi.getYear().slice(-1),
    }

    const yiJi: YiJiInfo = {
      yi: lunar_.getDayYi(),
      ji: lunar_.getDayJi(),
    }

    const chong: ChongShaInfo = {
      chong: lunar_.getDayChong(),
      sha: lunar_.getDaySha(),
    }

    const fangwei: FangWeiInfo = {
      xi: lunar_.getDayPositionXiDesc() || '',
      cai: lunar_.getDayPositionCaiDesc() || '',
      fu: lunar_.getDayPositionFuDesc() || '',
    }

    const xiu: XiuInfo = {
      name: lunar_.getXiu(),
      animal: lunar_.getAnimal(),
      luck: lunar_.getXiuLuck(),
      gong: lunar_.getGong(),
    }

    const jieQi = lunar_.getJieQi() || ''
    const weekDay = solar.getWeekInChinese()

    return {
      solarDate: solar.toYmd(),
      lunar: lunarInfo,
      ganzhi,
      shengxiao,
      yiJi,
      chongSha: chong,
      fangwei,
      xiu,
      jianChu: lunar_.getDayJianChu ? lunar_.getDayJianChu() : '',
      jianChuLuck: lunar_.getDayJianChuLuck ? lunar_.getDayJianChuLuck() : '',
      pengZu: lunar_.getPengZuGan ? `${lunar_.getPengZuGan()} ${lunar_.getPengZuZhi()}` : '',
      naYin: lunar_.getDayNaYin(),
      jieQi,
      festivals: lunar_.getFestivals(),
      otherFestivals: lunar_.getOtherFestivals(),
      bazi: bazi.toString(),
      dayGan: bazi.getDayGan(),
      dayZhi: bazi.getDayZhi(),
      weekDay,
    }
  } catch (e: unknown) {
    console.warn('[huangli] getHuangli failed:', e)
    return null
  }
}

/**
 * 获取日期格中显示的简略农历信息
 */
export function getDayCellLunarInfo(date: Date): {
  lunarDay: string
  ganZhiDay: string
  jieQi: string
  festivals: string[]
} | null {
  try {
    const solar = Solar.fromDate(date)
    const lunar_ = solar.getLunar()

    return {
      lunarDay: lunar_.getDayInChinese(),
      ganZhiDay: lunar_.getDayInGanZhi(),
      jieQi: lunar_.getJieQi() || '',
      festivals: [
        ...lunar_.getFestivals(),
        ...lunar_.getOtherFestivals(),
      ],
    }
  } catch (e: unknown) {
    return null
  }
}
