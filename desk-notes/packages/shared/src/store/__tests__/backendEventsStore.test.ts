/**
 * A1（前后端对齐规划）：backendEventsStore 回归测试。
 *
 * 覆盖六类后端事件的 store 落点 + installBackendEvents 的注册/卸载。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import type { ServerPushEvent, WsClient } from '../../ws-client'
import {
  useBackendEventsStore,
  installBackendEvents,
  handleTaskResumable,
  handleSyncConflict,
  handleSandboxTimeout,
  handleJobAlert,
  handleProactiveTriggered,
  handleExecStep,
} from '../backendEventsStore'

function ev(event_type: string, payload: Record<string, unknown>): ServerPushEvent {
  return { type: 'event', event_type, payload }
}

function resetStore(): void {
  useBackendEventsStore.setState({
    resumableTasks: [],
    resumableDismissed: false,
    syncConflicts: [],
    sandboxTimeouts: [],
    jobAlerts: [],
    morningBrief: null,
    recentExecSteps: [],
  })
}

describe('backendEventsStore', () => {
  beforeEach(resetStore)

  it('task.resumable 写入可续跑任务并重置关闭标记', () => {
    useBackendEventsStore.setState({ resumableDismissed: true })
    handleTaskResumable(ev('task.resumable', {
      count: 2,
      tasks: [
        { task_id: 't1', state: 'executing' },
        { task_id: 't2', state: 'paused' },
        { bogus: true }, // 无 task_id 应被过滤
      ],
    }))
    const s = useBackendEventsStore.getState()
    expect(s.resumableTasks).toHaveLength(2)
    expect(s.resumableTasks[0]).toEqual({ task_id: 't1', state: 'executing' })
    expect(s.resumableDismissed).toBe(false)
  })

  it('sync.conflict 按 resource+id 去重且新条目置顶', () => {
    handleSyncConflict(ev('sync.conflict', { resource: 'note', id: 'n1', title: '旧' }))
    handleSyncConflict(ev('sync.conflict', { resource: 'note', id: 'n2', title: '另一条' }))
    handleSyncConflict(ev('sync.conflict', { resource: 'note', id: 'n1', title: '新' }))
    const conflicts = useBackendEventsStore.getState().syncConflicts
    expect(conflicts).toHaveLength(2)
    expect(conflicts[0].id).toBe('n1')
    expect(conflicts[0].title).toBe('新')
  })

  it('sync.conflict 无 id 时忽略', () => {
    handleSyncConflict(ev('sync.conflict', { resource: 'note', title: '无 id' }))
    expect(useBackendEventsStore.getState().syncConflicts).toHaveLength(0)
  })

  it('sandbox.timeout 与 timeout_warning 分类落点并按 pending_id 去重', () => {
    handleSandboxTimeout(ev('sandbox.timeout_warning', { pending_id: 'p1', action_id: 'file.write', remaining_seconds: 55 }), 'warning')
    handleSandboxTimeout(ev('sandbox.timeout', { pending_id: 'p1', action_id: 'file.write', policy: 'reject', status: 'timed_out_rejected' }), 'timeout')
    const timeouts = useBackendEventsStore.getState().sandboxTimeouts
    expect(timeouts).toHaveLength(1)
    expect(timeouts[0].kind).toBe('timeout')
    expect(timeouts[0].policy).toBe('reject')
  })

  it('cron.job_alert 按 job_id 替换旧告警', () => {
    handleJobAlert(ev('cron.job_alert', { job_id: 'j1', job_name: '任务A', error: '第一次', consecutive_failures: 3 }))
    handleJobAlert(ev('cron.job_alert', { job_id: 'j1', job_name: '任务A', error: '第二次', consecutive_failures: 4 }))
    const alerts = useBackendEventsStore.getState().jobAlerts
    expect(alerts).toHaveLength(1)
    expect(alerts[0].error).toBe('第二次')
    expect(alerts[0].consecutive_failures).toBe(4)
  })

  it('proactive.triggered 仅 morning_brief 触发晨报，其他 trigger 忽略', () => {
    handleProactiveTriggered(ev('proactive.triggered', { trigger: 'idle', title: '无关' }))
    expect(useBackendEventsStore.getState().morningBrief).toBeNull()

    handleProactiveTriggered(ev('proactive.triggered', {
      trigger: 'morning_brief',
      title: '今日晨报',
      content: '昨日完成 3 项任务',
      metadata: { date: '2026-08-09', inbox_id: 42 },
    }))
    const brief = useBackendEventsStore.getState().morningBrief
    expect(brief).not.toBeNull()
    expect(brief?.title).toBe('今日晨报')
    expect(brief?.date).toBe('2026-08-09')
    expect(brief?.inbox_id).toBe('42')
  })

  it('exec.step 写入环形缓冲且新步骤置顶', () => {
    handleExecStep(ev('exec.step', { task_id: 'chat-1', step_type: 'tool_call', tool: 'web.search', summary: 'q=ai' }))
    handleExecStep(ev('exec.step', { task_id: 'chat-1', step_type: 'tool_result', tool: 'web.search', summary: 'ok' }))
    const steps = useBackendEventsStore.getState().recentExecSteps
    expect(steps).toHaveLength(2)
    expect(steps[0].step_type).toBe('tool_result')
  })

  it('exec.step 无 task_id 时忽略', () => {
    handleExecStep(ev('exec.step', { step_type: 'step' }))
    expect(useBackendEventsStore.getState().recentExecSteps).toHaveLength(0)
  })

  it('dismiss/clear 动作正确', () => {
    handleTaskResumable(ev('task.resumable', { tasks: [{ task_id: 't1', state: 'x' }] }))
    handleSyncConflict(ev('sync.conflict', { resource: 'todo', id: 'd1' }))
    handleJobAlert(ev('cron.job_alert', { job_id: 'j9', job_name: 'n', error: 'e', consecutive_failures: 3 }))
    handleProactiveTriggered(ev('proactive.triggered', { trigger: 'morning_brief', title: 't', content: 'c', metadata: {} }))

    const st = useBackendEventsStore.getState()
    st.dismissResumable()
    st.removeSyncConflict('todo', 'd1')
    st.dismissJobAlert('j9')
    st.clearMorningBrief()

    const after = useBackendEventsStore.getState()
    expect(after.resumableDismissed).toBe(true)
    expect(after.syncConflicts).toHaveLength(0)
    expect(after.jobAlerts).toHaveLength(0)
    expect(after.morningBrief).toBeNull()
  })
})

describe('installBackendEvents', () => {
  beforeEach(resetStore)

  /** 最小 fake WsClient：仅实现 on()，记录 handler 并支持按类型派发。 */
  function makeFakeClient() {
    const handlers = new Map<string, Array<(e: ServerPushEvent) => void>>()
    const client = {
      on(eventType: string, handler: (e: ServerPushEvent) => void) {
        if (!handlers.has(eventType)) handlers.set(eventType, [])
        handlers.get(eventType)!.push(handler)
        return () => {
          const arr = handlers.get(eventType)
          if (arr) handlers.set(eventType, arr.filter((h) => h !== handler))
        }
      },
    } as unknown as WsClient & { _emit(e: ServerPushEvent): void }
    ;(client as { _emit(e: ServerPushEvent): void })._emit = (e: ServerPushEvent) => {
      for (const h of handlers.get(e.event_type) ?? []) h(e)
      // B3：installBackendEvents 用通配符订阅，模拟 WsClient 的 '*' 分发
      for (const h of handlers.get('*') ?? []) h(e)
    }
    return client as WsClient & { _emit(e: ServerPushEvent): void }
  }

  it('安装后六类事件全部路由到 store', () => {
    const client = makeFakeClient()
    installBackendEvents(client)

    client._emit(ev('task.resumable', { tasks: [{ task_id: 'r1', state: 's' }] }))
    client._emit(ev('sync.conflict', { resource: 'note', id: 'c1' }))
    client._emit(ev('sandbox.timeout', { pending_id: 'p1', action_id: 'a' }))
    client._emit(ev('cron.job_alert', { job_id: 'j1', job_name: 'x', error: 'e', consecutive_failures: 3 }))
    client._emit(ev('proactive.triggered', { trigger: 'morning_brief', title: '晨', content: 'c', metadata: {} }))
    client._emit(ev('exec.step', { task_id: 'chat-9', step_type: 'step' }))

    const s = useBackendEventsStore.getState()
    expect(s.resumableTasks).toHaveLength(1)
    expect(s.syncConflicts).toHaveLength(1)
    expect(s.sandboxTimeouts).toHaveLength(1)
    expect(s.jobAlerts).toHaveLength(1)
    expect(s.morningBrief).not.toBeNull()
    expect(s.recentExecSteps).toHaveLength(1)
  })

  it('卸载后事件不再落点', () => {
    const client = makeFakeClient()
    const uninstall = installBackendEvents(client)
    uninstall()

    client._emit(ev('sync.conflict', { resource: 'note', id: 'zz' }))
    expect(useBackendEventsStore.getState().syncConflicts).toHaveLength(0)
  })
})
