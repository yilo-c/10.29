/**
 * B1（前后端对齐规划）：backgroundJobsStore 测试。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  useBackgroundJobsStore,
  runTrackedJob,
  rehydrateBackgroundJobs,
  selectRunningJobs,
} from '../backgroundJobsStore'

function resetStore() {
  useBackgroundJobsStore.setState({ jobs: {} })
}

describe('backgroundJobsStore', () => {
  beforeEach(resetStore)

  it('start/finish/fail 状态流转', () => {
    const s = () => useBackgroundJobsStore.getState()
    s().startJob('j1', 'translate', '翻译一段')
    expect(s().jobs['j1'].status).toBe('running')

    s().finishJob('j1', { ok: true })
    expect(s().jobs['j1'].status).toBe('done')
    expect(s().jobs['j1'].result).toEqual({ ok: true })
    expect(s().jobs['j1'].finishedAt).toBeGreaterThan(0)

    s().startJob('j2', 'ocr', 'OCR')
    s().failJob('j2', 'boom')
    expect(s().jobs['j2'].status).toBe('error')
    expect(s().jobs['j2'].error).toBe('boom')
  })

  it('finishJob/failJob 对未知 id 幂等无副作用', () => {
    const s = () => useBackgroundJobsStore.getState()
    s().finishJob('ghost', {})
    s().failJob('ghost', 'x')
    expect(Object.keys(s().jobs)).toHaveLength(0)
  })

  it('clearJob 与 clearFinished', () => {
    const s = () => useBackgroundJobsStore.getState()
    s().startJob('a', 'generic', 'a')
    s().startJob('b', 'generic', 'b')
    s().finishJob('b')
    s().clearFinished()
    expect(s().jobs['a']).toBeTruthy()
    expect(s().jobs['b']).toBeUndefined()

    s().clearJob('a')
    expect(Object.keys(s().jobs)).toHaveLength(0)
  })

  it('runTrackedJob 成功落终态并返回结果', async () => {
    const result = await runTrackedJob({
      type: 'url_learn',
      label: 'https://example.com',
      run: async () => ({ title: 'T' }),
    })
    expect(result).toEqual({ title: 'T' })
    const jobs = useBackgroundJobsStore.getState().jobs
    const job = Object.values(jobs)[0]
    expect(job.status).toBe('done')
    expect(job.type).toBe('url_learn')
    expect(job.result).toEqual({ title: 'T' })
  })

  it('runTrackedJob 失败落 error 并继续抛出', async () => {
    await expect(runTrackedJob({
      type: 'translate',
      label: 'x',
      run: async () => { throw new Error('网络错误') },
    })).rejects.toThrow('网络错误')
    const jobs = useBackgroundJobsStore.getState().jobs
    const job = Object.values(jobs)[0]
    expect(job.status).toBe('error')
    expect(job.error).toBe('网络错误')
  })

  it('selectRunningJobs 只返回运行中', () => {
    const s = () => useBackgroundJobsStore.getState()
    s().startJob('r1', 'generic', 'r1')
    s().startJob('r2', 'generic', 'r2')
    s().finishJob('r2')
    const running = selectRunningJobs(s().jobs)
    expect(running).toHaveLength(1)
    expect(running[0].id).toBe('r1')
  })
})

describe('rehydrateBackgroundJobs', () => {
  beforeEach(resetStore)

  it('恢复后端在途任务并去重', async () => {
    const call = vi.fn().mockResolvedValue([
      { task_id: 't1', goal: '写周报', state: 'executing' },
      { task_id: 't2', state: 'paused' },
      { bogus: true },
    ])

    const n = await rehydrateBackgroundJobs(call)
    expect(n).toBe(2)
    const jobs = useBackgroundJobsStore.getState().jobs
    expect(jobs['backend-t1'].label).toBe('写周报')
    expect(jobs['backend-t1'].status).toBe('running')
    expect(jobs['backend-t2'].label).toBe('t2')

    // 幂等：再次恢复不重复计数
    const n2 = await rehydrateBackgroundJobs(call)
    expect(n2).toBe(0)
  })

  it('后端不可达时静默返回 0', async () => {
    const call = vi.fn().mockRejectedValue(new Error('Not connected'))
    const n = await rehydrateBackgroundJobs(call)
    expect(n).toBe(0)
    expect(Object.keys(useBackgroundJobsStore.getState().jobs)).toHaveLength(0)
  })

  it('空列表返回 0', async () => {
    const call = vi.fn().mockResolvedValue([])
    expect(await rehydrateBackgroundJobs(call)).toBe(0)
  })
})
