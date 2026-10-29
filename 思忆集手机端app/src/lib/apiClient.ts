/* API response types */

export interface HealthResponse {
  status: string
  version?: string
}

export interface MeResponse {
  user_id: string
  device_id?: string
  scopes?: string[]
}

export interface DeviceCreateResponse {
  id: string
}

export interface NoteResponse {
  id?: string
  title: string
  content: string
  created_at?: string
  updated_at?: string
}

export interface NotesListResponse {
  notes: NoteResponse[]
  count: number
  next_cursor?: string
}

export interface TaskResponse {
  id?: string
  title: string
  description?: string
  status: string
  priority: number
}

export interface TasksListResponse {
  tasks: TaskResponse[]
  count: number
  next_cursor?: string
}

export interface TranslationListResponse {
  translations: Record<string, unknown>[]
  count: number
  next_cursor?: string
}

export interface WordbookListResponse {
  wordbooks: Record<string, unknown>[]
  count: number
  next_cursor?: string
}

export interface WordListResponse {
  words: Record<string, unknown>[]
  count: number
  next_cursor?: string
}

export interface AgentAskResponse {
  reply: string
  session_id?: string
}

/* WebSocket connection state */

export type ConnectionState = 'disconnected' | 'connecting' | 'connected'

/* API client */

export class ApiClient {
  private baseUrl = ''
  private authToken: string | null = null

  configure(baseUrl: string, token: string | null): void {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    this.authToken = token
  }

  setToken(token: string | null): void {
    this.authToken = token
  }

  get isConfigured(): boolean {
    return this.baseUrl.length > 0
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    if (!this.baseUrl) {
      throw new Error('ApiClient not configured: baseUrl is empty')
    }

    const url = `${this.baseUrl}${path}`
    const headers: Record<string, string> = {}
    if (this.authToken) {
      headers['Authorization'] = `Bearer ${this.authToken}`
    }
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json'
    }

    const res = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })

    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`API ${method} ${path} ${res.status}: ${text}`)
    }

    if (res.status === 204) {
      return undefined as T
    }

    return res.json() as Promise<T>
  }

  /* ── Health ── */

  async health(): Promise<HealthResponse> {
    return this.request<HealthResponse>('GET', '/health')
  }

  /* ── Auth ── */

  async getMe(): Promise<MeResponse> {
    return this.request<MeResponse>('GET', '/auth/me')
  }

  async registerDevice(name: string): Promise<DeviceCreateResponse> {
    return this.request<DeviceCreateResponse>('POST', '/auth/devices', { name, platform: 'capacitor' })
  }

  async revokeToken(): Promise<void> {
    return this.request<void>('POST', '/auth/tokens/revoke')
  }

  async registerFcmToken(payload: Record<string, string>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('POST', '/auth/devices/fcm', payload)
  }

  /* ── Notes ── */

  async listNotes(since?: string, limit = 100): Promise<NotesListResponse> {
    const params = new URLSearchParams()
    if (since) params.set('since_updated_at', since)
    params.set('limit', String(limit))
    return this.request<NotesListResponse>('GET', `/personal/notes?${params}`)
  }

  async createNote(payload: Record<string, string>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('POST', '/personal/notes', payload)
  }

  async updateNote(id: string, payload: Record<string, string>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('PUT', `/personal/notes/${id}`, payload)
  }

  async deleteNote(id: string): Promise<void> {
    return this.request<void>('DELETE', `/personal/notes/${id}`)
  }

  /* ── Tasks ── */

  async listTasks(since?: string, limit = 100): Promise<TasksListResponse> {
    const params = new URLSearchParams()
    if (since) params.set('since_updated_at', since)
    params.set('limit', String(limit))
    return this.request<TasksListResponse>('GET', `/personal/tasks?${params}`)
  }

  async createTask(payload: Record<string, string>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('POST', '/personal/tasks', payload)
  }

  async updateTask(id: string, payload: Record<string, string>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('PUT', `/personal/tasks/${id}`, payload)
  }

  async deleteTask(id: string): Promise<void> {
    return this.request<void>('DELETE', `/personal/tasks/${id}`)
  }

  /* ── Translations / Wordbooks ── */

  async listTranslations(since?: string, limit = 100): Promise<TranslationListResponse> {
    const params = new URLSearchParams()
    if (since) params.set('since_updated_at', since)
    params.set('limit', String(limit))
    return this.request<TranslationListResponse>('GET', `/personal/translations?${params}`)
  }

  async listWordbooks(since?: string, limit = 100): Promise<WordbookListResponse> {
    const params = new URLSearchParams()
    if (since) params.set('since_updated_at', since)
    params.set('limit', String(limit))
    return this.request<WordbookListResponse>('GET', `/personal/wordbooks?${params}`)
  }

  async listWords(since?: string, limit = 100): Promise<WordListResponse> {
    const params = new URLSearchParams()
    if (since) params.set('since_updated_at', since)
    params.set('limit', String(limit))
    return this.request<WordListResponse>('GET', `/personal/words?${params}`)
  }

  /* ── Sessions ── */

  async getLatestSession(): Promise<Record<string, unknown> | null> {
    return this.request<Record<string, unknown> | null>('GET', '/sessions/latest')
  }

  async listSessions(): Promise<Record<string, unknown>[]> {
    return this.request<Record<string, unknown>[]>('GET', '/sessions')
  }

  async saveSession(sessionId: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('PUT', `/sessions/${sessionId}`, payload)
  }

  async detectSessionSwitch(deviceType: string, currentSessionId?: string): Promise<Record<string, unknown> | null> {
    return this.request<Record<string, unknown> | null>('POST', '/sessions/switch', {
      device_type: deviceType,
      current_session_id: currentSessionId,
    })
  }

  /* ── Agent ── */

  async askAgent(message: string, sessionId?: string): Promise<AgentAskResponse> {
    return this.request<AgentAskResponse>('POST', '/agent/ask', {
      message,
      session_id: sessionId,
    })
  }
}
