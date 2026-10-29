/* 思忆集 PWA — 对话视图（WebSocket 实时聊天） */
(function () {
  'use strict';

  let ws = null;
  let wsReconnectTimer = null;
  let pingInterval = null;
  let messageList = [];
  let currentPage = null;

  function renderChat() {
    const page = document.createElement('div');
    page.className = 'view-page';
    page.innerHTML = `
      <div style="display:flex;flex-direction:column;height:calc(100vh - 60px);">
        <div style="padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;">
          <span style="font-size:16px;font-weight:600;flex:1;">对话</span>
          <span id="ws-status" class="sync-dot offline" style="width:10px;height:10px;" title="未连接"></span>
        </div>
        <div id="chat-messages" style="flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:8px;"></div>
        <div style="padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:8px;background:var(--surface);">
          <input id="chat-input" type="text" placeholder="输入消息..." style="flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:20px;font-size:14px;outline:none;background:var(--bg);color:var(--text);">
          <button id="chat-send" style="width:40px;height:40px;border-radius:50%;border:none;background:var(--primary);color:#fff;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;">→</button>
        </div>
      </div>
    `;
    currentPage = page;
    connectWebSocket(page);
    setupInputHandlers(page);
    return page;
  }

  function destroyChat() {
    disconnectWebSocket();
    currentPage = null;
  }

  // ── WebSocket ─────────────────────────────────────────────

  function connectWebSocket(page) {
    if (!window.api.isPaired()) return;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/mobile/ws`;

    try {
      ws = new WebSocket(url);
    } catch (err) {
      updateStatus(page, 'error');
      addSystemMessage(page, 'WebSocket 连接失败: ' + err.message);
      return;
    }

    ws.onopen = () => {
      // Send auth as first message
      ws.send(JSON.stringify({ type: 'auth', token: window.api.token }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleMessage(page, data);
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      updateStatus(page, 'offline');
      if (currentPage === page) {
        scheduleReconnect(page);
      }
    };

    ws.onerror = () => {
      updateStatus(page, 'error');
    };

    // Keepalive ping every 30s
    pingInterval = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);
  }

  function disconnectWebSocket() {
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    if (ws) {
      ws.onclose = null; // prevent reconnect during intentional close
      ws.close();
      ws = null;
    }
  }

  function scheduleReconnect(page) {
    if (wsReconnectTimer) return;
    wsReconnectTimer = setTimeout(() => {
      wsReconnectTimer = null;
      if (currentPage === page) {
        addSystemMessage(page, '正在重新连接...');
        connectWebSocket(page);
      }
    }, 5000);
  }

  // ── Message handling ─────────────────────────────────────

  function handleMessage(page, data) {
    const type = data.type;

    switch (type) {
      case 'auth_ok':
        updateStatus(page, 'online');
        addSystemMessage(page, '已连接到思忆集');
        // Restore previous messages from server? For now, clear any "connecting" state
        break;

      case 'pong':
        // keepalive response, nothing to do
        break;

      case 'error':
        addSystemMessage(page, '错误: ' + (data.message || ''));
        if (data.message === 'Invalid token') {
          updateStatus(page, 'error');
        }
        break;

      case 'approval':
        showApprovalNotification(page, data);
        break;

      case 'notification':
        addSystemMessage(page, data.title ? data.title + ': ' + (data.body || '') : '收到新通知');
        break;

      case 'chat_reply':
        addMessage(page, 'assistant', data.text, data.timestamp);
        break;

      case 'approval_response_ack':
        addSystemMessage(page, '审批已记录: ' + (data.action || ''));
        break;

      default:
        // Unknown message type — show if it has text
        if (data.text) {
          addMessage(page, 'assistant', data.text, data.timestamp);
        }
    }
  }

  // ── UI helpers ───────────────────────────────────────────

  function addMessage(page, role, text, timestamp) {
    const container = page.querySelector('#chat-messages');
    if (!container) return;

    const isUser = role === 'user';
    const time = timestamp ? formatTime(timestamp) : '';
    const div = document.createElement('div');
    div.style.cssText = `
      max-width:80%;padding:10px 14px;border-radius:16px;font-size:14px;line-height:1.5;
      word-wrap:break-word;align-self:${isUser ? 'flex-end' : 'flex-start'};
      background:${isUser ? 'var(--primary)' : 'var(--surface)'};
      color:${isUser ? '#fff' : 'var(--text)'};
      border-bottom-${isUser ? 'right' : 'left'}-radius:4px;
    `;
    div.textContent = text;
    if (time) {
      div.title = time;
    }
    container.appendChild(div);
    scrollToBottom(container);
    messageList.push({ role, text, timestamp });
  }

  function addSystemMessage(page, text) {
    const container = page.querySelector('#chat-messages');
    if (!container) return;
    const div = document.createElement('div');
    div.style.cssText = 'text-align:center;font-size:12px;color:var(--text-secondary);padding:4px 0;';
    div.textContent = text;
    container.appendChild(div);
    scrollToBottom(container);
  }

  function showApprovalNotification(page, data) {
    const container = page.querySelector('#chat-messages');
    if (!container) return;

    const div = document.createElement('div');
    div.style.cssText = `
      align-self:center;background:var(--surface);border:1px solid var(--border);
      border-radius:16px;padding:14px 18px;max-width:90%;text-align:center;
      box-shadow:0 2px 8px rgba(0,0,0,0.06);
    `;
    div.innerHTML = `
      <div style="font-size:13px;font-weight:600;margin-bottom:4px;">${escHtml(data.title || '审批请求')}</div>
      ${data.description ? `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">${escHtml(data.description)}</div>` : ''}
      <div style="display:flex;gap:8px;justify-content:center;">
        <button class="approval-btn approve" data-id="${escHtml(data.approval_id || '')}" style="flex:1;padding:8px;border:none;border-radius:12px;background:#22c55e;color:#fff;font-size:13px;cursor:pointer;">批准</button>
        <button class="approval-btn reject" data-id="${escHtml(data.approval_id || '')}" style="flex:1;padding:8px;border:none;border-radius:12px;background:#ef4444;color:#fff;font-size:13px;cursor:pointer;">拒绝</button>
      </div>
    `;

    div.querySelectorAll('.approval-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.classList.contains('approve') ? 'approve' : 'reject';
        const approvalId = btn.dataset.id;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'approval_response',
            approval_id: approvalId,
            action: action,
          }));
        }
        // Disable buttons after click
        div.querySelectorAll('.approval-btn').forEach(b => b.disabled = true);
        btn.textContent = action === 'approve' ? '✓ 已批准' : '✗ 已拒绝';
        btn.style.opacity = '0.6';
      });
    });

    container.appendChild(div);
    scrollToBottom(container);
  }

  function updateStatus(page, status) {
    const dot = page.querySelector('#ws-status');
    if (!dot) return;
    dot.className = 'sync-dot ' + status;
    dot.title = status === 'online' ? '已连接' : status === 'offline' ? '未连接' : '连接错误';
  }

  function setupInputHandlers(page) {
    const input = page.querySelector('#chat-input');
    const sendBtn = page.querySelector('#chat-send');

    function sendMessage() {
      const text = input.value.trim();
      if (!text) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        addSystemMessage(page, '未连接到服务器');
        return;
      }
      ws.send(JSON.stringify({ type: 'chat', text }));
      addMessage(page, 'user', text, new Date().toISOString());
      input.value = '';
      input.focus();
    }

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendMessage();
    });

    // Auto-focus input
    setTimeout(() => input.focus(), 300);
  }

  function scrollToBottom(container) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }

  function formatTime(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  }

  function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
  }

  // ── Register view ───────────────────────────────────────

  window.router.register('chat', () => ({
    render: renderChat,
    destroy: destroyChat,
  }));

})();
