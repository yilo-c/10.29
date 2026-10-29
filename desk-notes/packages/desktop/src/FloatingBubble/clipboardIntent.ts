/** 剪贴板文本意图识别 — 纯客户端规则，无需后端。 */

type Intent = 'translate' | 'summarize' | 'polish' | 'save_note' | 'add_word' | 'word_count' | null

/** 判断文本的主要意图，返回建议的 action ID */
export function classifyClipboardIntent(text: string): Intent {
  if (!text || !text.trim()) return null

  const trimmed = text.trim()
  const wordCount = trimmed.split(/[\s\u3000]+/).filter(Boolean).length
  const charCount = trimmed.length
  const cjkChars = (trimmed.match(/[一-鿿㐀-䶿]/g) || []).length
  const nonCjkRatio = cjkChars === 0 ? 1 : (charCount - cjkChars) / charCount
  const isEnglish = nonCjkRatio > 0.7 && cjkChars < 5

  // 1. 纯数字 — 字数统计
  if (/^\d+[\s\S]*$/.test(trimmed) && charCount < 20) return 'word_count'

  // 2. URL — 不操作 (已有独立 "学习此链接" 按钮)
  if (/^https?:\/\/[^\s]+/.test(trimmed)) return null

  // 3. 极短英文单词 → 加入单词库
  if (charCount <= 8 && isEnglish && wordCount <= 2) return 'add_word'

  // 4. 包含任务关键词 → 保存笔记
  const taskKeywords = [
    '待办', 'todo', '完成', '需要', '记得', '必须', '应该',
    '要做', '计划', '安排', '提醒', 'check', 'remember', 'remind', "don't forget",
    '买', '交', '联系', '预约', '参加',
  ]
  const hasTaskKeywords = taskKeywords.some(kw => trimmed.toLowerCase().includes(kw))
  if (hasTaskKeywords && wordCount < 30) return 'save_note'

  // 5. 英文为主 → 翻译
  if (isEnglish && (wordCount >= 3 || charCount > 20)) return 'translate'

  // 6. 中文/混合文本 — 用字符数判断长度
  if (cjkChars > 0) {
    if (charCount >= 30) return 'summarize'   // 长中文文本 → 总结
    if (charCount >= 10) return 'polish'      // 中等长度 → 润色
    return 'save_note'                        // 短文本 → 保存笔记
  }

  // 7. 英文：中等长度 → 润色，长文本 → 总结
  if (wordCount >= 3 && wordCount <= 20) return 'polish'
  if (wordCount > 20) return 'summarize'

  // 8. 默认 → 保存笔记
  return 'save_note'
}
