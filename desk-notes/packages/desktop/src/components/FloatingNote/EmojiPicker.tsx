import React from 'react'

const EMOJIS = [
  '😀', '😃', '😄', '😁', '😅', '😂', '🤣', '😊',
  '😇', '🙂', '😉', '😌', '😍', '🥰', '😘', '😗',
  '😋', '😛', '😜', '🤪', '😝', '🤑', '🤗', '🤭',
  '🤔', '🤐', '😐', '😑', '😶', '😏', '😒', '🙄',
  '😬', '😮', '😯', '😲', '😳', '🥺', '😢', '😭',
  '😤', '😡', '🤬', '😈', '👿', '💀', '☠️', '💩',
  '🤡', '👹', '👺', '👻', '👽', '🤖', '👍', '👎',
  '👊', '✊', '🤛', '🤜', '👏', '🙌', '👐', '🤲',
  '🤝', '🙏', '✌️', '🤟', '🤘', '👌', '✋', '💪',
  '🔥', '⭐', '✨', '💡', '📝', '📌', '🔖', '❤️',
  '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '💔',
  '🎉', '🎊', '🎈', '🎁', '🏆', '🥇', '⭐', '🌟',
  '☀️', '🌈', '⛅', '🌙', '💻', '📱', '⌨️', '🖥️',
  '📁', '📂', '🗂️', '📎', '✂️', '🔗', '📅', '📆',
  '🕐', '⏰', '✅', '❌', '❓', '❗', '⚠️', '🚀',
  '📈', '📊', '💯', '🔍', '🔒', '🔓', '🎯', '🧠',
]

interface Props {
  onSelect: (emoji: string) => void
  onClose: () => void
}

const EmojiPicker: React.FC<Props> = ({ onSelect, onClose }) => {
  return (
    <div
      className="absolute top-full left-0 mt-1 z-50 border border-white/[0.08] rounded-xl p-2 shadow-2xl"
      style={{ background: 'var(--panel-bg-solid)', width: '208px' }}
      onMouseDown={e => e.preventDefault()}
    >
      <div className="flex flex-wrap gap-0.5 max-h-40 overflow-y-auto">
        {EMOJIS.map((emoji, i) => (
          <button
            key={i}
            onClick={() => { onSelect(emoji); onClose() }}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-sm hover:bg-white/[0.08] transition-colors"
          >
            {emoji}
          </button>
        ))}
      </div>
    </div>
  )
}

export default EmojiPicker
