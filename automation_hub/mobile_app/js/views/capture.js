/* 思忆集 PWA — 快速捕获视图（文字/语音/拍照） */
(function () {
  'use strict';

function renderCapture() {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <div style="display:flex;gap:4px;margin-bottom:12px;border-bottom:2px solid var(--border);">
      <button class="capture-tab active" data-tab="text" style="flex:1;padding:8px;border:none;background:none;font-size:14px;font-weight:600;color:var(--primary);border-bottom:2px solid var(--primary);cursor:pointer;">文字</button>
      <button class="capture-tab" data-tab="voice" style="flex:1;padding:8px;border:none;background:none;font-size:14px;color:var(--text-secondary);cursor:pointer;">语音</button>
      <button class="capture-tab" data-tab="photo" style="flex:1;padding:8px;border:none;background:none;font-size:14px;color:var(--text-secondary);cursor:pointer;">拍照</button>
    </div>

    <!-- Text tab -->
    <div id="capture-text" class="capture-panel">
      <input class="input" id="capture-title" placeholder="标题（可选）" style="margin-bottom:8px;">
      <textarea class="input" id="capture-content" placeholder="写点什么..." style="min-height:150px;"></textarea>
      <button class="btn btn-primary" id="capture-text-btn" style="margin-top:12px;">保存文字</button>
    </div>

    <!-- Voice tab -->
    <div id="capture-voice" class="capture-panel" style="display:none;text-align:center;padding:20px 0;">
      <div style="font-size:48px;margin-bottom:16px;" id="voice-icon">🎤</div>
      <p style="color:var(--text-secondary);margin-bottom:16px;" id="voice-status">点击下方按钮开始录音</p>
      <button class="btn" id="capture-voice-btn" style="width:auto;padding:16px 48px;border-radius:40px;margin:0 auto;">开始录音</button>
      <div id="voice-wave" style="display:none;height:40px;margin:16px 0;">
        <div style="display:flex;justify-content:center;gap:4px;align-items:center;height:100%;">
          <div class="voice-bar" style="width:4px;height:20px;background:var(--primary);border-radius:2px;animation:voiceWave 0.5s ease infinite alternate;"></div>
          <div class="voice-bar" style="width:4px;height:30px;background:var(--primary);border-radius:2px;animation:voiceWave 0.5s 0.1s ease infinite alternate;"></div>
          <div class="voice-bar" style="width:4px;height:15px;background:var(--primary);border-radius:2px;animation:voiceWave 0.5s 0.2s ease infinite alternate;"></div>
          <div class="voice-bar" style="width:4px;height:35px;background:var(--primary);border-radius:2px;animation:voiceWave 0.5s 0.15s ease infinite alternate;"></div>
          <div class="voice-bar" style="width:4px;height:25px;background:var(--primary);border-radius:2px;animation:voiceWave 0.5s 0.3s ease infinite alternate;"></div>
        </div>
      </div>
      <div id="voice-result" style="margin-top:12px;display:none;">
        <textarea class="input" id="voice-transcript" placeholder="语音转文字结果..." style="min-height:80px;margin-bottom:8px;"></textarea>
        <button class="btn btn-primary" id="voice-save-btn">保存语音笔记</button>
      </div>
    </div>

    <!-- Photo tab -->
    <div id="capture-photo" class="capture-panel" style="display:none;text-align:center;padding:20px 0;">
      <div style="font-size:48px;margin-bottom:16px;">📷</div>
      <video id="camera-preview" style="width:100%;max-height:300px;border-radius:var(--radius);background:#000;display:none;" autoplay playsinline></video>
      <canvas id="camera-canvas" style="display:none;"></canvas>
      <div id="photo-preview" style="display:none;margin-bottom:12px;">
        <img id="photo-img" style="width:100%;max-height:300px;border-radius:var(--radius);object-fit:cover;">
      </div>
      <p style="color:var(--text-secondary);margin-bottom:16px;" id="photo-status">点击下方按钮打开相机</p>
      <button class="btn" id="capture-photo-btn" style="width:auto;padding:16px 48px;border-radius:40px;margin:0 auto;">打开相机</button>
      <div id="photo-actions" style="display:none;margin-top:12px;">
        <button class="btn btn-primary" id="photo-capture-btn" style="width:auto;padding:12px 32px;margin:0 auto 8px;">📸 拍照</button>
        <button class="btn btn-outline" id="photo-save-btn" style="width:auto;padding:12px 32px;margin:0 auto;">保存照片笔记</button>
      </div>
    </div>

    <div id="capture-status" style="font-size:13px;color:var(--text-secondary);text-align:center;margin-top:12px;"></div>
  `;

  // ── Tab switching ─────────────────────────────────
  let activeStream = null;
  let capturedPhotoBlob = null;
  let voiceBlob = null;

  page.querySelectorAll('.capture-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // Stop any active media
      if (activeStream) {
        activeStream.getTracks().forEach(t => t.stop());
        activeStream = null;
      }
      window.MediaCapture && window.MediaCapture.stopRecording();
      capturedPhotoBlob = null;
      voiceBlob = null;

      page.querySelectorAll('.capture-tab').forEach(t => {
        t.style.color = 'var(--text-secondary)';
        t.style.borderBottom = '2px solid transparent';
      });
      tab.style.color = 'var(--primary)';
      tab.style.borderBottom = '2px solid var(--primary)';

      page.querySelectorAll('.capture-panel').forEach(p => p.style.display = 'none');
      const panel = page.querySelector('#capture-' + tab.dataset.tab);
      if (panel) panel.style.display = 'block';
    });
  });

  // ── Text capture ──────────────────────────────────
  page.querySelector('#capture-text-btn').addEventListener('click', async () => {
    const title = page.querySelector('#capture-title').value.trim();
    const content = page.querySelector('#capture-content').value.trim();
    const statusEl = page.querySelector('#capture-status');

    if (!content) { showToast('请输入内容'); return; }

    statusEl.textContent = '保存中...';
    const btn = page.querySelector('#capture-text-btn');
    btn.disabled = true;

    try {
      await window.api.createNote(title || '手机速记', content);
      statusEl.textContent = '✅ 已保存';
      statusEl.style.color = 'var(--success)';
      page.querySelector('#capture-title').value = '';
      page.querySelector('#capture-content').value = '';
    } catch (err) {
      statusEl.textContent = '❌ ' + err.message;
      statusEl.style.color = 'var(--danger)';
    }
    btn.disabled = false;
  });

  // ── Voice capture ─────────────────────────────────
  const voiceBtn = page.querySelector('#capture-voice-btn');
  const voiceStatus = page.querySelector('#voice-status');
  const voiceIcon = page.querySelector('#voice-icon');
  const voiceWave = page.querySelector('#voice-wave');
  const voiceResult = page.querySelector('#voice-result');

  async function startRecording() {
    if (!window.MediaCapture) {
      voiceStatus.textContent = '❌ 媒体捕获工具不可用';
      return;
    }
    try {
      const stream = await window.MediaCapture.getMicStream();
      activeStream = stream;
      voiceBtn.textContent = '⏹ 停止录音';
      voiceStatus.textContent = '录音中...';
      voiceIcon.textContent = '🔴';
      voiceWave.style.display = 'block';
      voiceResult.style.display = 'none';
      voiceBlob = null;

      // Start recording
      window.MediaCapture.recordAudio(stream).then((blob) => {
        voiceBlob = blob;
        voiceBtn.textContent = '开始录音';
        voiceStatus.textContent = '✅ 录音完成 (' + (blob.size / 1024).toFixed(0) + 'KB)';
        voiceIcon.textContent = '🎤';
        voiceWave.style.display = 'none';
        voiceResult.style.display = 'block';
        activeStream = null;

        // Try to auto-fill a dummy transcript
        const transcriptEl = page.querySelector('#voice-transcript');
        transcriptEl.value = '[语音记录 ' + new Date().toLocaleString() + ']';
      });
    } catch (err) {
      voiceStatus.textContent = '❌ ' + err.message;
      voiceBtn.textContent = '开始录音';
    }
  }

  voiceBtn.addEventListener('click', () => {
    if (voiceBtn.textContent.includes('停止')) {
      window.MediaCapture && window.MediaCapture.stopRecording();
      voiceBtn.textContent = '开始录音';
    } else {
      startRecording();
    }
  });

  page.querySelector('#voice-save-btn').addEventListener('click', async () => {
    const transcript = page.querySelector('#voice-transcript').value.trim();
    const statusEl = page.querySelector('#capture-status');

    if (!transcript && !voiceBlob) { showToast('没有录音内容'); return; }

    statusEl.textContent = '保存中...';
    const btn = page.querySelector('#voice-save-btn');
    btn.disabled = true;

    try {
      await window.api.createNote('语音速记', transcript || '[语音记录]');
      if (voiceBlob && window.MediaCapture) {
        try {
          await window.MediaCapture.uploadBlob(voiceBlob, 'voice-' + Date.now() + '.webm', 'voice');
        } catch (e) { /* upload optional */ }
      }
      statusEl.textContent = '✅ 已保存';
      statusEl.style.color = 'var(--success)';
      voiceResult.style.display = 'none';
      voiceBlob = null;
    } catch (err) {
      statusEl.textContent = '❌ ' + err.message;
      statusEl.style.color = 'var(--danger)';
    }
    btn.disabled = false;
  });

  // ── Photo capture ─────────────────────────────────
  const photoBtn = page.querySelector('#capture-photo-btn');
  const photoStatus = page.querySelector('#photo-status');
  const cameraPreview = page.querySelector('#camera-preview');
  const photoPreview = page.querySelector('#photo-preview');
  const photoImg = page.querySelector('#photo-img');
  const photoActions = page.querySelector('#photo-actions');
  const capturePhotoBtn = page.querySelector('#photo-capture-btn');

  async function openCamera() {
    if (!window.MediaCapture) {
      photoStatus.textContent = '❌ 媒体捕获工具不可用';
      return;
    }
    try {
      const stream = await window.MediaCapture.getCameraStream('environment');
      activeStream = stream;
      cameraPreview.srcObject = stream;
      cameraPreview.style.display = 'block';
      photoPreview.style.display = 'none';
      photoBtn.textContent = '关闭相机';
      photoBtn.className = 'btn btn-danger';
      photoStatus.textContent = '相机已开启';
      photoActions.style.display = 'block';
      capturePhotoBtn.style.display = 'inline-block';
      capturedPhotoBlob = null;
    } catch (err) {
      photoStatus.textContent = '❌ ' + err.message;
    }
  }

  function closeCamera() {
    if (activeStream) {
      activeStream.getTracks().forEach(t => t.stop());
      activeStream = null;
    }
    cameraPreview.style.display = 'none';
    cameraPreview.srcObject = null;
    photoBtn.textContent = '打开相机';
    photoBtn.className = 'btn';
    photoStatus.textContent = '点击下方按钮打开相机';
    photoActions.style.display = 'none';
  }

  photoBtn.addEventListener('click', () => {
    if (activeStream) {
      closeCamera();
    } else {
      openCamera();
    }
  });

  capturePhotoBtn.addEventListener('click', async () => {
    if (!activeStream || !window.MediaCapture) return;
    try {
      const blob = await window.MediaCapture.capturePhoto(activeStream);
      capturedPhotoBlob = blob;
      closeCamera();

      const url = URL.createObjectURL(blob);
      photoImg.src = url;
      photoPreview.style.display = 'block';
      photoStatus.textContent = '✅ 拍照完成 (' + (blob.size / 1024).toFixed(0) + 'KB)';
      photoBtn.textContent = '重新拍照';
      photoBtn.className = 'btn';
    } catch (err) {
      photoStatus.textContent = '❌ ' + err.message;
    }
  });

  page.querySelector('#photo-save-btn').addEventListener('click', async () => {
    const statusEl = page.querySelector('#capture-status');
    if (!capturedPhotoBlob) { showToast('请先拍照'); return; }

    statusEl.textContent = '保存中...';
    const btn = page.querySelector('#photo-save-btn');
    btn.disabled = true;

    try {
      await window.api.createNote('拍照速记', '[照片 ' + new Date().toLocaleString() + ']');
      if (window.MediaCapture) {
        try {
          await window.MediaCapture.uploadBlob(capturedPhotoBlob, 'photo-' + Date.now() + '.jpg', 'photo');
        } catch (e) { /* upload optional */ }
      }
      statusEl.textContent = '✅ 已保存';
      statusEl.style.color = 'var(--success)';
      photoPreview.style.display = 'none';
      capturedPhotoBlob = null;
    } catch (err) {
      statusEl.textContent = '❌ ' + err.message;
      statusEl.style.color = 'var(--danger)';
    }
    btn.disabled = false;
  });

  return page;
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

// Voice wave animation
const style = document.createElement('style');
style.textContent = `
  @keyframes voiceWave {
    0% { transform: scaleY(0.5); }
    100% { transform: scaleY(1); }
  }
`;
document.head.appendChild(style);

window.router.register('capture', () => ({
  render: renderCapture,
  destroy: () => {},
}));

})();
