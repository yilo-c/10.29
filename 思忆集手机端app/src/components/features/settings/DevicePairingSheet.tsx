import { useState, useRef, useEffect } from 'react'
import { Sheet } from '../../ui'
import { tokenManager } from '../../../lib/tokenManager'
import { haptic } from '../../../lib/haptic'
import { configureWs, configureWsAuthToken } from '../../../lib/rpcClient'

interface DevicePairingSheetProps {
  open: boolean
  onClose: () => void
  onPairingChange: () => void
}

export default function DevicePairingSheet({ open, onClose, onPairingChange }: DevicePairingSheetProps) {
  const [serverUrl, setServerUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false) // 等待桌面端批准
  const [tailscaleStatus, setTailscaleStatus] = useState<{connected: boolean; tailscale_ip: string | null; hostname: string | null} | null>(null)

  const isPaired = tokenManager.isPaired
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => () => stopPolling(), [])

  const handleConnect = async () => {
    let ip = serverUrl.trim()
    if (!ip) return
    ip = ip.replace(/https?:\/\//, '').replace(/\/.*$/, '').replace(/:\d+$/, '')
    haptic.medium()
    setLoading(true)
    setError(null)
    setPending(false)
    const url = `http://${ip}:18765`
    try {
      // 1) 发现（免鉴权）：确认对端是思忆集桌面端
      const discRes = await fetch(`${url}/discover`).catch(() => null)
      if (!discRes || !discRes.ok) throw new Error('discover failed')

      // 2) 发起配对请求（免鉴权）：桌面端需批准
      const pairRes = await fetch(`${url}/pair`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'request',
          device_name: '手机',
          device_id: tokenManager.deviceId ?? `mobile-${Date.now()}`,
        }),
      })
      const pairJson = await pairRes.json().catch(() => ({}))
      if (!pairRes.ok || !pairJson.request_id) throw new Error('pair request failed')
      const requestId: string = pairJson.request_id

      setPending(true)
      setLoading(false)

      // 3) 轮询批准（最多 5 分钟，与服务端过期一致）
      pollRef.current = setInterval(async () => {
        try {
          const stRes = await fetch(`${url}/pair`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'status', request_id: requestId }),
          })
          const stJson = await stRes.json().catch(() => ({}))
          if (stJson.state === 'approved' && stJson.token) {
            stopPolling()
            tokenManager.baseUrl = url
            tokenManager.backendPort = 18765
            tokenManager.apiToken = stJson.token // P1-4: 真实主 token，替换原 'direct'
            tokenManager.deviceName = '手机'
            tokenManager.userId = 'local'
            if (!tokenManager.deviceId) tokenManager.deviceId = `mobile-${Date.now()}`
            tokenManager.isPaired = true
            configureWs(url) // 推送 WS URL + 真实 token（WsClient 会自动 /auth 握手）
            haptic.success()
            setPending(false)
            onPairingChange()
            // 异步探测桌面端 Tailscale 状态（非关键）
            fetch(`${url}/rpc`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${stJson.token}`,
              },
              body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'system.tailscale_status', params: {} }),
            }).then((r) => r.json()).then((res) => {
              if (res?.result) setTailscaleStatus(res.result)
            }).catch(() => {/* 非关键路径，静默失败 */ })
          } else if (stJson.state === 'unknown') {
            stopPolling()
            setPending(false)
            setError('配对请求已失效，请重新连接')
          }
        } catch {
          /* 网络抖动：继续轮询 */
        }
      }, 2000)
    } catch {
      haptic.error()
      setLoading(false)
      setPending(false)
      setError('无法连接到桌面端，请确认后端已启动 (端口 18765) 且网络可达')
    }
  }

  const cancelPending = () => {
    stopPolling()
    setPending(false)
    setLoading(false)
  }

  const handleUnpair = () => {
    haptic.medium()
    stopPolling()
    setPending(false)
    setTailscaleStatus(null)
    tokenManager.apiToken = null
    configureWsAuthToken(null)
    tokenManager.deviceId = null
    tokenManager.baseUrl = null
    tokenManager.userId = null
    tokenManager.deviceName = null
    tokenManager.isPaired = false
    onPairingChange()
  }

  return (
    <Sheet open={open} onClose={onClose} title="设备与服务">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

        {/* Connection status */}
        <div style={{
          padding: '10px 14px', borderRadius: 'var(--radius-md)', fontSize: 'var(--text-footnote)',
          background: isPaired ? 'var(--accent-surface)' : 'var(--bg-tertiary)',
          border: `0.5px solid ${isPaired ? 'var(--accent-border)' : 'var(--border-subtle)'}`,
        }}>
          <div style={{ fontWeight: 600, marginBottom: 2 }}>
            {isPaired ? '✓ 已配对' : '未配对'}
          </div>
          {isPaired && (
            <div style={{ color: 'var(--text-tertiary)', fontSize: 'var(--text-caption-2)' }}>
              {tokenManager.baseUrl}
            </div>
          )}
          {isPaired && tailscaleStatus?.connected && (
            <div style={{ marginTop: 6, display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 'var(--radius-sm)', background: 'var(--accent-surface)', border: '0.5px solid var(--accent-border)', fontSize: 'var(--text-caption-2)', color: 'var(--accent)' }}>
              <span>✓ Tailscale</span>
              <span style={{ color: 'var(--text-tertiary)' }}>{tailscaleStatus.tailscale_ip}</span>
            </div>
          )}
          {isPaired && tailscaleStatus && !tailscaleStatus.connected && (
            <div style={{ marginTop: 6, fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
              ⚠ 桌面端未检测到 Tailscale（当前为局域网/直连模式）
            </div>
          )}
        </div>

        {/* Pairing form */}
        {!isPaired && (
          <>
            <div>
              <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)', marginBottom: 12, lineHeight: 1.5 }}>
                输入桌面端的 IP 或主机名发起配对，在桌面端批准后即可连接。Tailscale 用户可填 MagicDNS 主机名（如 my-desktop）或 100.x.x.x IP，外网也能连。
              </div>
              <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginBottom: 8 }}>
                桌面端 IP / Tailscale 主机名
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <input value={serverUrl} onChange={e => setServerUrl(e.target.value)}
                  placeholder="192.168.3.17 或 100.x.x.x 或 my-desktop"
                  disabled={pending}
                  style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '0.5px solid var(--border-default)', background: 'var(--bg-tertiary)', color: 'var(--text-primary)', fontSize: 'var(--text-footnote)', outline: 'none', boxSizing: 'border-box' }} />
                {!pending ? (
                  <button onClick={handleConnect} disabled={loading || !serverUrl.trim()}
                    style={{ width: '100%', padding: '12px 0', borderRadius: 'var(--radius-md)', border: 'none', background: loading ? 'var(--bg-tertiary)' : 'var(--accent)', color: loading ? 'var(--text-disabled)' : '#fff', fontSize: 'var(--text-body)', fontWeight: 600, cursor: loading ? 'default' : 'pointer' }}>
                    {loading ? '连接中...' : '连接桌面端'}
                  </button>
                ) : (
                  <button onClick={cancelPending}
                    style={{ width: '100%', padding: '12px 0', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-default)', background: 'transparent', color: 'var(--text-secondary)', fontSize: 'var(--text-body)', fontWeight: 600, cursor: 'pointer' }}>
                    等待桌面端批准…（取消）
                  </button>
                )}
              </div>
            </div>
            {error && (
              <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--danger)', textAlign: 'center' }}>{error}</div>
            )}
          </>
        )}

        {/* Unpair */}
        {isPaired && (
          <button onClick={handleUnpair}
            style={{ width: '100%', padding: '12px 0', borderRadius: 'var(--radius-md)', border: '1px solid var(--danger)', background: 'transparent', color: 'var(--danger)', fontSize: 'var(--text-headline)', fontWeight: 600, cursor: 'pointer' }}>
            解除配对
          </button>
        )}
      </div>
    </Sheet>
  )
}
