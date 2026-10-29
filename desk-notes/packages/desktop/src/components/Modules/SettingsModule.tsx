import React, { useState, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { COLOR_PRESETS } from '../../utils/helpers'
import CustomThemeEditor from '../Common/CustomThemeEditor'
import SkinPicker from '../Common/SkinPicker'
import AISettingsPanel from '../Common/AISettingsPanel'
import TrashPanel from '../Common/TrashPanel'
import TemplateManagerPanel from '../Common/TemplateManagerPanel'
import AIActivityPanel from '../Common/AIActivityPanel'
import BackupRestorePanel from '../Common/BackupRestorePanel'
import CloudSyncPanel from '../Common/CloudSyncPanel'
import PageHeader from '../Common/PageHeader'

const SettingsModule: React.FC = () => {
  const { t } = useTranslation()
  const panel = useStore(s => s.panel)
  const setPanelOpacity = useStore(s => s.setPanelOpacity)
  const setPanelColor = useStore(s => s.setPanelColor)
  const backgroundMode = useStore(s => s.backgroundMode)
  const setBackgroundMode = useStore(s => s.setBackgroundMode)
  const theme = useStore(s => s.theme)
  const setTheme = useStore(s => s.setTheme)
  const locale = useStore(s => s.locale)
  const setLocale = useStore(s => s.setLocale)
  const appPin = useStore(s => s.appPin)
  const setAppPin = useStore(s => s.setAppPin)
  const appLocked = useStore(s => s.appLocked)
  const unlockApp = useStore(s => s.unlockApp)
  const [showAISettings, setShowAISettings] = useState(false)
  const [showAIActivity, setShowAIActivity] = useState(false)
  const [showTrash, setShowTrash] = useState(false)
  const [showTemplates, setShowTemplates] = useState(false)
  const [showBackup, setShowBackup] = useState(false)
  const [pinInput, setPinInput] = useState('')
  const [pinConfirm, setPinConfirm] = useState('')
  const [pinError, setPinError] = useState('')
  const [pinStep, setPinStep] = useState<'idle' | 'setup' | 'confirm' | 'unlock'>('idle')

  // Initialize pin state based on current app state
  React.useEffect(() => {
    if (appLocked) setPinStep('unlock')
    else if (appPin) setPinStep('idle')
    else setPinStep('idle')
  }, [])

  const handleSetPin = () => {
    if (pinInput.length < 4) {
      setPinError(t('lock.pinTooShort'))
      return
    }
    if (pinStep === 'setup') {
      setPinConfirm('')
      setPinStep('confirm')
      setPinError('')
    } else if (pinStep === 'confirm') {
      if (pinInput !== pinConfirm) {
        setPinError(t('lock.pinMismatch'))
        return
      }
      setAppPin(pinInput)
      setPinInput('')
      setPinConfirm('')
      setPinError('')
      setPinStep('idle')
    } else if (pinStep === 'unlock') {
      if (pinInput === appPin) {
        unlockApp()
        setPinInput('')
        setPinError('')
        setPinStep('idle')
      } else {
        setPinError(t('lock.wrongPin'))
      }
    }
  }

  const handleChangePin = () => {
    setPinInput('')
    setPinConfirm('')
    setPinError('')
    setPinStep('setup')
  }

  const handleDisablePin = () => {
    setAppPin(null)
    setPinInput('')
    setPinStep('idle')
  }

  const handleSetTheme = (th: 'dark' | 'light' | 'black') => {
    setTheme(th)
  }

  const handleSetLocale = (locale: 'zh-CN' | 'en') => {
    setLocale(locale)
  }

  const sectionClass = "border-b border-white/[0.04] pb-4 mb-4 last:border-b-0 last:mb-0 last:pb-0"

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-lg mx-auto p-5 pt-6 space-y-0">
        <PageHeader title={t('ai.settings.title')} className="mb-5" />

        {/* AI Settings */}
        <div className={sectionClass}>
          <h3 className="text-xs font-medium mb-3" style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-robot mr-1.5 text-xs text-text-muted" />
            {t('ai.settings.title')}
          </h3>
          <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
            {t('settings.aiSettingsDesc')}
          </p>
          <button onClick={() => setShowAISettings(true)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary">
            {t('settings.openAISettings')}
          </button>
          <button onClick={() => setShowAIActivity(true)}
            className="ml-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary">
            <Icon icon="fa-solid fa-chart-simple mr-1" />
            {t('settings.usageCost')}
          </button>
        </div>

        {/* Appearance */}
        <div className={sectionClass}>
          <h3 className="text-xs font-medium mb-3" style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-palette mr-1.5 text-xs text-text-muted" />
            {t('note.appearance')}
          </h3>

          {/* Theme */}
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-text-secondary">{t('note.theme.title')}</span>
            <div className="flex items-center gap-1">
              {(['dark', 'light', 'black'] as const).map(th => (
                <button key={th} onClick={() => handleSetTheme(th)}
                  className={`px-2.5 py-1 rounded-md text-xs font-medium transition-all ${theme === th ? 'bg-white/[0.08] text-text-primary' : 'text-white/45 hover:text-white/65 hover:bg-white/[0.04]'}`}>
                  {t('note.theme.' + th)}
                </button>
              ))}
            </div>
          </div>

          {/* Background mode */}
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-text-secondary">{t('settings.background')}</span>
            <div className="flex items-center gap-1">
              {(['acrylic', 'pure-white', 'pure-black'] as const).map(m => (
                <button key={m} onClick={() => { setBackgroundMode(m) }}
                  className={`px-2.5 py-1 rounded-md text-xs font-medium transition-all ${backgroundMode === m ? 'bg-white/[0.08] text-text-primary' : 'text-white/45 hover:text-white/65 hover:bg-white/[0.04]'}`}>
                  {t('backgroundMode.' + m)}
                </button>
              ))}
            </div>
          </div>

          {/* Opacity */}
          <div className="flex items-center gap-3 mb-3">
            <span className="text-xs text-text-secondary w-12">{t('settings.opacity')}</span>
            <input type="range" min="0.15" max="1" step="0.05" value={panel.opacity}
              onChange={e => { setPanelOpacity(parseFloat(e.target.value)) }}
              className="flex-1 h-1 accent-fluent-blue cursor-pointer" />
            <span className="text-xs text-text-secondary w-8 text-right">{Math.round(panel.opacity * 100)}%</span>
          </div>

          {/* Color presets */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-text-secondary">{t('settings.color')}</span>
            <div className="flex items-center gap-1 flex-wrap">
              {COLOR_PRESETS.map(p => (
                <button key={p.label} onClick={() => { setPanelColor(p.color || undefined) }}
                  className="w-4 h-4 rounded-full border border-white/[0.15] flex items-center justify-center transition-transform hover:scale-125"
                  style={{ background: p.color || 'transparent' }} title={p.label}>
                  {!p.color ? <span className="w-2 h-0.5 bg-white/30 rounded-full" /> : null}
                  {(panel.color || '') === p.color && <Icon icon="fa-solid fa-check text-[6px] text-text-primary" />}
                </button>
              ))}
              <label className="w-4 h-4 rounded-full border border-dashed border-white/[0.25] flex items-center justify-center cursor-pointer hover:border-white/50 transition-colors"
                title={t('note.customColor')}>
                <Icon icon="fa-solid fa-plus text-[7px] text-text-secondary" />
                <input type="color" value={panel.color || '#3b82f6'}
                  onChange={e => setPanelColor(e.target.value)}
                  className="absolute opacity-0 w-0 h-0" />
              </label>
            </div>
          </div>

          {/* Custom Theme */}
          <div className="mt-3">
            <CustomThemeEditor />
          </div>

          {/* Skin */}
          <div className="mt-3">
            <SkinPicker />
          </div>
        </div>

        {/* Language */}
        <div className={sectionClass}>
          <h3 className="text-xs font-medium mb-3" style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-language mr-1.5 text-xs text-text-muted" />
            {t('settings.language')}
          </h3>
          <div className="flex items-center gap-2">
            <button onClick={() => handleSetLocale('zh-CN')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${locale === 'zh-CN' ? 'bg-white/[0.08] text-text-primary' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]'}`}>
              {t('settings.languageChinese')}
            </button>
            <button onClick={() => handleSetLocale('en')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${locale === 'en' ? 'bg-white/[0.08] text-text-primary' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]'}`}>
              {t('settings.languageEnglish')}
            </button>
          </div>
        </div>

        {/* Security / Lock */}
        <div className={sectionClass}>
          <h3 className="text-xs font-medium mb-3" style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-shield-halved mr-1.5 text-xs text-text-muted" />
            {t('lock.title')}
          </h3>
          {pinStep === 'unlock' ? (
            <div className="flex items-center gap-2">
              <input type="password" value={pinInput} onChange={e => { setPinInput(e.target.value); setPinError('') }}
                onKeyDown={e => e.key === 'Enter' && handleSetPin()}
                placeholder={t('lock.pinPlaceholder')}
                maxLength={6}
                className="w-28 px-2 py-1.5 rounded-lg text-xs outline-none border"
                style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }} />
              <button onClick={handleSetPin}
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-all">
                {t('common.confirm')}
              </button>
              {pinError && <span className="text-xs text-red-400">{pinError}</span>}
            </div>
          ) : pinStep === 'setup' || pinStep === 'confirm' ? (
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <input type="password" value={pinStep === 'setup' ? pinInput : pinConfirm}
                  onChange={e => {
                    if (pinStep === 'setup') { setPinInput(e.target.value); setPinError('') }
                    else { setPinConfirm(e.target.value); setPinError('') }
                  }}
                  onKeyDown={e => e.key === 'Enter' && handleSetPin()}
                  placeholder={pinStep === 'setup' ? t('lock.setupPin') : t('lock.confirmPin')}
                  maxLength={6}
                  className="w-36 px-2 py-1.5 rounded-lg text-xs outline-none border"
                  style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }} />
                <button onClick={handleSetPin}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-all">
                  {t('common.confirm')}
                </button>
                <button onClick={() => { setPinStep('idle'); setPinError('') }}
                  className="px-2 py-1.5 rounded-lg text-xs text-text-muted hover:text-text-secondary transition-all">
                  {t('common.cancel')}
                </button>
              </div>
              {pinError && <span className="text-xs text-red-400">{pinError}</span>}
            </div>
          ) : appPin ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-text-secondary">{t('lock.pinSet')}</span>
              <button onClick={handleChangePin}
                className="px-2 py-1 rounded-md text-xs text-text-secondary hover:text-text-secondary hover:bg-white/[0.04] transition-all">
                {t('lock.change')}
              </button>
              <button onClick={handleDisablePin}
                className="px-2 py-1 rounded-md text-xs text-red-400/60 hover:text-red-400 hover:bg-red-400/[0.06] transition-all">
                {t('lock.disable')}
              </button>
            </div>
          ) : (
            <button onClick={() => setPinStep('setup')}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary transition-all">
              <Icon icon="fa-solid fa-plus mr-1 text-2xs" />
              {t('lock.setupPin')}
            </button>
          )}
        </div>

        {/* Data management */}
        <div className={sectionClass}>
          <h3 className="text-xs font-medium mb-3" style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-database mr-1.5 text-xs text-text-muted" />
            {t('settings.dataManagement')}
          </h3>
          <div className="flex items-center gap-2 flex-wrap">
            <button onClick={() => setShowBackup(true)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary">
              <Icon icon="fa-solid fa-floppy-disk mr-1.5" />
              {t('backup.title')}
            </button>
            <button onClick={() => setShowTrash(true)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary">
              <Icon icon="fa-regular fa-trash-can mr-1.5" />
              {t('trash.title')}
            </button>
            <button onClick={() => setShowTemplates(true)}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary">
              <Icon icon="fa-regular fa-file-lines mr-1.5" />
              {t('toolbar.template')}
            </button>
          </div>
        </div>
      </div>

      {/* Cloud sync (fallback channel) */}
      <CloudSyncPanel />

      {/* P1-4: 设备配对批准（手机端发起配对后在此批准） */}
      <PairingApprovalSection />

      {/* WebSocket URL config */}
      <WsConfigSection />

      {/* Sidebar management */}
      <SidebarManagementSection />

      {/* Chat history import */}
      <ImportSection />

      {/* Modal overlays */}
      {showAISettings && <AISettingsPanel onClose={() => setShowAISettings(false)} />}
      {showAIActivity && <AIActivityPanel onClose={() => setShowAIActivity(false)} />}
      {showTrash && <TrashPanel onClose={() => setShowTrash(false)} />}
      {showTemplates && <TemplateManagerPanel onClose={() => setShowTemplates(false)} />}
      {showBackup && <BackupRestorePanel onClose={() => setShowBackup(false)} />}
    </div>
  )
}

export default SettingsModule

// Sidebar management component

function SidebarManagementSection() {
  const { t } = useTranslation()
  const sidebarConfig = useStore(s => s.sidebarConfig)
  const setSidebarConfig = useStore(s => s.setSidebarConfig)
  const resetSidebarConfig = useStore(s => s.resetSidebarConfig)
  const [draggedKey, setDraggedKey] = useState<string | null>(null)

  // Module metadata (icon + labelKey)
  // U4 修复（2026-09-16）：补齐 getDefaultSidebarConfig 实际引用的缺失 key
  // （memory / job-hunting / info / rss / system-monitor / app-manager 等）。
  // 此前这些模块因 `if (!meta) return null` 在「侧边栏管理」页静默消失。
  // 图标对照 NavSidebar MODULE_META；NavSidebar 未收录的（info/rss/ 等）选语义相近的既有 fa 图标。
  const MODULE_META: Record<string, { icon: string; labelKey: string }> = {
    dashboard: { icon: 'fa-solid fa-table-cells-large', labelKey: 'nav.dashboard' },
    chat: { icon: 'fa-solid fa-comment-dots', labelKey: 'nav.chat' },
    notes: { icon: 'fa-regular fa-note-sticky', labelKey: 'nav.notes' },
    todo: { icon: 'fa-regular fa-square-check', labelKey: 'nav.todo' },
    calendar: { icon: 'fa-regular fa-calendar', labelKey: 'nav.calendar' },
    inbox: { icon: 'fa-regular fa-inbox', labelKey: 'nav.inbox' },
    learning: { icon: 'fa-solid fa-graduation-cap', labelKey: 'nav.learning' },
    'knowledge-network': { icon: 'fa-solid fa-share-nodes', labelKey: 'nav.knowledgeNetwork' },
    tools: { icon: 'fa-solid fa-toolbox', labelKey: 'nav.tools' },
    settings: { icon: 'fa-solid fa-gear', labelKey: 'nav.settings' },
    // ↓ 补缺 key（图标与 NavSidebar MODULE_META 一致，NavSidebar 无收录的见注释）
    agent: { icon: 'fa-solid fa-robot', labelKey: 'nav.agent' },
    memory: { icon: 'fa-solid fa-brain', labelKey: 'nav.memory' },
    'job-hunting': { icon: 'fa-solid fa-briefcase', labelKey: 'nav.jobHunting' },
    notification: { icon: 'fa-solid fa-bell', labelKey: 'nav.notification' },
    automation: { icon: 'fa-solid fa-bolt', labelKey: 'nav.automation' },
    'task-queue': { icon: 'fa-solid fa-list-check', labelKey: 'nav.taskQueue' },
    usage: { icon: 'fa-solid fa-chart-pie', labelKey: 'nav.usage' },
    admin: { icon: 'fa-solid fa-shield-halved', labelKey: 'nav.admin' },
    personal: { icon: 'fa-solid fa-user-astronaut', labelKey: 'nav.personal' },
    info: { icon: 'fa-solid fa-newspaper', labelKey: 'nav.info' },
    rss: { icon: 'fa-solid fa-rss', labelKey: 'nav.rss' },
    'system-monitor': { icon: 'fa-solid fa-server', labelKey: 'nav.systemMonitor' },
    'app-manager': { icon: 'fa-solid fa-table-list', labelKey: 'nav.appManager' },
  }

  // Update a single item's config
  const updateItem = (key: string, patch: Partial<{ visible: boolean; pinned: boolean; order: number }>) => {
    const items = sidebarConfig.items.map(it =>
      it.key === key ? { ...it, ...patch } : it
    )
    setSidebarConfig({ ...sidebarConfig, items })
  }

  // Drag-reorder (F8 fix: insert into target slot rather than swap)
  const handleDrop = (targetKey: string) => {
    if (!draggedKey || draggedKey === targetKey) return
    const dragged = sidebarConfig.items.find(it => it.key === draggedKey)
    const target = sidebarConfig.items.find(it => it.key === targetKey)
    if (!dragged || !target) return
    // Reorder within same group: remove dragged then re-insert at target position
    if (dragged.groupId === target.groupId) {
      const sameGroup = sidebarConfig.items
        .filter(it => it.groupId === dragged.groupId)
        .sort((a, b) => a.order - b.order)
      const withoutDragged = sameGroup.filter(it => it.key !== draggedKey)
      const targetIdx = withoutDragged.findIndex(it => it.key === targetKey)
      withoutDragged.splice(targetIdx, 0, dragged)
      // Reassign order indices
      const orderMap = new Map<string, number>()
      withoutDragged.forEach((it, idx) => orderMap.set(it.key, idx))
      const items = sidebarConfig.items.map(it =>
        orderMap.has(it.key) ? { ...it, order: orderMap.get(it.key)! } : it
      )
      setSidebarConfig({ ...sidebarConfig, items })
    }
    // Cross-group drag: update groupId of dragged
    else {
      const items = sidebarConfig.items.map(it =>
        it.key === draggedKey ? { ...it, groupId: target.groupId } : it
      )
      setSidebarConfig({ ...sidebarConfig, items })
    }
    setDraggedKey(null)
  }

  // Render by group
  const renderGroup = (groupId: string | null, label: string) => {
    const groupItems = [...sidebarConfig.items]
      .filter(it => it.groupId === groupId)
      .sort((a, b) => a.order - b.order)

    if (groupItems.length === 0) return null

    return (
      <div key={groupId || 'standalone'} className="mb-3">
        <div className="text-xs text-text-muted mb-1.5 font-medium uppercase tracking-wider">{label}</div>
        <div className="space-y-1">
          {groupItems.map(it => {
            const meta = MODULE_META[it.key]
            if (!meta) return null
            const isSettings = it.key === 'settings'
            return (
              <div
                key={it.key}
                draggable={!isSettings}
                onDragStart={() => !isSettings && setDraggedKey(it.key)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => !isSettings && handleDrop(it.key)}
                className={`flex items-center gap-2 px-2.5 py-2 rounded-lg border transition-all ${
                  draggedKey === it.key
                    ? 'opacity-40 border-fluent-blue/40'
                    : 'border-transparent bg-white/[0.03] hover:bg-white/[0.06]'
                } ${!isSettings ? 'cursor-move' : 'cursor-default'}`}
              >
                {/* Drag handle */}
                <Icon icon={`fa-solid fa-grip-vertical text-xs ${isSettings ? 'opacity-20' : 'text-text-dim'}`} />
                {/* Icon */}
                <Icon icon={`${meta.icon} text-xs w-4 text-center text-text-secondary`} />
                {/* Label */}
                <span className="flex-1 text-xs text-text-secondary">{t(meta.labelKey)}</span>
                {/* Pin toggle */}
                <button
                  onClick={() => !isSettings && updateItem(it.key, { pinned: !it.pinned })}
                  disabled={isSettings}
                  className={`w-6 h-6 flex items-center justify-center rounded text-xs transition-colors ${
                    it.pinned
                      ? 'text-yellow-400 bg-yellow-400/10'
                      : 'text-text-dim hover:text-text-secondary'
                  } ${isSettings ? 'opacity-30 cursor-not-allowed' : ''}`}
                  title={t('nav.sidebarPinned')}
                >
                  <Icon icon="fa-solid fa-star" />
                </button>
                {/* Visibility toggle */}
                <button
                  onClick={() => !isSettings && updateItem(it.key, { visible: !it.visible })}
                  disabled={isSettings}
                  className={`w-6 h-6 flex items-center justify-center rounded text-xs transition-colors ${
                    it.visible
                      ? 'text-fluent-blue bg-fluent-blue/10'
                      : 'text-text-dim hover:text-text-secondary'
                  } ${isSettings ? 'opacity-30 cursor-not-allowed' : ''}`}
                  title={t('nav.sidebarVisible')}
                >
                  <Icon icon={`fa-solid ${it.visible ? 'fa-eye' : 'fa-eye-slash'}`} />
                </button>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="mb-6 p-4 rounded-xl border" style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}>
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-sm font-medium text-text-primary flex items-center gap-2">
            <Icon icon="fa-solid fa-bars-staggered text-fluent-blue" />
            {t('nav.sidebarManagement')}
          </div>
          <div className="text-xs text-text-muted mt-0.5">{t('nav.sidebarManagementDesc')}</div>
        </div>
        <button
          onClick={() => resetSidebarConfig()}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary"
        >
          <Icon icon="fa-solid fa-rotate-left mr-1" />
          {t('nav.sidebarReset')}
        </button>
      </div>

      <div className="text-xs text-text-dim mb-2 italic">{t('nav.sidebarDragHint')}</div>

      {sidebarConfig.groups.map(g => renderGroup(g.id, t(g.labelKey)))}
      {renderGroup(null, t('nav.settings'))}
    </div>
  )
}

// Chat history import section

function ImportSection() {
  const { t } = useTranslation()
  const [status, setStatus] = useState('')
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<{ imported?: number; total?: number; categories?: Record<string, number>; files?: any[] } | null>(null)

  const handleSelectFile = async () => {
    const ei = window.electronAPI as any
    if (!ei?.showOpenDialog) return
    const result = await ei.showOpenDialog({
      filters: [{ name: t('importChat.filterName'), extensions: ['json', 'md'] }],
      properties: ['openFile', 'multiSelections'],
    })
    if (!result?.filePaths?.length) return
    setImporting(true); setResult(null)
    try {
      const { getWsClient } = await import('@desk-notes/shared')
      const client = getWsClient()
      let resp: any
      if (result.filePaths.length === 1) {
        resp = await client.call('data.import_chat', { file_path: result.filePaths[0] })
      } else {
        const dir = result.filePaths[0].split(/[/\\]/).slice(0, -1).join('/')
        resp = await client.call('data.import_chat_folder', { folder_path: dir })
      }
      setResult(resp)
      setStatus(resp?.success ? t('importChat.successMark') : t('importChat.failedFmt', { error: resp?.error || '' }))
    } catch (err: unknown) {
      setStatus(t('importChat.failedFmt', { error: err instanceof Error ? err.message : t('importChat.unknownError') }))
    }
    setImporting(false)
  }

  return (
    <div className="px-4 py-3 border-t" style={{ borderColor: 'var(--panel-border)' }}>
      <div className="flex items-center gap-2 mb-2">
        <Icon icon="fa-solid fa-download text-xs" style={{ color: 'var(--text-muted)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{t('importChat.title')}</span>
      </div>
      <div className="text-2xs mb-2" style={{ color: 'var(--text-tertiary)' }}>
        {t('importChat.desc')}
      </div>
      <button
        onClick={handleSelectFile}
        disabled={importing}
        className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
        style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}
      >
        {importing ? t('importChat.importing') : t('importChat.selectFile')}
      </button>
      {status && <div className="text-xs mt-1" style={{ color: status.startsWith('✅') ? 'var(--tone-smooth)' : 'var(--danger)' }}>{status}</div>}
      {result?.total !== undefined && (
        <div className="text-2xs mt-1 space-y-0.5" style={{ color: 'var(--text-tertiary)' }}>
          <div>{t('importChat.summaryFmt', { total: String(result.total), imported: String(result.imported ?? 0) })}</div>
          {result.categories && Object.entries(result.categories).length > 0 && (
            <div>{t('importChat.categoriesFmt', { list: Object.entries(result.categories).map(([k, v]) => `${k}=${v}`).join(t('importChat.categoriesSep')) })}</div>
          )}
        </div>
      )}
    </div>
  )
}

// 判断 WS URL 是否为远程（非 loopback）地址：远程后端需要 token 才能连
function isRemoteWsUrl(url: string): boolean {
  if (!url) return false
  try {
    const h = new URL(url).hostname
    return !(h === '127.0.0.1' || h === '::1' || h === 'localhost')
  } catch {
    return false
  }
}

// WebSocket URL + token 配置（P1-4 follow-up：远程后端 token 输入通路）
function WsConfigSection() {
  const { t } = useTranslation()
  const [wsUrl, setWsUrl] = useState(() => localStorage.getItem('siyi_ws_url') ?? '')
  const [wsToken, setWsToken] = useState(() => localStorage.getItem('siyi_ws_token') ?? '')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  // URL format check: blank is OK (use default); non-blank must start with ws:// or wss://
  const trimmed = wsUrl.trim()
  const isValid = !trimmed || /^wss?:\/\/.+/.test(trimmed)
  const isRemote = isRemoteWsUrl(trimmed)

  // P1-4 follow-up：把 URL + token 推给 WsClient 并重建单例，立即重连到新地址。
  // token 仅对远程后端有意义（loopback 由服务端直接信任，无需也不保存 token）。
  const applyAndReconnect = async (url: string, token: string) => {
    const { configureWsAuthToken, getWsClient } = await import('@desk-notes/shared')
    const effectiveToken = isRemoteWsUrl(url) ? token.trim() : ''
    if (effectiveToken) localStorage.setItem('siyi_ws_token', effectiveToken)
    else localStorage.removeItem('siyi_ws_token')
    configureWsAuthToken(effectiveToken || null)
    getWsClient() // 重建单例并自动 connect（远程自动以 ?token=<token> 携带）
  }

  const handleSave = async () => {
    if (!isValid) return
    setError('')
    try {
      if (trimmed) localStorage.setItem('siyi_ws_url', trimmed)
      else localStorage.removeItem('siyi_ws_url')
      await applyAndReconnect(trimmed, wsToken)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleReset = async () => {
    localStorage.removeItem('siyi_ws_url')
    localStorage.removeItem('siyi_ws_token')
    setWsUrl('')
    setWsToken('')
    setError('')
    try {
      const { configureWsAuthToken, getWsClient } = await import('@desk-notes/shared')
      configureWsAuthToken(null)
      getWsClient()
    } catch { /* ignore */ }
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="rounded-xl p-3 space-y-2" style={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)' }}>
      <div className="flex items-center gap-2">
        <Icon icon="fa-solid fa-plug text-xs" style={{ color: 'var(--text-secondary)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
          {t('settings.wsConfig')}
        </span>
      </div>
      <div className="text-2xs" style={{ color: 'var(--text-tertiary)' }}>
        {t('settings.wsConfigDesc')}
      </div>
      <div className="flex gap-1.5">
        <input
          type="text"
          value={wsUrl}
          onChange={(e) => setWsUrl(e.target.value)}
          placeholder={t('settings.wsConfigPlaceholder')}
          className="flex-1 px-2 py-1 rounded-lg text-xs outline-none"
          style={{
            background: 'var(--bg)',
            border: `1px solid ${!isValid ? 'var(--danger)' : 'var(--panel-border)'}`,
            color: 'var(--text-primary)',
          }}
        />
        <button
          onClick={handleSave}
          disabled={!isValid}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
          style={{ background: 'var(--accent-bg)', color: 'var(--accent-text)' }}
        >
          {saved ? t('settings.wsSavedMark') : t('settings.wsSave')}
        </button>
        <button
          onClick={handleReset}
          className="px-2 py-1 rounded-lg text-xs transition-all"
          style={{ background: 'rgba(255,255,255,0.04)', color: 'var(--text-tertiary)' }}
        >
          {t('settings.wsReset')}
        </button>
      </div>
      {/* P1-4 follow-up: 远程后端 token 输入 */}
      <div className="flex gap-1.5">
        <input
          type="password"
          value={wsToken}
          onChange={(e) => setWsToken(e.target.value)}
          placeholder={t('settings.wsTokenPlaceholder')}
          className="flex-1 px-2 py-1 rounded-lg text-xs outline-none"
          style={{ background: 'var(--bg)', border: '1px solid var(--panel-border)', color: 'var(--text-primary)' }}
        />
      </div>
      {isRemote && (
        <div className="text-2xs" style={{ color: 'var(--text-tertiary)' }}>
          {t('settings.wsTokenDesc')}
        </div>
      )}
      {!isValid && (
        <div className="text-2xs" style={{ color: 'var(--danger)' }}>
          {t('settings.wsInvalidUrl')}
        </div>
      )}
      {error && (
        <div className="text-2xs" style={{ color: 'var(--danger)' }}>{error}</div>
      )}
      {saved && (
        <div className="text-2xs" style={{ color: 'var(--text-tertiary)' }}>
          {t('settings.wsSavedHint')}
        </div>
      )}
    </div>
  )
}

// P1-4: 设备配对批准（手机端发起配对后在此批准并下发主 token）
function PairingApprovalSection() {
  const { t } = useTranslation()
  const [pending, setPending] = useState<{ request_id: string; device_name: string; created: number }[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  const loadPending = async () => {
    try {
      const { getWsClient } = await import('@desk-notes/shared')
      const client = getWsClient()
      const res: any = await client.call('pairing.pending')
      if (res?.success) setPending(res.pending || [])
      else setError(res?.error ? String(res.error) : t('settings.pairingLoadFailed'))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    loadPending()
    // A-3：配对是低频事件，10s 轮询足够；断连时跳过，避免 call 挂起排队
    // 参与 _waitOpen 惊群（重连瞬间所有排队请求齐发再度触发限流）。
    const id = setInterval(() => {
      void (async () => {
        const { getWsClient } = await import('@desk-notes/shared')
        if (getWsClient()?.status !== 'connected') return
        await loadPending()
      })()
    }, 10_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const approve = async (requestId: string) => {
    setBusy(requestId)
    setError('')
    try {
      const { getWsClient } = await import('@desk-notes/shared')
      const client = getWsClient()
      const res: any = await client.call('pairing.approve', { request_id: requestId })
      if (res?.success) {
        setPending((p) => p.filter((x) => x.request_id !== requestId))
      } else {
        setError(res?.error ? String(res.error) : t('settings.pairingApproveFailed'))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="rounded-xl p-3 space-y-2" style={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)' }}>
      <div className="flex items-center gap-2">
        <Icon icon="fa-solid fa-mobile-screen mr-1 text-xs" style={{ color: 'var(--text-secondary)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
          {t('settings.devicePairing')}
        </span>
      </div>
      <div className="text-2xs" style={{ color: 'var(--text-tertiary)' }}>
        {t('settings.pairingHint')}
      </div>
      {error && <div className="text-2xs" style={{ color: 'var(--danger)' }}>{error}</div>}
      {pending.length === 0 ? (
        <div className="text-2xs" style={{ color: 'var(--text-tertiary)' }}>{t('settings.pairingEmpty')}</div>
      ) : (
        pending.map((p) => (
          <div key={p.request_id} className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5" style={{ background: 'var(--bg)' }}>
            <div className="min-w-0">
              <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{p.device_name}</div>
              <div className="text-2xs" style={{ color: 'var(--text-tertiary)' }}>{t('settings.pairingRequested')}</div>
            </div>
            <button
              onClick={() => approve(p.request_id)}
              disabled={busy === p.request_id}
              className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
              style={{ background: 'var(--accent-bg)', color: 'var(--accent-text)' }}
            >
              {busy === p.request_id ? t('settings.pairingApproving') : t('settings.pairingApprove')}
            </button>
          </div>
        ))
      )}
    </div>
  )
}
