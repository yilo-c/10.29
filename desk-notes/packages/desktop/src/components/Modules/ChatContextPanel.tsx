import React from 'react'
import Icon from '../Common/Icon'
import { useTranslation } from '../../i18n'
import RelatedMemories from './RelatedMemories'
import ChainProgress from './ChainProgress'
import MemoryChips from './MemoryChips'
import SandboxTimeoutHints from '../Common/SandboxTimeoutHints'
import SandboxPreview from '../../FloatingBubble/SandboxPreview'
import { useSandbox } from '../../FloatingBubble/useSandbox'
import { type StudySubject, type StudySubjectInfo } from './chatHelpers'

interface ChatContextPanelProps {
  showHistory: boolean
  studySubject: StudySubject
  onToggleStudySubject: (k: string) => void
  studySubjects: Record<string, StudySubjectInfo>
  backendConnected: boolean
  ollamaOnline: boolean
  lastUserMessage: string
  input: string
  onSelectMemory: (m: string) => void
  sandbox: ReturnType<typeof useSandbox>
  showSandbox: boolean
  onToggleSandbox: () => void
}

export const ChatContextPanel: React.FC<ChatContextPanelProps> = ({
  showHistory, studySubject, onToggleStudySubject, studySubjects,
  backendConnected, ollamaOnline, lastUserMessage, input,
  onSelectMemory, sandbox, showSandbox, onToggleSandbox,
}) => {
  const { t } = useTranslation()

  if (showHistory) return null

  return (
    <>
      {/* Study presets bar */}
      <div className="flex items-center gap-1 px-3 py-1 border-b flex-shrink-0 overflow-x-auto" style={{ borderColor: 'var(--separator)' }}>
        <span className="text-[8px] font-medium uppercase tracking-wider flex-shrink-0 mr-1" style={{ color: 'var(--text-muted)' }}>
          <Icon icon="fa-solid fa-graduation-cap mr-1" />
          {t('exam.aiTutor')}
        </span>
        <button
          onClick={() => onToggleStudySubject('__none__')}
          className={`px-2 py-0.5 rounded text-2xs font-medium transition-all flex-shrink-0 ${
            !studySubject ? 'bg-white/[0.1] text-text-secondary' : 'text-text-dim hover:text-text-secondary'
          }`}
          title={t('exam.aiTutorHint')}
        >
          {t('exam.presetGeneral')}
        </button>
        {(Object.entries(studySubjects) as [string, StudySubjectInfo][])
          .filter(([k]) => !studySubject || k === studySubject)
          .map(([k, v]) => (
          <button
            key={k}
            onClick={() => onToggleStudySubject(k)}
            className={`flex items-center gap-1 px-2 py-0.5 rounded text-2xs font-medium transition-all flex-shrink-0 ${
              studySubject === k ? 'bg-white/[0.1]' : 'text-text-dim hover:text-text-secondary'
            }`}
            style={studySubject === k ? { color: v.color } : undefined}
          >
            <Icon icon={`${v.icon} text-[7px]`} />
            {v.label}
          </button>
        ))}
        {studySubject && (
          <span className="text-[8px] ml-auto flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-arrow-rotate-right mr-0.5" />
            {t('exam.aiTutorHint')}
          </span>
        )}
      </div>

      {/* AI 状态提示 — 后端或本地模型不可用时 */}
      {(!backendConnected || !ollamaOnline) && (
        <div className="flex items-center gap-1.5 px-3 py-1 text-2xs border-b flex-shrink-0"
          style={{
            borderColor: 'var(--separator)',
            color: !backendConnected && !ollamaOnline ? 'var(--danger)' : 'var(--warning)',
            background: !backendConnected && !ollamaOnline ? 'rgba(239,68,68,0.06)' : 'rgba(245,158,11,0.06)',
          }}>
          <Icon icon={`fa-solid ${!backendConnected && !ollamaOnline ? 'fa-circle-exclamation' : 'fa-triangle-exclamation'}`} />
          {!backendConnected && !ollamaOnline
            ? t('chatModule.statusBackendAndLocalDown')
            : !ollamaOnline
              ? t('chatModule.statusLocalDown')
              : t('chatModule.statusBackendDown')}
        </div>
      )}

      {/* Related memories */}
      {lastUserMessage && (
        <RelatedMemories text={lastUserMessage} />
      )}

      {/* Chain progress */}
      <ChainProgress />

      {/* Memory chips — surfacing related memories while typing */}
      <MemoryChips text={input} onSelectMemory={onSelectMemory} />

      {/* Sandbox preview — pending file operations */}
      {sandbox.pendingList.length > 0 && (
        <div className="flex-shrink-0 px-3 pb-1">
          <div
            style={{
              border: '1px solid rgba(251,191,36,0.3)',
              borderRadius: 8,
              background: 'rgba(251,191,36,0.06)',
              overflow: 'hidden',
            }}
          >
            <div
              onClick={onToggleSandbox}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 10px',
                cursor: 'pointer',
                userSelect: 'none',
              }}
            >
              <span style={{ fontSize: 12 }}>🛡️</span>
              <span style={{ color: '#fbbf24', fontSize: 11, fontWeight: 600 }}>
                {t('chatModule.sandboxPendingFmt', { count: sandbox.pendingList.length })}
              </span>
              <span style={{ color: '#6b7280', fontSize: 10, marginLeft: 'auto' }}>
                {showSandbox ? t('chatModule.sandboxCollapse') : t('chatModule.sandboxExpand')}
              </span>
            </div>
            {showSandbox && (
              <div style={{ padding: '0 10px 8px' }}>
                {/* A4：审批超时提示（sandbox.timeout* 事件） */}
                <SandboxTimeoutHints />
                {/* A4：批量批准（≥2 条时显示） */}
                {sandbox.pendingList.length >= 2 && (
                  <button
                    onClick={async () => {
                      await sandbox.batchApprove(sandbox.pendingList.map(p => p.pending_id))
                    }}
                    className="mb-1.5 px-2 py-1 rounded-md transition-all"
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      color: '#34d399',
                      background: 'rgba(52,211,153,0.1)',
                      border: '1px solid rgba(52,211,153,0.3)',
                    }}
                  >
                    全部批准（{sandbox.pendingList.length}）
                  </button>
                )}
                {sandbox.pendingList.map((preview) => (
                  <SandboxPreview
                    key={preview.pending_id}
                    preview={preview}
                    onApprove={async (pid) => { await sandbox.approve(pid) }}
                    onReject={async (pid) => { await sandbox.reject(pid) }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}

export default ChatContextPanel
