import React, { Suspense, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { useTranslation, tStatic } from '../../i18n'
import ErrorBoundary from '../Common/ErrorBoundary'
import Skeleton from '../Common/Skeleton'
import { useTodayReviewLauncher } from '../TodayReview/useTodayReviewLauncher'

// 旧 ChatModule 已被 NewChatView 替换
const NewChatView = React.lazy(() =>
  import('../chat/ChatView').then((m) => ({ default: m.ChatView }))
)
const NotesModule = React.lazy(() => import('../Modules/NotesModule'))
const TodoModule = React.lazy(() => import('../Modules/TodoModule'))
const CalendarModule = React.lazy(() => import('../Modules/CalendarModule'))
// 工作台：2026-09-09 导航收敛只把它从**侧边栏**隐藏（NavSidebar 注释「仅隐藏，代码与
// 组件保留」），但 17ecc90 顺手删掉了这里的 case，导致 4 处调用点全部静默失效：
// store 启动默认值 activeModule='dashboard'（uiSlice.ts）落到 default 分支渲染成
// NewChatView、快捷键 4（MainWindow）、快速搜索「工作台」（QuickSearchModal）。
// 2026-09-16 补回路由；并由 tests/module-route-coverage.test.ts 加机器断言防复发。
const DashboardModule = React.lazy(() => import('../Modules/DashboardModule'))
const WordbankModule = React.lazy(() => import('../Wordbank/WordbankModule'))
const FlashcardModule = React.lazy(() => import('../Modules/FlashcardModule'))
const InfoModule = React.lazy(() => import('../Modules/InfoModule'))
const SettingsModule = React.lazy(() => import('../Modules/SettingsModule'))
const AIManagementModule = React.lazy(() => import('../Modules/AIManagementModule'))
const KnowledgeGraphModule = React.lazy(() => import('../Modules/KnowledgeGraphModule'))
const KnowledgeBaseModule = React.lazy(() => import('../Modules/KnowledgeBaseModule'))
const BacklinksModule = React.lazy(() => import('../Modules/BacklinksModule'))
const InboxModule = React.lazy(() => import('../Modules/InboxModule'))
const TranslationModule = React.lazy(() => import('../Modules/TranslationModule'))
const TransferView = React.lazy(() => import('../Modules/TransferView'))
const SystemMonitorModule = React.lazy(() => import('../Modules/AgentMonitorModule'))
const AutomationModule = React.lazy(() => import('../Modules/AutomationModule').then((m) => ({ default: m.AutomationModule })))
const OCRModule = React.lazy(() => import('../Modules/OCRModule').then((m) => ({ default: m.OCRModule })))
const NotificationModule = React.lazy(() => import('../Modules/NotificationModule').then((m) => ({ default: m.NotificationModule })))
const JobHunterModule = React.lazy(() => import('../Modules/JobHunterModule').then((m) => ({ default: m.JobHunterModule })))
const TaskQueueModule = React.lazy(() => import('../Modules/TaskQueueModule').then((m) => ({ default: m.TaskQueueModule })))
const UsageDashboardModule = React.lazy(() => import('../Modules/UsageDashboardModule').then((m) => ({ default: m.UsageDashboardModule })))
const ManagementModule = React.lazy(() => import('../Modules/ManagementModule').then((m) => ({ default: m.ManagementModule })))
const PersonalModule = React.lazy(() => import('../Modules/PersonalModule').then((m) => ({ default: m.PersonalModule })))
// U3 批2 记忆管理面板 2026-09-16
const MemoryModule = React.lazy(() => import('../Modules/MemoryModule').then((m) => ({ default: m.MemoryModule })))
const TodayReviewModule = React.lazy(() => import('../TodayReview/TodayReviewModule'))

/** 合并模块的 Tab 切换容器 */
const TabbedModule: React.FC<{
  tabs: { key: string; label: string; render: () => React.ReactNode }[]
  defaultTab?: string
}> = ({ tabs, defaultTab }) => {
  const [activeTab, setActiveTab] = useState(defaultTab || tabs[0]?.key)
  // 存储上一次的 defaultTab，仅当外部导航切换时才同步
  const prevDefaultRef = React.useRef(defaultTab)
  React.useEffect(() => {
    if (defaultTab && defaultTab !== prevDefaultRef.current) {
      prevDefaultRef.current = defaultTab
      setActiveTab(defaultTab)
    }
  }, [defaultTab])
  // 当 tabs 变化时，确保 activeTab 仍有效
  React.useEffect(() => {
    if (!tabs.some((tab) => tab.key === activeTab)) {
      const fallbackTab = tabs[0]?.key || defaultTab
      if (fallbackTab) setActiveTab(fallbackTab)
    }
  }, [tabs])
  if (tabs.length === 0) {
    return <div className="flex-1 flex items-center justify-center text-sm text-[var(--text-muted)]">{tStatic('ux.noModulesAvailable')}</div>
  }
  const active = tabs.find(t => t.key === activeTab) || tabs[0]
  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Tab 栏 */}
      <div
        className="flex items-center gap-1 px-5 py-2 border-b"
        style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg-solid)' }}
      >
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-3 py-1.5 rounded-md text-xs transition-colors ${
              activeTab === tab.key
                ? 'text-brand bg-brand-soft'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--hover-bg)]'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {/* 内容（P5 动效层：tab 切换淡入，仅入场动画避免退场依赖 rAF 被遮挡时卡死） */}
      <motion.div
        key={active?.key}
        className="flex-1 flex flex-col min-h-0 overflow-hidden"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.15, ease: 'easeOut' }}
      >
        {active?.render()}
      </motion.div>
    </div>
  )
}

/** 模块懒加载骨架屏（P6）：模拟页面头部 + 卡片网格，避免裸 spinner 布局跳动 */
const LoadingFallback: React.FC = () => (
  <div className="flex-1 flex flex-col min-h-0 overflow-hidden p-5 gap-4" data-testid="module-loading-skeleton">
    <div className="flex items-center justify-between flex-shrink-0">
      <div className="space-y-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-3 w-56" />
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-8 w-20" />
        <Skeleton className="h-8 w-8" />
      </div>
    </div>
    <Skeleton className="h-4 w-full max-w-md flex-shrink-0" />
    <div className="grid grid-cols-2 gap-4 flex-1 min-h-0">
      <Skeleton className="h-full" />
      <Skeleton className="h-full" />
      <Skeleton className="h-full" />
      <Skeleton className="h-full" />
    </div>
  </div>
)

const ContentRouter: React.FC = () => {
  const activeModule = useStore(s => s.activeModule)
  const setCurrentContext = useStore(s => s.setCurrentContext)
  const { t: _t } = useTranslation()
  // V1-6：今日回顾入口（Alt+R）在此常驻注册，侧边栏导航项与数量不变
  useTodayReviewLauncher()

  useEffect(() => {
    setCurrentContext(activeModule, null, null)
  }, [activeModule, setCurrentContext])

  const renderModule = () => {
    switch (activeModule) {
      case 'chat':
        return <NewChatView />
      case 'notes':
        return <NotesModule />
      case 'todo':
        return <TodoModule />
      case 'calendar':
        return <CalendarModule />
      case 'dashboard':
        return <DashboardModule />
      // 合并项：学习 = 词库 + 闪卡
      case 'learning':
      case 'wordbank':
      case 'flashcard':
        return (
          <TabbedModule
            defaultTab={activeModule === 'flashcard' ? 'flashcard' : 'wordbank'}
            tabs={[
              { key: 'wordbank', label: _t('nav.wordbank'), render: () => <WordbankModule /> },
              { key: 'flashcard', label: _t('nav.flashcard'), render: () => <FlashcardModule /> },
            ]}
          />
        )
      // 合并项：知识网络 = 知识图谱 + 知识库 + 反向链接
      case 'knowledge-network':
      case 'knowledge-graph':
      case 'knowledge-base':
      case 'backlinks':
        return (
          <TabbedModule
            defaultTab={activeModule === 'backlinks' ? 'backlinks' : activeModule === 'knowledge-base' ? 'knowledge-base' : 'knowledge-graph'}
            tabs={[
              { key: 'knowledge-graph', label: _t('nav.knowledgeGraph'), render: () => <KnowledgeGraphModule /> },
              { key: 'knowledge-base', label: _t('nav.knowledgeBase'), render: () => <KnowledgeBaseModule /> },
              { key: 'backlinks', label: _t('nav.backlinks'), render: () => <BacklinksModule /> },
            ]}
          />
        )
      // 合并项：工具 = 翻译 + 传送 + 信息 + 系统监控
      case 'tools':
      case 'translate':
      case 'transfer':
      case 'info':
      case 'system-monitor':
        return (
          <TabbedModule
            defaultTab={activeModule === 'tools' ? 'translate' : activeModule}
            tabs={[
              { key: 'translate', label: _t('nav.translate'), render: () => <TranslationModule /> },
              { key: 'transfer', label: _t('nav.transfer'), render: () => <TransferView /> },
              { key: 'info', label: _t('nav.info'), render: () => <InfoModule /> },
              { key: 'system-monitor', label: _t('nav.systemMonitor'), render: () => <SystemMonitorModule /> },
              { key: 'ocr', label: _t('ux.ocrTab'), render: () => <OCRModule /> },
            ]}
          />
        )
      case 'agent':
        return <AIManagementModule />
      case 'inbox':
        return <InboxModule />
      case 'settings':
        return <SettingsModule />
      case 'notification':
        return <NotificationModule />
      case 'automation':
        return <AutomationModule />
      case 'rss':
        return <InfoModule />
      case 'job-hunting':
        return <JobHunterModule />
      case 'task-queue':
        return <TaskQueueModule />
      case 'usage':
        return <UsageDashboardModule />
      case 'admin':
        return <ManagementModule />
      case 'personal':
        return <PersonalModule />
      case 'memory':
        return <MemoryModule />
      case 'today-review':
        return <TodayReviewModule />
      default:
        return <NewChatView />
    }
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* P5 动效层：模块切换入场淡入+上移（不用 AnimatePresence：退场动画依赖 rAF，窗口被遮挡时会被浏览器暂停导致模块切换卡死） */}
      <motion.div
        key={activeModule}
        className="flex-1 flex min-h-0 overflow-hidden"
        data-testid="module-content"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
      >
        <ErrorBoundary label="主窗口模块">
          <Suspense fallback={<LoadingFallback />}>
            {renderModule()}
          </Suspense>
        </ErrorBoundary>
      </motion.div>
    </div>
  )
}

export default ContentRouter
