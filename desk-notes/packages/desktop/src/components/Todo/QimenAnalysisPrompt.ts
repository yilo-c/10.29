// AI prompt template for Qimen analysis in TodoList — these are AI instructions, not user-visible UI.
// Extracted to avoid false positives in i18n CJK scanner.
export function buildQimenAnalysisPrompt(text: string): string {
  return `分析以下待办事项属于奇门遁甲哪个领域，只返回JSON，不要输出其他内容。\n\n待办事项：${text}\n\n领域选项：career(事业工作), wealth(求财投资), relationship(感情人际), travel(出行), study(学业考试), life(日常生活), other(其他)\n\n返回格式：{"domain":"领域","scenario":"具体场景"}`
}