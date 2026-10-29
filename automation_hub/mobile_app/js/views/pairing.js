/* 思忆集 PWA — 配对页面（PIN 输入 + URL token 双通道） */
(function () {

function renderPairingPage() {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <div class="pairing-container">
      <h2>📱 连接到思忆集</h2>
      <p>请在桌面端开启「手机接入」后输入配对码</p>

      <div id="pairing-pin-section">
        <label style="font-size:13px;color:var(--text-secondary);display:block;margin-bottom:8px;">配对码（6 位数）</label>
        <input class="input pin-input" id="pairing-pin" type="text" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="000000" autocomplete="off">
        <div style="margin-top:16px;">
          <button class="btn btn-primary" id="pairing-submit-btn">连接</button>
        </div>
      </div>

      <div class="divider">或者</div>

      <p style="font-size:13px;color:var(--text-secondary);">扫描桌面端 QR 码将自动进入</p>

      <div id="pairing-status" style="margin-top:20px;font-size:14px;color:var(--text-secondary);"></div>
    </div>
  `;

  const pinInput = page.querySelector('#pairing-pin');
  const submitBtn = page.querySelector('#pairing-submit-btn');
  const statusEl = page.querySelector('#pairing-status');

  // Check URL params for setup token (from QR code scan)
  const urlParams = new URLSearchParams(window.location.search);
  const setupToken = urlParams.get('token');

  if (setupToken) {
    statusEl.textContent = '检测到配对链接，正在配对...';
    statusEl.style.color = 'var(--primary)';
    completePairingFlow(setupToken, '', '', statusEl);
  }

  // PIN input: auto-submit on 6 digits
  pinInput.addEventListener('input', () => {
    pinInput.value = pinInput.value.replace(/\D/g, '').slice(0, 6);
  });

  pinInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && pinInput.value.length === 6) {
      doPinPairing();
    }
  });

  submitBtn.addEventListener('click', doPinPairing);

  function doPinPairing() {
    const pin = pinInput.value.trim();
    if (pin.length !== 6) {
      showToast('请输入完整的 6 位配对码');
      return;
    }
    pinInput.disabled = true;
    submitBtn.disabled = true;
    statusEl.textContent = '正在配对...';
    statusEl.style.color = 'var(--text-secondary)';
    completePairingFlow('', pin, '', statusEl);
  }

  return page;
}

async function completePairingFlow(setupToken, pinCode, deviceName, statusEl) {
  try {
    const name = deviceName || 'Android Phone';
    const result = await window.api.completePairing(setupToken, pinCode, name);
    window.api.setToken(result.api_token, result.device_id);
    statusEl.textContent = '✅ 配对成功！';
    statusEl.style.color = 'var(--success)';
    setTimeout(() => window.router.navigate('dashboard'), 800);
  } catch (err) {
    statusEl.textContent = '❌ ' + (err.message || '配对失败，请重试');
    statusEl.style.color = 'var(--danger)';
    // Re-enable inputs
    const pinInput = document.getElementById('pairing-pin');
    const submitBtn = document.getElementById('pairing-submit-btn');
    if (pinInput) pinInput.disabled = false;
    if (submitBtn) submitBtn.disabled = false;
  }
}

window.router.register('pairing', () => ({
  render: renderPairingPage,
  destroy: () => {},
}));

})();
