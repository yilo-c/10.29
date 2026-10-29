declare module 'lunar-javascript' {
  export class Solar {
    static fromDate(date: Date): Solar
    getLunar(): Lunar
    toYmd(): string
    getWeekInChinese(): string
  }
  export class Lunar {
    toFullString(): string
    getMonthInChinese(): string
    getDayInChinese(): string
    getYearInGanZhi(): string
    getMonthInGanZhi(): string
    getDayInGanZhi(): string
    getYearShengXiao(): string
    getDayYi(): string[]
    getDayJi(): string[]
    getDayChong(): string
    getDaySha(): string
    getDayPositionXiDesc(): string
    getDayPositionCaiDesc(): string
    getDayPositionFuDesc(): string
    getXiu(): string
    getAnimal(): string
    getXiuLuck(): string
    getGong(): string
    getJieQi(): string
    getDayJianChu(): string
    getDayJianChuLuck(): string
    getPengZuGan(): string
    getPengZuZhi(): string
    getDayNaYin(): string
    getFestivals(): string[]
    getOtherFestivals(): string[]
    getEightChar(): EightChar
  }
  export class EightChar {
    getTime(): string
    getYear(): string
    getDayGan(): string
    getDayZhi(): string
    toString(): string
  }
}
