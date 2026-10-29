// ── Modern Range/Selection helpers (replaces deprecated execCommand) ──

/** Delete `count` characters before the cursor in a text node. */
function deleteCharsBefore(sel: Selection, count: number): void {
  const range = sel.getRangeAt(0)
  const node = range.startContainer
  if (node.nodeType !== Node.TEXT_NODE) return
  const offset = range.startOffset
  if (offset < count) return
  const text = node.textContent || ''
  node.textContent = text.slice(0, offset - count) + text.slice(offset)
  range.setStart(node, offset - count)
  range.collapse(true)
  sel.removeAllRanges()
  sel.addRange(range)
}

/** Find the nearest block-level ancestor of the cursor within the contentEditable host. */
function findCurrentBlock(sel: Selection): HTMLElement | null {
  const range = sel.getRangeAt(0)
  let node: Node | null = range.startContainer
  while (node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement
      const tag = el.tagName
      if (tag === 'DIV' || tag === 'P' || /^H[1-6]$/.test(tag) || tag === 'BLOCKQUOTE' || tag === 'LI') {
        return el
      }
    }
    if (node.parentElement?.isContentEditable && node.parentElement !== document.body) {
      return node.parentElement
    }
    node = node.parentElement
  }
  return null
}

/** Replace the current block with a new element of the given tag, moving all child nodes. */
function formatAsBlock(sel: Selection, tag: string): void {
  const block = findCurrentBlock(sel)
  if (!block) return

  const newBlock = document.createElement(tag)
  while (block.firstChild) {
    newBlock.appendChild(block.firstChild)
  }
  block.replaceWith(newBlock)

  // Place cursor at start of new block
  const range = document.createRange()
  if (newBlock.firstChild) {
    range.setStart(newBlock.firstChild, 0)
  } else {
    range.setStart(newBlock, 0)
  }
  range.collapse(true)
  sel.removeAllRanges()
  sel.addRange(range)
}

/** Wrap the current block's content in a list (<ul> or <ol>) with a single <li>. */
function insertListBlock(sel: Selection, ordered: boolean): void {
  const block = findCurrentBlock(sel)
  if (!block) return

  const list = document.createElement(ordered ? 'ol' : 'ul')
  const li = document.createElement('li')
  while (block.firstChild) {
    li.appendChild(block.firstChild)
  }
  list.appendChild(li)
  block.replaceWith(list)

  const range = document.createRange()
  if (li.firstChild) {
    range.setStart(li.firstChild, 0)
  } else {
    range.setStart(li, 0)
  }
  range.collapse(true)
  sel.removeAllRanges()
  sel.addRange(range)
}

/** Nest a list item deeper — move it into a sublist of the previous sibling. */
function indentListItem(li: HTMLLIElement): void {
  const prev = li.previousElementSibling
  if (!prev || prev.tagName !== 'LI') return

  const parentList = li.parentElement
  if (!parentList) return

  let sublist = prev.querySelector(':scope > ul, :scope > ol') as HTMLElement | null
  if (!sublist) {
    sublist = document.createElement(parentList.tagName)
    prev.appendChild(sublist)
  }
  sublist.appendChild(li)
}

/** Un-nest a list item — move it up one level. If already at top level, lift out of the list. */
function outdentListItem(li: HTMLLIElement): void {
  const parentList = li.parentElement
  if (!parentList || !['UL', 'OL'].includes(parentList.tagName)) return

  const grandparentLi = parentList.parentElement?.closest('li')
  const listContainer = parentList.parentElement
  if (!listContainer) return

  // Collect items after this one (may be empty)
  const afterItems: HTMLLIElement[] = []
  let next = li.nextElementSibling
  while (next) {
    if (next.tagName === 'LI') afterItems.push(next as HTMLLIElement)
    next = next.nextElementSibling
  }

  if (grandparentLi) {
    // Nested — move li and following siblings after the parent list
    listContainer.insertBefore(li, parentList.nextSibling)
    if (afterItems.length > 0) {
      const newList = document.createElement(parentList.tagName)
      for (const item of afterItems) newList.appendChild(item)
      listContainer.insertBefore(newList, li.nextSibling)
    }
  } else {
    // Top level — lift li out of the list entirely
    listContainer.insertBefore(li, parentList.nextSibling)
    if (afterItems.length > 0) {
      const newList = document.createElement(parentList.tagName)
      for (const item of afterItems) newList.appendChild(item)
      listContainer.insertBefore(newList, li.nextSibling)
    }
  }

  // Remove parent list if it's now empty
  if (parentList.children.length === 0) parentList.remove()
}

// ── Keyboard handler ──

export function handleEditorKeyDown(e: React.KeyboardEvent) {
  if (e.key === 'Tab') {
    const sel = window.getSelection()
    if (sel && sel.rangeCount && sel.anchorNode) {
      const el = sel.anchorNode.nodeType === Node.TEXT_NODE
        ? sel.anchorNode.parentElement
        : sel.anchorNode as HTMLElement
      if (el?.closest?.('li')) {
        e.preventDefault()
        const li = el.closest('li')!
        if (e.shiftKey) {
          outdentListItem(li)
        } else {
          indentListItem(li)
        }
        return
      }
    }
    e.preventDefault()
    // Insert 4 non-breaking spaces
    if (sel && sel.rangeCount) {
      const range = sel.getRangeAt(0)
      range.deleteContents()
      const spaceNode = document.createTextNode('    ')
      range.insertNode(spaceNode)
      range.setStartAfter(spaceNode)
      range.collapse(true)
      sel.removeAllRanges()
      sel.addRange(range)
    }
    return
  }

  // Enter on empty list item: break out of list
  if (e.key === 'Enter' && !e.shiftKey) {
    const sel = window.getSelection()
    if (sel && sel.rangeCount && sel.anchorNode) {
      const el = sel.anchorNode.nodeType === Node.TEXT_NODE
        ? sel.anchorNode.parentElement
        : sel.anchorNode as HTMLElement
      const li = el?.closest?.('li')
      if (li && (!li.textContent || li.textContent.trim() === '')) {
        e.preventDefault()
        outdentListItem(li)
        return
      }
    }
  }

  // Markdown shortcuts on Space
  if (e.key === ' ') {
    const sel = window.getSelection()
    if (!sel || !sel.rangeCount || !sel.anchorNode) return
    const node = sel.anchorNode
    const text = node.textContent || ''
    const lineStart = text.lastIndexOf('\n', sel.anchorOffset) + 1
    const prefix = sel.anchorOffset <= 0 ? '' : text.slice(lineStart, sel.anchorOffset).trim()
    if (!prefix) return

    if (prefix === '#') {
      e.preventDefault()
      deleteCharsBefore(sel, 1)
      formatAsBlock(window.getSelection()!, 'h1')
      return
    }
    if (prefix === '##') {
      e.preventDefault()
      deleteCharsBefore(sel, 2)
      formatAsBlock(window.getSelection()!, 'h2')
      return
    }
    if (prefix === '###') {
      e.preventDefault()
      deleteCharsBefore(sel, 3)
      formatAsBlock(window.getSelection()!, 'h3')
      return
    }
    if (prefix === '>') {
      e.preventDefault()
      deleteCharsBefore(sel, 1)
      formatAsBlock(window.getSelection()!, 'blockquote')
      return
    }
    if (prefix === '-' || prefix === '*') {
      e.preventDefault()
      deleteCharsBefore(sel, 1)
      insertListBlock(window.getSelection()!, false)
      return
    }
    if (/^\d+$/.test(prefix)) {
      e.preventDefault()
      deleteCharsBefore(sel, prefix.length)
      insertListBlock(window.getSelection()!, true)
      return
    }
  }
}
