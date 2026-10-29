declare module 'lunar-javascript' {
  class Solar {
    static fromDate(date: Date): Solar
    getLunar(): Lunar
    getWeekInChinese(): string
    toYmd(): string
  }

  class Lunar {
    getEightChar(): EightChar
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
    getDayJianChu?(): string
    getDayJianChuLuck?(): string
    getPengZuGan(): string
    getPengZuZhi(): string
    getDayNaYin(): string
    getFestivals(): string[]
    getOtherFestivals(): string[]
    getTimeYmd(): string
    getYearInChinese(): string
  }

  class EightChar {
    getTime(): string
    getYear(): string
    getDayGan(): string
    getDayZhi(): string
  }

  export { Solar, Lunar, EightChar }
  export default { Solar, Lunar, EightChar }
}
