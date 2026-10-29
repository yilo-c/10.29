/* 思忆集 PWA — API 客户端 */
class MobileApiClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
    this.token = localStorage.getItem('siyiji_token') || '';
    this.deviceId = localStorage.getItem('siyiji_device_id') || '';
  }

  setToken(token, deviceId) {
    this.token = token;
    this.deviceId = deviceId;
    localStorage.setItem('siyiji_token', token);
    localStorage.setItem('siyiji_device_id', deviceId || '');
  }

  clearToken() {
    this.token = '';
    this.deviceId = '';
    localStorage.removeItem('siyiji_token');
    localStorage.removeItem('siyiji_device_id');
  }

  isPaired() { return !!this.token; }

  async request(method, path, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (this.token) headers['Authorization'] = `Bearer ${this.token}`;
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method, headers,
        body: body ? JSON.stringify(body) : null,
      });
      if (res.status === 401) {
        this.clearToken();
        window.router.navigate('pairing');
        throw new Error('未授权，请重新配对');
      }
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      return res.json();
    } catch (err) {
      if (err.name === 'TypeError' && err.message.includes('fetch')) {
        // Queue POST/PUT/DELETE requests for offline replay
        if (method !== 'GET' && window.offlineQueue) {
          try {
            await window.offlineQueue.enqueue(method, path, body);
          } catch { /* offline queue may not be ready */ }
        }
        throw new Error('无法连接到服务器');
      }
      throw err;
    }
  }

  // Pairing
  initiatePairing() { return this.request('POST', '/mobile/pairing/initiate'); }
  completePairing(setupToken, pinCode, deviceName) {
    return this.request('POST', '/mobile/pairing/complete', {
      setup_token: setupToken,
      pin_code: pinCode,
      device_name: deviceName,
      platform: 'android',
    });
  }

  // Data
  getDashboard() { return this.request('GET', '/mobile/dashboard'); }
  getNotes(cursor) { return this.request('GET', `/personal/notes?limit=50${cursor ? `&cursor=${cursor}` : ''}`); }
  getNote(id) { return this.request('GET', `/personal/notes/${id}`); }
  updateNote(id, data) { return this.request('PUT', `/personal/notes/${id}`, data); }
  getTasks() { return this.request('GET', '/personal/tasks?limit=50'); }
  getTask(id) { return this.request('GET', `/personal/tasks/${id}`); }
  patchTask(id, data) { return this.request('PATCH', `/personal/tasks/${id}`, data); }
  createNote(title, content) { return this.request('POST', '/personal/notes', { title, content }); }
  quickCapture(captureType, content, mediaUrl) {
    return this.request('POST', '/mobile/capture', {
      capture_type: captureType, content, media_url: mediaUrl || null,
      source: 'phone', captured_at: new Date().toISOString(),
    });
  }

  // Sync info
  getPairingStatus() { return this.request('GET', '/mobile/pairing/status'); }
  getMe() { return this.request('GET', '/auth/me'); }

  // Chat (for future use)
  getChat() { return this.request('GET', '/mobile/chat'); }
  sendChatMessage(message) { return this.request('POST', '/mobile/chat', { message }); }
}

window.api = new MobileApiClient('');
