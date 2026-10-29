import { useEffect } from 'react'
import { getWsClient } from '../utils/ai'

/**
 * Desktop notifications hook — listens for backend events and shows
 * native OS notifications via the Web Notification API (Electron bridges it).
 */
export function useDesktopNotifications() {
  useEffect(() => {
    const client = getWsClient()
    if (!client) return

    // Request permission (will show a single system-level prompt)
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }

    const unsubBrief = client.on('info.collected', (event) => {
      const payload = event.payload as { brief?: string } | undefined
      if (!payload?.brief) return
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('📰 资讯简报已生成', {
          body: '点击查看今日最新技术资讯摘要',
          silent: false,
        })
      }
    })

    const unsubSuggestion = client.on('proactive.suggestion', (event) => {
      const payload = event.payload as { title?: string; description?: string } | undefined
      if (!payload?.title) return
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification('💡 ' + payload.title, {
          body: payload.description?.slice(0, 100) || '',
          silent: true,
        })
      }
    })

    // P2.9 通知推送通道：后端 DesktopToastChannel 仅对 high/urgent 优先级 emit
    // notification.toast，这里用 Web Notification API 弹系统级 toast。
    const unsubToast = client.on('notification.toast', (event) => {
      const payload = event.payload as { title?: string; body?: string; priority?: string } | undefined
      if (!payload?.title) return
      if ('Notification' in window && Notification.permission === 'granted') {
        try {
          new Notification('🔔 ' + payload.title, {
            body: payload.body?.slice(0, 100) || '',
            silent: payload.priority !== 'urgent',
          })
        } catch {
          // Notification 构造在浏览器/Electron 通知服务异常时可能抛错；
          // 事件总线已捕获 handler 异常，这里避免单条 toast 失败扩散。
        }
      }
    })

    const unsubReconnect = client.onStatusChange((status) => {
      if (status === 'connected') {
        // Backend just came online — brief notification for awareness
        if ('Notification' in window && Notification.permission === 'granted') {
          new Notification('思忆集后端已连接', { body: '所有功能已恢复', silent: true })
        }
      }
    })

    return () => {
      unsubBrief()
      unsubSuggestion()
      unsubToast()
      unsubReconnect()
    }
  }, [])
}
