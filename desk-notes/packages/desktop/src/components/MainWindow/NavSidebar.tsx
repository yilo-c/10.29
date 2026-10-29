import React, { useEffect, useState } from 'react'
import Icon from '../Common/Icon'
import { motion } from 'framer-motion'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { getWsClient } from '../../utils/ai'
import StatusPill from '../Common/StatusPill'
import type { ActiveModule } from '@desk-notes/shared'

/** 每个模块的图标与标签 key 映射。
 *
 * P2-23 只读清理：改为 Partial —— 仅保留渲染路径（SECTIONS 全量平铺、
 * MERGED_DEFAULTS 路由目标）实际引用的 key。以下 key 已确认无任何
 * MODULE_META 查询路径（不在 SECTIONS 中，侧边栏不会渲染它们），且代码库中
 * 无 setActiveModule 调用可将其设为激活模块，故移除：
 *   flashcard / knowledge-base / backlinks / transfer / info / rss /
 *   app-manager / system-monitor
 * 存疑保留：settings（footer 目前硬编码图标）、
 * wordbank / knowledge-graph / translate（MERGED_DEFAULTS 路由目标，上轮结论不可删）。
 */
const MODULE_META: Partial<Record<ActiveModule, { icon: string; labelKey: string }>> = {
  dashboard: { icon: 'fa-solid fa-table-columns', labelKey: 'nav.dashboard' },
  chat: { icon: 'fa-solid fa-comment-dots', labelKey: 'nav.chat' },
  notes: { icon: 'fa-regular fa-note-sticky', labelKey: 'nav.notes' },
  todo: { icon: 'fa-regular fa-square-check', labelKey: 'nav.todo' },
  calendar: { icon: 'fa-solid fa-calendar-days', labelKey: 'nav.calendar' },
  inbox: { icon: 'fa-solid fa-inbox', labelKey: 'nav.inbox' },
  learning: { icon: 'fa-solid fa-graduation-cap', labelKey: 'nav.learning' },
  'knowledge-network': { icon: 'fa-solid fa-book', labelKey: 'nav.knowledgeNetwork' },
  tools: { icon: 'fa-solid fa-toolbox', labelKey: 'nav.tools' },
  wordbank: { icon: 'fa-solid fa-spell-check', labelKey: 'nav.wordbank' },
  'knowledge-graph': { icon: 'fa-solid fa-diagram-project', labelKey: 'nav.knowledgeGraph' },
  translate: { icon: 'fa-solid fa-language', labelKey: 'nav.translate' },
  agent: { icon: 'fa-solid fa-robot', labelKey: 'nav.agent' },
  notification: { icon: 'fa-solid fa-bell', labelKey: 'nav.notification' },
  automation: { icon: 'fa-solid fa-bolt', labelKey: 'nav.automation' },
  'job-hunting': { icon: 'fa-solid fa-briefcase', labelKey: 'nav.jobHunting' },
  'task-queue': { icon: 'fa-solid fa-list-check', labelKey: 'nav.taskQueue' },
  usage: { icon: 'fa-solid fa-chart-pie', labelKey: 'nav.usage' },
  admin: { icon: 'fa-solid fa-shield-halved', labelKey: 'nav.admin' },
  personal: { icon: 'fa-solid fa-user-astronaut', labelKey: 'nav.personal' },
  // U3 批2 记忆管理面板 2026-09-16
  memory: { icon: 'fa-solid fa-brain', labelKey: 'nav.memory' },
  settings: { icon: 'fa-solid fa-gear', labelKey: 'nav.settings' },
}

/** 侧边栏分组定义 */
interface NavSection {
  labelKey: string
  items: ActiveModule[]
}

/** 侧边栏最终形态：2026-09-09 从 17 项收敛，2026-09-16 复核为
 * **11 个分组项 + footer 设置 = 12 个 `button.nav-item`**（改这里请同步
 * e2e/smoke.spec.ts 的 toHaveCount 断言与 tests/module-route-coverage.test.ts）。
 * 移除（仅隐藏，代码与组件保留）：dashboard / personal / notification /
 * job-hunting 保留但调整分组 → 通知并入收件箱语义（inbox），admin 组整组移除。
 * 说明：dashboard / calendar / notification / task-queue / usage / admin /
 * personal 已从导航隐藏；ocr / system-monitor 本就不在 SECTIONS 中，无需处理。
 * tools 父项点击路由到 translate（见 MERGED_DEFAULTS），其下翻译/双端传送/信息
 * 三个工具 tab 由 tools 子模块承载，不展开为独立导航项。
 *
 * ⚠️「仅隐藏」的语义是**只从侧边栏摘掉入口**，模块本身仍必须可达——dashboard 在
 * 2026-09-09 收敛时被连带删掉了 ContentRouter 的 case（17ecc90），而 store 启动
 * 默认值仍是 activeModule='dashboard'，于是启动落到 default 分支渲染成聊天页。
 * 2026-09-16 已补回路由；隐藏 ≠ 删除路由，这是两条独立的改动。 */
const SECTIONS: NavSection[] = [
  {
    labelKey: 'nav.groupCore',
    items: ['chat', 'notes', 'todo'],
  },
  {
    labelKey: 'nav.groupKnowledge',
    items: ['knowledge-network', 'learning'],
  },
  {
    labelKey: 'nav.groupIntelligence',
    // job-hunting：后端 job_hunter.* 26 个方法 + JobHunterModule 已就绪，
    // 此前仅缺 SECTIONS 入口导致侧边栏不渲染（2026-08-17 补）
    items: ['agent', 'automation', 'inbox', 'job-hunting', 'memory'],
  },
  {
    labelKey: 'nav.groupTools',
    // tools 父项经 MERGED_DEFAULTS 路由到 translate，其下
    // 翻译/双端传送/信息三个工具 tab 由子模块承载，不展开
    items: ['tools'],
  },
]

/** 合并父模块 → 默认子模块（点击父模块时路由到默认子模块） */
const MERGED_DEFAULTS: Partial<Record<ActiveModule, ActiveModule>> = {
  learning: 'wordbank',
  'knowledge-network': 'knowledge-graph',
  tools: 'translate',
}

/** 2026-09-01 UX 重构（用户反馈）：移除「更多」按钮。
 * 原方案把非核心模块收进「更多」折叠区，实测带来两个问题：
 * 1) 功能入口多一次点击且不可见，用户不知道模块藏在哪；
 * 2) 「更多」本身占据了导航位却不含任何功能。
 * 现改为全量平铺：4 个分组共 10 项 + footer 设置项，带分组标题可扫读，
 * 与成熟 Agent 产品（ChatGPT/Claude/Linear）侧边栏形态一致，超长时自然滚动。 */

const NavSidebar: React.FC = () => {
  const activeModule = useStore(s => s.activeModule)
  const setActiveModule = useStore(s => s.setActiveModule)
  const navCollapsed = useStore(s => s.navCollapsed)
  const moduleAvailability = useStore(s => s.moduleAvailability)
  const setModuleAvailability = useStore(s => s.setModuleAvailability)
  const notesCount = useStore(s => s.floatingNotes?.length ?? 0)
  const todoCount = useStore(s => s.todos?.filter(t => !t.done).length ?? 0)
  const preferredModelName = useStore(s => s.preferredModelName)
  const { t } = useTranslation()

  // 实时后端连接状态（驱动底部环境胶囊）
  const [backendConnected, setBackendConnected] = useState(false)
  useEffect(() => {
    const client = getWsClient()
    setBackendConnected(client.status === 'connected')
    const unsub = client.onStatusChange((s) => setBackendConnected(s === 'connected'))
    return unsub
  }, [])

  // S2-3 死信红角标：自动化入口在 task_queue.stats.dead_letter > 0 时常驻角标。
  // 数据源复用既有 task_queue.stats RPC（TaskQueueModule 同款），不新增后端接口。
  // 轮询刷新（60s），拉取失败静默保留旧值 —— 角标属非关键提示，不打扰用户。
  const [deadLetterCount, setDeadLetterCount] = useState(0)
  useEffect(() => {
    if (!backendConnected) return
    let cancelled = false
    const refresh = async () => {
      try {
        const res = await getWsClient().call<{ success?: boolean; stats?: Record<string, number> }>(
          'task_queue.stats',
          {},
        )
        if (!cancelled) setDeadLetterCount(res?.stats?.dead_letter ?? 0)
      } catch {
        /* 后端偶发失败时维持上一次计数 */
      }
    }
    void refresh()
    const timer = setInterval(refresh, 60_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [backendConnected])

  // U4 未配置模块折叠：拉取后端 modules.status，写入会话级 store。
  // 契约：{ success:true, modules:[{ module, configured, hasData }] }。
  // 信封失败（success:false / 连接异常 / 后端未升级）→ 置 null，全部按可用处理，不误伤。
  // 轮询节奏与 deadLetterCount 一致（60s），失败静默。
  useEffect(() => {
    if (!backendConnected) return
    let cancelled = false
    const refresh = async () => {
      try {
        const res = await getWsClient().call<{
          success?: boolean
          modules?: { module?: string; configured?: boolean; hasData?: boolean }[]
        }>('modules.status', {})
        if (cancelled) return
        if (!res?.success || !Array.isArray(res.modules)) {
          setModuleAvailability(null)
          return
        }
        const map: Record<string, { configured: boolean; hasData: boolean }> = {}
        for (const m of res.modules) {
          if (!m || typeof m.module !== 'string') continue
          map[m.module] = { configured: m.configured !== false, hasData: m.hasData === true }
        }
        setModuleAvailability(map)
      } catch {
        /* 后端偶发失败/未升级：未知状态 = 全可用 */
        if (!cancelled) setModuleAvailability(null)
      }
    }
    void refresh()
    const timer = setInterval(refresh, 60_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [backendConnected, setModuleAvailability])

  /** 模块是否被后端判定为「已注册但未配置」→ 隐藏导航项。
   * 不在列表 / availability=null（后端未升级）→ 显示。 */
  const isModuleHidden = (key: ActiveModule): boolean =>
    moduleAvailability?.[key]?.configured === false

  const isItemActive = (key: ActiveModule): boolean => {
    if (activeModule === key) return true
    return isSubModuleOf(key, activeModule)
  }

  const handleItemClick = (key: ActiveModule) => {
    const target = MERGED_DEFAULTS[key] || key
    setActiveModule(target)
  }

  const getBadge = (key: ActiveModule): number | null => {
    if (key === 'notes') return notesCount > 0 ? notesCount : null
    if (key === 'todo') return todoCount > 0 ? todoCount : null
    return null
  }

  const modelLabel = preferredModelName || 'DeepSeek V3'

  return (
    <aside
      data-testid="nav-sidebar"
      className={`nav-sidebar flex flex-col flex-shrink-0 border-r select-none ${
        navCollapsed ? 'nav-sidebar-collapsed' : ''
      }`}
      style={{
        borderColor: 'var(--panel-border)',
        background: 'var(--sidebar-bg, var(--panel-bg-solid))',
      }}
    >
      {/* Header: logo only */}
      <div className="nav-header">
        <div className="nav-logo" onClick={() => setActiveModule('chat')}>
          <Icon icon="fa-solid fa-brain" />
          <span className="nav-logo-text">思忆集</span>
        </div>
      </div>

      {/* Module sections — 全量平铺，分组标题提供结构（2026-09-01 移除「更多」折叠区） */}
      <nav className="nav-sections">
        {SECTIONS.map((section) => {
          // U4：先算出可见项（configured=false 的模块隐藏）；整组空则连组标题一并隐藏。
          const visibleItems = section.items.filter((key) => !isModuleHidden(key))
          if (visibleItems.length === 0) return null
          return (
          <div key={section.labelKey} className="nav-section">
            <div className="nav-section-label">{t(section.labelKey)}</div>
            {visibleItems.map((key) => {
              const meta = MODULE_META[key]
              if (!meta) return null
              const active = isItemActive(key)
              const badge = getBadge(key)
              const deadBadge = key === 'automation' && deadLetterCount > 0
              return (
                <button
                  key={key}
                  onClick={() => handleItemClick(key)}
                  className={`nav-item${active ? ' active' : ''}`}
                  aria-current={active ? 'page' : undefined}
                  title={deadBadge ? t('nav.deadLetterBadge', { count: deadLetterCount }) : t(meta.labelKey)}
                >
                  <Icon icon={`${meta.icon} nav-item-icon`} />
                  <span className="nav-item-label">{t(meta.labelKey)}</span>
                  {deadBadge && (
                    <span
                      className="nav-badge nav-badge--danger"
                      data-testid="nav-deadletter-badge"
                      onClick={(e) => {
                        // 「点击查看」：跳转任务队列的死信面板，不触发本项默认导航
                        e.stopPropagation()
                        setActiveModule('task-queue')
                      }}
                    >
                      {deadLetterCount}
                    </span>
                  )}
                  {badge !== null && (
                    <span className="nav-badge">{badge}</span>
                  )}
                  {active && (
                    <motion.span
                      layoutId="nav-active-indicator"
                      className="nav-indicator"
                      transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                    />
                  )}
                </button>
              )
            })}
          </div>
          )
        })}
      </nav>

      {/* Footer: settings + model indicator */}
      <div className="nav-footer">
        <button
          onClick={() => setActiveModule('settings')}
          className={`nav-item${activeModule === 'settings' ? ' active' : ''}`}
          aria-current={activeModule === 'settings' ? 'page' : undefined}
        >
          <Icon icon={`fa-solid fa-gear nav-item-icon`} />
          <span className="nav-item-label">{t('nav.settings')}</span>
          {activeModule === 'settings' && (
            <motion.span
              layoutId="nav-active-indicator"
              className="nav-indicator"
              transition={{ type: 'spring', stiffness: 420, damping: 34 }}
            />
          )}
        </button>
        <div
          className="model-indicator"
          onClick={() => setActiveModule('agent')}
          title={`${modelLabel} · ${t('nav.agent')}`}
        >
          {/* 收敛：连接状态只在聊天页展示，这里仅显示模型名（固定中性色调） */}
          {backendConnected ? (
            <StatusPill
              tone="idle"
              /* 折叠态只显示状态灯，不显示文字（2026-09-01 用户反馈：截断的模型名只剩一个字母，无意义） */
              label={navCollapsed ? '' : modelLabel}
            />
          ) : (
            modelLabel
          )}
        </div>
      </div>
    </aside>
  )
}

/** 判断 child 是否为 parent 的子模块（用于合并项高亮）。
 *
 * 仅保留语义上确实从属于父项的子项，避免「更多」抽屉里点击独立的
 * info / agent / system-monitor 时错误高亮 tools（原实现把它们都挂到
 * tools 下，造成高亮错乱）。
 */
function isSubModuleOf(parent: ActiveModule, child: ActiveModule): boolean {
  if (parent === 'learning') return child === 'wordbank' || child === 'flashcard'
  if (parent === 'knowledge-network')
    return child === 'knowledge-graph' || child === 'knowledge-base' || child === 'backlinks'
  if (parent === 'tools') return child === 'translate' || child === 'transfer'
  return false
}

export default NavSidebar
