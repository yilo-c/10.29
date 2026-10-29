import React, { useState } from 'react'
import Icon from './Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { useTranslation } from '../../i18n'

interface InstallPromptProps {
  prompt: BeforeInstallPromptEvent | null
  onClose: () => void
}

const InstallPrompt: React.FC<InstallPromptProps> = ({ prompt, onClose }) => {
  const { t } = useTranslation()
  const [installing, setInstalling] = useState(false)

  const handleInstall = async () => {
    if (!prompt) return
    setInstalling(true)
    await prompt.prompt()
    const result = await prompt.userChoice
    setInstalling(false)
    if (result.outcome === 'accepted') {
      onClose()
    }
  }

  return (
    <AnimatePresence>
      {prompt && (
        <motion.div
          initial={{ y: 80, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 80, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 300, damping: 28 }}
          className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[99999] w-[calc(100%-32px)] max-w-sm"
        >
          <div className="flex items-center gap-3 px-4 py-3 rounded-2xl border border-white/[0.06] shadow-2xl backdrop-blur-xl"
            style={{ background: 'var(--panel-bg-solid)' }}
          >
            <div className="w-9 h-9 rounded-xl bg-white/[0.04] flex items-center justify-center flex-shrink-0">
              <span className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>📌</span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                {t('installPrompt.title')}
              </div>
              <div className="text-2xs mt-0.5 truncate" style={{ color: 'var(--text-muted)' }}>
                {t('installPrompt.description')}
              </div>
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <button
                onClick={onClose}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-xs transition-colors"
                style={{ color: 'var(--text-muted)' }}
              >
                <Icon icon="fa-solid fa-xmark" />
              </button>
              <button
                onClick={handleInstall}
                disabled={installing}
                className="px-3 h-7 rounded-lg text-xs font-medium transition-all hover:brightness-110 active:scale-95 disabled:opacity-50"
                style={{ background: 'var(--fluent-blue, var(--brand))', color: '#fff' }}
              >
                {installing ? (
                  <Icon icon="fa-solid fa-spinner fa-spin" />
                ) : (
                  t('installPrompt.install')
                )}
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export default InstallPrompt
