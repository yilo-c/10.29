/* 思忆集 PWA — 设置视图 */
(function () {
  'use strict';

function renderSettings() {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <h3 style="font-size:16px;font-weight:600;margin-bottom:12px;">设置</h3>

    <div class="card">
      <div class="card-title">外观</div>
      <div style="margin-top:8px;display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:14px;">暗色模式</span>
        <label style="position:relative;display:inline-block;width:48px;height:26px;">
          <input type="checkbox" id="dark-mode-toggle" style="opacity:0;width:0;height:0;">
          <span id="dark-mode-slider" style="position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#ccc;border-radius:26px;transition:0.3s;"></span>
          <span id="dark-mode-knob" style="position:absolute;content:'';height:20px;width:20px;left:3px;bottom:3px;background:white;border-radius:50%;transition:0.3s;"></span>
        </label>
      </div>
      <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">
        系统自动匹配: <span id="system-theme-info">-</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">设备信息</div>
      <div style="font-size:13px;color:var(--text-secondary);margin-top:4px;">
        <div>设备 ID: <span id="settings-device-id">-</span></div>
        <div style="margin-top:4px;">已配对: <span id="settings-paired-status">是</span></div>
        <div style="margin-top:4px;">服务器: <span id="settings-server">-</span></div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">同步状态</div>
      <div style="font-size:13px;color:var(--text-secondary);margin-top:4px;">
        <span id="settings-sync-status">检查中...</span>
      </div>
    </div>

    <button class="btn btn-danger" id="settings-unpair" style="margin-top:16px;">断开配对</button>
    <div id="settings-status" style="font-size:13px;text-align:center;margin-top:12px;"></div>
  `;

  // ── Dark mode ──────────────────────────────────
  const toggle = page.querySelector('#dark-mode-toggle');
  const slider = page.querySelector('#dark-mode-slider');
  const knob = page.querySelector('#dark-mode-knob');

  function applyTheme(dark) {
    if (dark) {
      document.documentElement.setAttribute('data-theme', 'dark');
      slider.style.background = 'var(--primary)';
      knob.style.transform = 'translateX(22px)';
    } else {
      document.documentElement.removeAttribute('data-theme');
      slider.style.background = '#ccc';
      knob.style.transform = 'translateX(0)';
    }
    localStorage.setItem('siyiji_theme', dark ? 'dark' : 'light');
  }

  // Detect system preference
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');
  const savedTheme = localStorage.getItem('siyiji_theme');

  // Show system preference info
  page.querySelector('#system-theme-info').textContent = prefersDark.matches ? '暗色' : '亮色';

  if (savedTheme) {
    toggle.checked = savedTheme === 'dark';
    applyTheme(toggle.checked);
  } else {
    toggle.checked = prefersDark.matches;
    applyTheme(toggle.checked);
  }

  toggle.addEventListener('change', () => {
    applyTheme(toggle.checked);
  });

  // Listen for system theme changes (only when no saved preference)
  prefersDark.addEventListener('change', (e) => {
    if (!localStorage.getItem('siyiji_theme')) {
      toggle.checked = e.matches;
      applyTheme(e.matches);
    }
  });

  // ── Device info ─────────────────────────────────
  page.querySelector('#settings-device-id').textContent = window.api.deviceId || '未知';
  page.querySelector('#settings-server').textContent = window.api.baseUrl;

  // ── Sync status ─────────────────────────────────
  checkSync(page);

  // ── Unpair ─────────────────────────────────────
  page.querySelector('#settings-unpair').addEventListener('click', async () => {
    if (!confirm('确定断开配对？')) return;
    window.api.clearToken();
    showToast('已断开配对');
    window.router.navigate('pairing');
  });

  return page;
}

async function checkSync(page) {
  try {
    const status = await window.api.getPairingStatus();
    page.querySelector('#settings-sync-status').textContent =
      '已连接 (' + (status.paired_devices || []).length + ' 台设备)';
    page.querySelector('#settings-paired-status').textContent = '是';
    page.querySelector('#settings-sync-status').style.color = 'var(--success)';
  } catch {
    page.querySelector('#settings-sync-status').textContent = '离线';
    page.querySelector('#settings-sync-status').style.color = 'var(--danger)';
  }
}

window.router.register('settings', () => ({
  render: renderSettings,
  destroy: () => {},
}));

})();
