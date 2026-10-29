import { useState, useEffect, useRef, useCallback } from 'react'
import { useBackendEvents } from '../FloatingBubble/useBackendEvents'

export interface ChainNodeProgress {
  node_id: string
  description: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  risk_level?: string
  error?: string
}

export interface ChainState {
  chain_id: string
  goal: string
  total_steps: number
  nodes: ChainNodeProgress[]
  status: 'running' | 'completed' | 'failed'
  completed: number
  failed: number
}

export interface WorkflowState {
  label: string
  total: number
  current: number
  status: 'running' | 'completed' | 'failed'
}

export function useChainProgress() {
  const { lastEvent, consumeEvents } = useBackendEvents()
  const [chain, setChain] = useState<ChainState | null>(null)
  const chainRef = useRef<ChainState | null>(null)
  const [workflow, setWorkflow] = useState<WorkflowState | null>(null)
  const workflowRef = useRef<WorkflowState | null>(null)

  // Handle chain.started
  useEffect(() => {
    const ev = lastEvent('agent.chain.started')
    if (!ev?.payload) return
    const p = ev.payload as { chain_id: string; goal: string; total_steps: number }
    const state: ChainState = {
      chain_id: p.chain_id,
      goal: p.goal,
      total_steps: p.total_steps,
      nodes: [],
      status: 'running',
      completed: 0,
      failed: 0,
    }
    chainRef.current = state
    setChain({ ...state })
  }, [lastEvent])

  // Handle node.started
  useEffect(() => {
    const ev = lastEvent('agent.chain.node.started')
    if (!ev?.payload || !chainRef.current) return
    const p = ev.payload as { chain_id: string; node_id: string; description: string; risk_level: string }
    if (p.chain_id !== chainRef.current.chain_id) return
    const newNode: ChainNodeProgress = {
      node_id: p.node_id,
      description: p.description,
      status: 'running',
      risk_level: p.risk_level,
    }
    chainRef.current = {
      ...chainRef.current,
      nodes: [...chainRef.current.nodes.filter(n => n.node_id !== p.node_id), newNode],
      status: 'running',
    }
    setChain({ ...chainRef.current })
  }, [lastEvent])

  // Handle node.completed
  useEffect(() => {
    const ev = lastEvent('agent.chain.node.completed')
    if (!ev?.payload || !chainRef.current) return
    const p = ev.payload as { chain_id: string; node_id: string; description: string }
    if (p.chain_id !== chainRef.current.chain_id) return
    const newNode: ChainNodeProgress = {
      node_id: p.node_id,
      description: p.description,
      status: 'completed',
    }
    chainRef.current = {
      ...chainRef.current,
      nodes: [...chainRef.current.nodes.filter(n => n.node_id !== p.node_id), newNode],
      completed: chainRef.current.completed + 1,
    }
    setChain({ ...chainRef.current })
  }, [lastEvent])

  // Handle node.failed
  useEffect(() => {
    const ev = lastEvent('agent.chain.node.failed')
    if (!ev?.payload || !chainRef.current) return
    const p = ev.payload as { chain_id: string; node_id: string; description: string; error: string }
    if (p.chain_id !== chainRef.current.chain_id) return
    const newNode: ChainNodeProgress = {
      node_id: p.node_id,
      description: p.description,
      status: 'failed',
      error: p.error,
    }
    chainRef.current = {
      ...chainRef.current,
      nodes: [...chainRef.current.nodes.filter(n => n.node_id !== p.node_id), newNode],
      failed: chainRef.current.failed + 1,
    }
    setChain({ ...chainRef.current })
  }, [lastEvent])

  // Handle chain.completed
  useEffect(() => {
    const ev = lastEvent('agent.chain.completed')
    if (!ev?.payload || !chainRef.current) return
    const p = ev.payload as { chain_id: string; status: string; completed: number; failed: number }
    if (p.chain_id !== chainRef.current.chain_id) return
    chainRef.current = {
      ...chainRef.current,
      status: p.status as ChainState['status'],
      completed: p.completed,
      failed: p.failed,
    }
    setChain({ ...chainRef.current })
  }, [lastEvent])

  // Handle workflow.progress
  useEffect(() => {
    const ev = lastEvent('workflow.progress')
    if (!ev?.payload) return
    const p = ev.payload as { step: number; total: number; label: string; status: string }
    const state: WorkflowState = {
      label: p.label,
      total: p.total,
      current: p.step,
      status: p.status === 'failed' ? 'failed' : p.status === 'completed' ? 'completed' : 'running',
    }
    workflowRef.current = state
    setWorkflow({ ...state })
  }, [lastEvent])

  // Handle workflow.completed
  useEffect(() => {
    const ev = lastEvent('workflow.completed')
    if (!ev?.payload || !workflowRef.current) return
    workflowRef.current = {
      ...workflowRef.current,
      status: 'completed',
      current: workflowRef.current.total,
    }
    setWorkflow({ ...workflowRef.current })
  }, [lastEvent])

  // Clear on start events
  useEffect(() => { consumeEvents('agent.chain.started') }, [])

  const clearChain = useCallback(() => {
    chainRef.current = null
    setChain(null)
  }, [])

  const clearWorkflow = useCallback(() => {
    workflowRef.current = null
    setWorkflow(null)
  }, [])

  return { chain, setChain, clearChain, workflow, clearWorkflow }
}
