import React, { useState, useMemo, useCallback } from 'react'
import { useStore } from '../../store/useStore'
import StatisticsModule from './StatisticsModule'
import DashboardCard from './DashboardCard'
import { ExamCountdown, GreetingBar, QuickActionBar } from './DashboardWidgets'
import { TaskCardContent, NotesCardContent, SuggestionsCardContent, InfoCardContent, TranslateCardContent, InboxCardContent } from './DashboardCardContents'
import DashboardSettings, { DashboardControlBar } from './DashboardSettings'
import DashboardTodayBar from './DashboardToday'
import { ALL_CARDS, CARD_CONFIGS, DENSITY_CONFIG, LS_KEY, SIZE_NEXT, type CardKey, type LayoutPreset } from './DashboardTypes'
import { swallowError } from '../../utils/swallowError'
import { getTranslations, useTranslation } from '@desk-notes/shared/i18n'

function loadLayout(): LayoutPreset {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw)
  } catch (e) { /* ignore */ swallowError('DashboardModule:15', e) }
  return {
    name: getTranslations().dashboardSettings.defaultPresetName, template: 'balanced',
    visibility: { tasks: true, notes: true, suggestions: true, news: false, translate: false, inbox: false },
    sizes: { tasks: 'medium', notes: 'medium', suggestions: 'medium', news: 'medium', translate: 'medium', inbox: 'medium' },
    spans: { tasks: 1, notes: 1, suggestions: 1, news: 1, translate: 1, inbox: 1 },
    order: ALL_CARDS,
    density: 'normal',
  }
}

function saveLayout(l: LayoutPreset) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(l)) } catch (e) { /* ignore */ swallowError('DashboardModule:27', e) }
}

const DashboardModule: React.FC = () => {
  const { t } = useTranslation()
  const setActiveModule = useStore(s => s.setActiveModule)
  const [layout, setLayout] = useState<LayoutPreset>(loadLayout)
  const [showSettings, setShowSettings] = useState(false)
  const [showStats, setShowStats] = useState(false)
  const [presetName, setPresetName] = useState('')
  const [savedPresets, setSavedPresets] = useState<LayoutPreset[]>(() => {
    try { return JSON.parse(localStorage.getItem('desk-notes-dashboard-presets') || '[]') } catch { return [] }
  })

  const updateLayout = useCallback((patch: Partial<LayoutPreset>) => {
    setLayout(prev => {
      const next = { ...prev, ...patch }
      saveLayout(next)
      return next
    })
  }, [])

  const setTemplate = (t: typeof layout.template) => updateLayout({ template: t })
  const setDensity = (d: typeof layout.density) => updateLayout({ density: d })

  const toggleCard = (key: CardKey) => {
    updateLayout({ visibility: { ...layout.visibility, [key]: !layout.visibility[key] } })
  }

  const cycleSize = (key: CardKey) => {
    updateLayout({ sizes: { ...layout.sizes, [key]: SIZE_NEXT[layout.sizes[key]] } })
  }

  const cycleSpan = (key: CardKey) => {
    updateLayout({ spans: { ...layout.spans, [key]: layout.spans[key] === 1 ? 2 : 1 } })
  }

  const savePreset = () => {
    const name = presetName.trim() || t('dashboardSettings.presetNameFmt', { n: savedPresets.length + 1 })
    const preset: LayoutPreset = { ...layout, name }
    const updated = [...savedPresets.filter(p => p.name !== name), preset]
    setSavedPresets(updated)
    localStorage.setItem('desk-notes-dashboard-presets', JSON.stringify(updated))
    setPresetName('')
  }

  const loadPreset = (preset: LayoutPreset) => {
    setLayout({ ...preset })
    saveLayout(preset)
  }

  const deletePreset = (name: string) => {
    const updated = savedPresets.filter(p => p.name !== name)
    setSavedPresets(updated)
    localStorage.setItem('desk-notes-dashboard-presets', JSON.stringify(updated))
  }

  const resetLayout = () => {
    const def = loadLayout()
    setLayout(def)
    saveLayout(def)
  }

  const visibleCards: CardKey[] = useMemo(() => {
    let ordered = layout.order.filter(k => layout.visibility[k])
    if (layout.template === 'focus') {
      ordered = [...ordered.filter(k => k !== 'news' && k !== 'translate' && k !== 'inbox'), ...ordered.filter(k => k === 'news' || k === 'translate' || k === 'inbox')]
    } else if (layout.template === 'daily') {
      const priority = ordered.filter(k => k === 'tasks' || k === 'suggestions')
      const rest = ordered.filter(k => k !== 'tasks' && k !== 'suggestions')
      ordered = [...priority, ...rest]
    }
    return ordered
  }, [layout])

  const dd = DENSITY_CONFIG[layout.density]

  const renderCardContent = (key: CardKey): React.ReactNode => {
    switch (key) {
      case 'tasks':
        return <TaskCardContent />
      case 'notes':
        return <NotesCardContent />
      case 'suggestions':
        return <SuggestionsCardContent />
      case 'news':
        return <InfoCardContent onNavigate={() => setActiveModule('info')} />
      case 'translate':
        return <TranslateCardContent onNavigate={() => setActiveModule('translate')} />
      case 'inbox':
        return <InboxCardContent onNavigate={() => setActiveModule('inbox')} />
    }
  }

  const cardActionMap: Partial<Record<CardKey, () => void>> = {
    tasks: () => setActiveModule('todo'),
    notes: () => setActiveModule('notes'),
    news: () => setActiveModule('info'),
    translate: () => setActiveModule('translate'),
    inbox: () => setActiveModule('inbox'),
  }

  if (showStats) {
    return (
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <StatisticsModule onBack={() => setShowStats(false)} />
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className={`flex-1 overflow-y-auto ${dd.padding}`}>
        <div className="space-y-5">
          <GreetingBar />
          <ExamCountdown />

          <div className="flex items-center justify-between gap-3">
            <QuickActionBar />
            <DashboardControlBar
              layout={layout}
              showSettings={showSettings}
              onSetTemplate={setTemplate}
              onSetDensity={setDensity}
              onToggleSettings={() => setShowSettings(!showSettings)}
              onShowStats={() => setShowStats(true)}
            />
          </div>

          {showSettings && (
            <DashboardSettings
              layout={layout}
              presetName={presetName}
              savedPresets={savedPresets}
              onToggleCard={toggleCard}
              onReorder={order => updateLayout({ order })}
              onSavePreset={savePreset}
              onLoadPreset={loadPreset}
              onDeletePreset={deletePreset}
              onResetLayout={resetLayout}
              onPresetNameChange={setPresetName}
            />
          )}

          {/* I1：「今天要处理」聚合区 —— 卡片网格上方独立区块 */}
          <DashboardTodayBar />

          <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: `${dd.gap * 4}px` }}>
            {visibleCards.map(key => (
              <DashboardCard
                key={key} config={CARD_CONFIGS[key]} visible={true}
                size={layout.sizes[key]} span={layout.spans[key]}
                density={layout.density}
                onAction={cardActionMap[key]}
                onCycleSize={() => cycleSize(key)}
                onCycleSpan={() => cycleSpan(key)}
              >
                {renderCardContent(key)}
              </DashboardCard>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default DashboardModule
