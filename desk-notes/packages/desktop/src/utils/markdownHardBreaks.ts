/**
 * 把 markdown 中的软换行转成硬断行（行尾两空格）。
 *
 * 背景：ReactMarkdown(remark-gfm) 默认把单个 \n 渲染成空格——AI 简报
 * "标题/链接 / 点评 / 来源"4 行一条的格式会被挤成一大段（2026-10-15
 * 简报格式根因）。行尾补两空格强制 <br>；围栏代码块内不动；
 * 空行 / 已带硬断行的行不动。
 */
export function hardenSoftBreaks(md: string): string {
  if (!md) return md
  const lines = md.split('\n')
  let inFence = false
  return lines
    .map((line, i) => {
      const isFence = /^\s*(```|~~~)/.test(line)
      if (isFence) inFence = !inFence
      // 围栏行本身与围栏内内容原样保留
      if (isFence || inFence) return line
      const next = lines[i + 1]
      // 末行 / 本行空 / 下一行空（块边界）/ 已是硬断行 → 不动
      if (next === undefined || next.trim() === '' || line.trim() === '' || line.endsWith('  ')) {
        return line
      }
      // 行尾反斜杠也是 markdown 硬断行，不再叠加
      if (line.endsWith('\\')) return line
      return line + '  '
    })
    .join('\n')
}
