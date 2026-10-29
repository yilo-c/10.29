import type { StateCreator } from 'zustand'
import type { Store } from '../types'

export interface PermissionRequest {
  id: string
  source: string
  group: string
  actionLabel: string
  targetLabel: string
  risk: 'auto' | 'low' | 'medium' | 'high'
  description?: string
  icon?: string
  timeoutMs?: number
  onAllow: () => void | Promise<void>
  onDeny?: () => void | Promise<void>
}

export interface PermissionMemory {
  group: string
  targetPattern?: string
  decision: 'allow' | 'deny'
  expiresAt?: number
  createdAt: number
}

export interface PermissionLog {
  requestId: string
  group: string
  actionLabel: string
  decision: 'allow' | 'deny' | 'timeout'
  timestamp: number
}

export interface PermissionSlice {
  permissionQueue: PermissionRequest[]
  permissionMemory: PermissionMemory[]
  permissionLog: PermissionLog[]

  requestPermission: (req: Omit<PermissionRequest, 'id'>) => Promise<boolean>
  resolvePermission: (id: string, decision: 'allow' | 'deny') => void
  rememberPermissionDecision: (group: string, decision: 'allow' | 'deny') => void
  forgetPermissionDecision: (group: string) => void
  clearPermissionQueue: () => void
}

let _reqId = 0
function nextId() {
  _reqId += 1
  return `perm-${Date.now()}-${_reqId}`
}

function matchesTarget(target?: string, pattern?: string): boolean {
  if (!pattern || !target) return false
  if (pattern === '*') return true
  if (pattern.endsWith('*') && target.startsWith(pattern.slice(0, -1))) return true
  return target === pattern
}

export const createPermissionSlice: StateCreator<Store, [], [], PermissionSlice> = (set, get, _api) => ({
    permissionQueue: [],
    permissionMemory: [],
    permissionLog: [],

    requestPermission: async (req): Promise<boolean> => {
      const { permissionMemory } = get()

      // auto-risk: always allow silently
      if (req.risk === 'auto') {
        try { await req.onAllow() } catch { /* ignore */ }
        return true
      }

      // Check memory
      const remembered = permissionMemory.find(
        (m: PermissionMemory) =>
          m.group === req.group && matchesTarget(req.targetLabel, m.targetPattern)
      )
      if (remembered) {
        if (remembered.expiresAt && remembered.expiresAt < Date.now()) {
          // expired, remove and re-ask
          set((s: PermissionSlice) => ({
            permissionMemory: s.permissionMemory.filter((m: PermissionMemory) => m !== remembered),
          }))
        } else if (remembered.decision === 'allow') {
          try { await req.onAllow() } catch { /* ignore */ }
          return true
        } else {
          await req.onDeny?.()
          return false
        }
      }

      // low-risk: execute immediately, log as auto-allowed
      if (req.risk === 'low') {
        try { await req.onAllow() } catch { /* ignore */ }
        return true
      }

      // medium/high: enqueue
      return new Promise<boolean>((resolve) => {
        const id = nextId()
        const entry: PermissionRequest = {
          ...req,
          id,
          onAllow: async () => { await req.onAllow(); resolve(true) },
          onDeny: async () => { await req.onDeny?.(); resolve(false) },
        }

        set((s: PermissionSlice) => ({
          permissionQueue: [...s.permissionQueue, entry],
        }))

        // Auto-timeout: deny after timeoutMs
        if (req.timeoutMs && req.timeoutMs > 0) {
          setTimeout(() => {
            const state = get() as PermissionSlice
            if (state.permissionQueue.find((r: PermissionRequest) => r.id === id)) {
              set((s: PermissionSlice) => ({
                permissionQueue: s.permissionQueue.filter((r: PermissionRequest) => r.id !== id),
                permissionLog: [
                  ...s.permissionLog.slice(-99),
                  { requestId: id, group: req.group, actionLabel: req.actionLabel, decision: 'timeout', timestamp: Date.now() },
                ],
              }))
              resolve(false)
            }
          }, req.timeoutMs)
        }
      })
    },

    resolvePermission: (id, decision) => {
      const state = get() as PermissionSlice
      const req = state.permissionQueue.find((r: PermissionRequest) => r.id === id)
      if (!req) return

      set((s: PermissionSlice) => ({
        permissionQueue: s.permissionQueue.filter((r: PermissionRequest) => r.id !== id),
        permissionLog: [
          ...s.permissionLog.slice(-99),
          { requestId: id, group: req.group, actionLabel: req.actionLabel, decision, timestamp: Date.now() },
        ],
      }))

      if (decision === 'allow') {
        req.onAllow()
      } else {
        req.onDeny?.()
      }
    },

    rememberPermissionDecision: (group, decision) => {
      set((s: PermissionSlice) => ({
        permissionMemory: [
          ...s.permissionMemory.filter((m: PermissionMemory) => m.group !== group),
          { group, decision, createdAt: Date.now() },
        ],
      }))
    },

    forgetPermissionDecision: (group) => {
      set((s: PermissionSlice) => ({
        permissionMemory: s.permissionMemory.filter((m: PermissionMemory) => m.group !== group),
      }))
    },

    clearPermissionQueue: () => {
      set({ permissionQueue: [] })
    },
  }
)
