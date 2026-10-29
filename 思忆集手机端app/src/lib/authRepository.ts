import { ApiClient } from './apiClient'
import { tokenManager } from './tokenManager'
import { swallowError } from './swallowError'

export interface PairingState {
  isPaired: boolean
  userId?: string
  deviceId?: string
  error?: string
}

export class AuthRepository {
  constructor(private api: ApiClient) {}

  async validateAndPair(token: string, serverUrl: string, deviceName: string): Promise<PairingState> {
    const url = serverUrl.replace(/\/+$/, '')
    this.api.configure(url, token)

    // Step 1: validate token
    let me: { user_id: string }
    try {
      me = await this.api.getMe()
    } catch (err) {
      this.api.configure('', null)
      const msg = err instanceof Error ? err.message : '连接失败'
      return { isPaired: false, error: msg }
    }

    // Step 2: register device
    let deviceId = ''
    try {
      const device = await this.api.registerDevice(deviceName)
      deviceId = device.id
    } catch (e) {
      // Token valid but device registration failed — partial pairing
      swallowError('authRepository:33', e)
    }

    // Persist pairing
    tokenManager.apiToken = token
    tokenManager.baseUrl = url
    tokenManager.userId = me.user_id
    tokenManager.deviceId = deviceId || null
    tokenManager.deviceName = deviceName
    tokenManager.isPaired = true

    return { isPaired: true, userId: me.user_id, deviceId: deviceId || undefined }
  }

  async checkConnection(serverUrl: string): Promise<boolean> {
    const tmp = new ApiClient()
    tmp.configure(serverUrl, null)
    try {
      const res = await tmp.health()
      return res.status === 'ok' || res.status === 'healthy'
    } catch {
      return false
    }
  }

  async revokeCurrentToken(): Promise<void> {
    if (!tokenManager.apiToken) return
    try {
      await this.api.revokeToken()
    } catch (e) {
      // Ignore revocation errors — proceed with cleanup
      swallowError('authRepository:63', e)
    }
  }

  clearPairing(): void {
    this.api.setToken(null)
    tokenManager.apiToken = null
    tokenManager.deviceId = null
    tokenManager.baseUrl = null
    tokenManager.userId = null
    tokenManager.deviceName = null
    tokenManager.isPaired = false
  }
}
