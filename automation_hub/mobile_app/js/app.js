/* 思忆集 PWA — 主入口 */
(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ── Lifecycle ──────────────────────────────────

  async function init() {
    // Update sync dot via syncManager
    if (window.syncManager) {
      window.syncManager.on('ok', () => {
        updateSyncDotUI();
      });
      window.syncManager.on('error', () => {
        updateSyncDotUI();
      });
      window.syncManager.on('syncing', () => {
        updateSyncDotUI();
      });
    }

    // Determine base URL: use current origin
    window.api.baseUrl = window.location.origin;

    // If already paired, go to dashboard
    if (window.api.isPaired()) {
      // Start sync polling
      if (window.syncManager) {
        window.syncManager.start(30000);
      }
      window.router.navigate('dashboard');
      return;
    }

    // Check for setup token in URL query (from QR code)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('token')) {
      window.router.navigate('pairing');
      return;
    }

    window.router.navigate('pairing');
  }

  // ── Sync status UI ─────────────────────────────

  function updateSyncDotUI() {
    const dot = $('#sync-dot');
    if (dot && window.syncManager) {
      window.syncManager.updateSyncDot(dot);
    }
  }

  // ── Toast ──────────────────────────────────────

  window.showToast = function (msg) {
    let toast = $('#toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'toast';
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(() => toast.classList.remove('show'), 2500);
  };

  // ── Service Worker registration ────────────────

  async function registerSW() {
    if ('serviceWorker' in navigator) {
      try {
        const reg = await navigator.serviceWorker.register('sw.js');
        reg.addEventListener('updatefound', () => {
          const newSW = reg.installing;
          newSW.addEventListener('statechange', () => {
            if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
              showToast('有新版本可用，请刷新页面');
            }
          });
        });

        navigator.serviceWorker.addEventListener('message', (event) => {
          if (event.data && event.data.type === 'SYNC_OFFLINE') {
            window.offlineQueue.replay(window.api).then((result) => {
              if (result.replayed > 0) {
                showToast('已同步 ' + result.replayed + ' 条离线数据');
              }
            });
          }
        });
      } catch {
        // SW registration failed — app works without it
      }
    }
  }

  // ── Boot ───────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    registerSW();

    // Update loading screen with some visual
    const loadingScreen = $('#loading-screen');
    if (loadingScreen) {
      loadingScreen.innerHTML = '<div class="spinner"></div><p>思忆集</p>';
    }

    // Register views
    const loaded = new Set();
    const originalNavigate = window.router.navigate.bind(window.router);

    const scriptMap = {
      pairing: 'js/views/pairing.js',
      dashboard: 'js/views/dashboard.js',
      notes: 'js/views/notes.js',
      'note-detail': 'js/views/note-detail.js',
      tasks: 'js/views/tasks.js',
      'task-detail': 'js/views/task-detail.js',
      capture: 'js/views/capture.js',
      settings: 'js/views/settings.js',
      chat: 'js/views/chat.js',
    };

    window.router.navigate = function (name, params) {
      const script = scriptMap[name];
      if (script && !loaded.has(script)) {
        loaded.add(script);
        const tag = document.createElement('script');
        tag.src = script;
        tag.onload = () => originalNavigate(name, params);
        tag.onerror = () => {
          console.warn('Failed to load view:', script);
          originalNavigate(name, params);
        };
        document.body.appendChild(tag);
      } else {
        originalNavigate(name, params);
      }
    };

    // Also intercept route changes for lazy-loading
    const origHandleRoute = window.router._handleRoute.bind(window.router);
    window.router._handleRoute = function () {
      const hash = window.location.hash.slice(1).replace(/\?.*$/, '') || 'pairing';
      const script = scriptMap[hash];

      if (script && !loaded.has(script)) {
        loaded.add(script);
        const tag = document.createElement('script');
        tag.src = script;
        tag.onload = origHandleRoute;
        tag.onerror = origHandleRoute;
        document.body.appendChild(tag);
        return;
      }
      origHandleRoute();
    };

    // Nav click handlers
    $$('.nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        window.router.navigate(btn.dataset.route);
      });
    });

    init();
  });

})();
