import React, { useEffect, useState } from 'react'
import Icon from './Icon'

import { onSaveStatusChange, type SaveState } from '../../store/saveTracker'
import { useTranslation } from '@desk-notes/shared/i18n'

const SaveIndicator: React.FC = () => {
  const { t } = useTranslation()
  const [status, setStatus] = useState<SaveState>('idle')

  useEffect(() => onSaveStatusChange(setStatus), [])

  if (status === 'idle') return null

  return (
    <span className="flex items-center gap-1 text-xs transition-opacity">
      {status === 'saving' ? (
        <>
          <Icon icon="fa-solid fa-circle-notch fa-spin text-fluent-blue/50" />
          <span className="text-text-dim">{t('saveIndicator.saving')}</span>
        </>
      ) : (
        <>
          <Icon icon="fa-solid fa-check-circle text-green-400/60" />
          <span className="text-green-400/60">{t('saveIndicator.saved')}</span>
        </>
      )}
    </span>
  )
}

export default SaveIndicator
