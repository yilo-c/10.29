import { useEffect, useState } from 'react'
import { getWsClient } from '../utils/ai'

/**
 * 订阅 WebSocket 连接状态（connected 布尔）。
 *
 * 断线重连后重新变为 true，从而驱动依赖它的 effect 重新拉取数据，
 * 修复 Info/Inbox 等卡片断线后永久空白的问题。
 */
export function useWsConnected(): boolean {
  const [connected, setConnected] = useState<boolean>(() => getWsClient().status === 'connected')

  useEffect(() => {
    const client = getWsClient()
    // 订阅前先同步一次当前状态，避免错过订阅窗口内的状态变化。
    setConnected(client.status === 'connected')
    return client.onStatusChange((s) => setConnected(s === 'connected'))
  }, [])

  return connected
}
