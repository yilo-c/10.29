/* 思忆集 PWA — 同步状态管理 */
(function () {
  'use strict';

  /**
   * SyncManager: extracts sync polling logic from app.js into a standalone module.
   * Emits events: 'syncing', 'ok', 'error'
   */
  class SyncManager {
    constructor() {
      this._interval = null;
      this._listeners = {};
      this._status = 'unknown'; // 'unknown' | 'syncing' | 'ok' | 'error'
      this._lastCheck = 0;
    }

    get status() { return this._status; }

    on(event, fn) {
      if (!this._listeners[event]) this._listeners[event] = [];
      this._listeners[event].push(fn);
    }

    off(event, fn) {
      if (!this._listeners[event]) return;
      this._listeners[event] = this._listeners[event].filter(f => f !== fn);
    }

    _emit(event, data) {
      (this._listeners[event] || []).forEach(fn => fn(data));
    }

    start(intervalMs = 30000) {
      this.check();
      this._interval = setInterval(() => this.check(), intervalMs);
    }

    stop() {
      if (this._interval) {
        clearInterval(this._interval);
        this._interval = null;
      }
    }

    async check() {
      if (!window.api.isPaired()) return;

      this._emit('syncing', {});
      this._status = 'syncing';

      try {
        const status = await window.api.getPairingStatus();
        this._status = 'ok';
        this._lastCheck = Date.now();
        this._emit('ok', { status });

        // Replay offline queue when online
        window.offlineQueue.replay(window.api).then((result) => {
          if (result.replayed > 0) {
            showToast('已同步 ' + result.replayed + ' 条离线数据');
          }
        });
      } catch (err) {
        this._status = 'error';
        this._emit('error', { error: err.message });
      }
    }

    /** Update sync dot UI element */
    updateSyncDot(dotEl) {
      if (!dotEl) return;
      const clsMap = {
        ok: 'online',
        syncing: 'syncing',
        error: 'offline',
        unknown: 'offline',
      };
      dotEl.className = 'sync-dot ' + (clsMap[this._status] || 'offline');
    }
  }

  window.syncManager = new SyncManager();

})();
