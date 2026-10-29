declare module 'lunar-javascript' {
  export class Solar {
    static fromDate(date: Date): Solar
    static fromYmd(year: number, month: number, day: number): Solar
    getLunar(): Lunar
    getEightChar(): EightChar
    toFullString(): string
    toYmd(): string
    getWeekInChinese(): string
  }

  export class Lunar {
    static fromDate(date: Date): Lunar
    static fromYmd(year: number, month: number, day: number): Lunar
    toFullString(): string
    getYear(): number
    getMonth(): number
    getDay(): number
    getYearInGanZhi(): string
    getMonthInGanZhi(): string
    getDayInGanZhi(): string
    getYearShengXiao(): string
    getYearShengXiaoByZhi(): string
    getMonthInChinese(): string
    getDayInChinese(): string
    getDayYi(): string[]
    getDayJi(): string[]
    getDayChong(): string
    getDaySha(): string
    getDayXi(): string
    getDayCai(): string
    getDayFu(): string
    getXiu(): string
    getAnimal(): string
    getXiuLuck(): string
    getGong(): string
    getDayJianChu(): string
    getDayJianChuLuck(): string
    getDayPengZu(): string
    getDayNaYin(): string
    getJieQi(): string
    getDayZhiXing(): string
    getFestivals(): string[]
    getOtherFestivals(): string[]
    getTimeZhi(): string
    getTimeGan(): string
  }

  export class EightChar {
    toFullString(): string
    getYear(): string
    getMonth(): string
    getDay(): string
    getTime(): string
    getDayGan(): string
    getDayZhi(): string
  }

  export class LunarTime {
    static fromYmdHms(year: number, month: number, day: number, hour: number, min: number, sec: number): LunarTime
    toFullString(): string
    getYi(): string[]
    getJi(): string[]
  }
}
