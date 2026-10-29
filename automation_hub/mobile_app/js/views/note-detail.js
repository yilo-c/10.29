/* 思忆集 PWA — 笔记详情/编辑视图 */
(function () {
  'use strict';

function renderNoteDetail(params) {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px;">
      <button class="btn btn-outline" id="note-back" style="width:auto;padding:8px 16px;">← 返回</button>
      <button class="btn btn-primary" id="note-save" style="width:auto;padding:8px 16px;display:none;">保存</button>
      <button class="btn btn-outline" id="note-edit-toggle" style="width:auto;padding:8px 16px;display:none;">编辑</button>
    </div>
    <div id="note-detail-loading"><div class="spinner" style="margin:40px auto;"></div></div>
    <div id="note-detail-error" style="display:none;text-align:center;padding:40px 0;"></div>
    <div id="note-detail-body" style="display:none;">
      <input class="input" id="note-title-input" placeholder="标题" style="margin-bottom:8px;font-weight:600;">
      <textarea class="input" id="note-content-input" placeholder="内容..." style="min-height:200px;font-family:inherit;"></textarea>
      <div id="note-rendered" style="display:none;line-height:1.7;font-size:15px;"></div>
      <div id="note-meta" style="font-size:12px;color:var(--text-secondary);margin-top:12px;padding-top:8px;border-top:1px solid var(--border);"></div>
      <div id="note-save-status" style="font-size:13px;margin-top:8px;"></div>
    </div>
  `;

  const titleInput = page.querySelector('#note-title-input');
  const contentInput = page.querySelector('#note-content-input');
  const saveBtn = page.querySelector('#note-save');
  const editToggle = page.querySelector('#note-edit-toggle');
  const rendered = page.querySelector('#note-rendered');
  const metaEl = page.querySelector('#note-meta');
  const statusEl = page.querySelector('#note-save-status');
  const loadingEl = page.querySelector('#note-detail-loading');
  const errorEl = page.querySelector('#note-detail-error');
  const bodyEl = page.querySelector('#note-detail-body');

  let isEditing = false;
  let noteData = null;

  function toggleEdit() {
    isEditing = !isEditing;
    titleInput.style.display = isEditing ? 'block' : 'none';
    contentInput.style.display = isEditing ? 'block' : 'none';
    rendered.style.display = isEditing ? 'none' : 'block';
    saveBtn.style.display = isEditing ? 'inline-block' : 'none';
    editToggle.textContent = isEditing ? '预览' : '编辑';
    if (!isEditing && noteData) {
      renderMarkdown();
    }
  }

  function renderMarkdown() {
    const title = titleInput.value || noteData.title || '';
    const content = contentInput.value || noteData.content || '';
    let html = '';
    if (title) html += '<h1 style="font-size:20px;margin-bottom:12px;">' + escHtml(title) + '</h1>';
    if (content) {
      // Simple markdown rendering
      html += content
        .split('\n')
        .map(line => {
          line = escHtml(line);
          if (/^#{1,6}\s/.test(line)) {
            const level = line.match(/^#+/)[0].length;
            const text = line.replace(/^#+\s*/, '');
            return `<h${level} style="margin:12px 0 6px;font-size:${Math.max(14, 20 - level * 2)}px;">${text}</h${level}>`;
          }
          if (/^- `\[.+?\]` /.test(line) || /^- \*\*.+?\*\*: /.test(line)) {
            return '<p style="margin:4px 0;">' + line + '</p>';
          }
          if (/^- /.test(line)) {
            return '<li style="margin:2px 0 2px 16px;">' + line.replace(/^- /, '') + '</li>';
          }
          if (/^\d+\. /.test(line)) {
            return '<li style="margin:2px 0 2px 16px;">' + line.replace(/^\d+\. /, '') + '</li>';
          }
          if (/^> /.test(line)) {
            return '<blockquote style="border-left:3px solid var(--primary);padding:4px 12px;margin:6px 0;color:var(--text-secondary);">' + line.replace(/^> /, '') + '</blockquote>';
          }
          if (/^`{3}/.test(line)) return '<pre style="background:var(--bg);padding:8px;border-radius:4px;overflow-x:auto;font-size:13px;">';
          if (/^`{3}$/.test(line)) return '</pre>';
          return '<p style="margin:4px 0;">' + line + '</p>';
        })
        .join('\n');
    }
    rendered.innerHTML = html;
  }

  async function loadNote(id) {
    loadingEl.style.display = 'block';
    errorEl.style.display = 'none';
    bodyEl.style.display = 'none';

    // Try cache first
    const cached = window.notesCache ? await window.notesCache.getNote(id) : null;

    try {
      const data = await window.api.getNote(id);
      noteData = data.note;
      // Update cache
      if (window.notesCache) await window.notesCache.updateNote(noteData);
      showNote();
    } catch (err) {
      if (cached) {
        noteData = cached;
        showNote();
        showToast('显示缓存数据');
        return;
      }
      loadingEl.style.display = 'none';
      errorEl.style.display = 'block';
      errorEl.innerHTML = `
        <div style="color:var(--danger);font-size:14px;margin-bottom:12px;">加载失败: ${escHtml(err.message)}</div>
        <button class="btn btn-outline" id="note-retry" style="width:auto;padding:8px 24px;margin:0 auto;">重试</button>
      `;
      errorEl.querySelector('#note-retry').addEventListener('click', () => loadNote(id));
    }
  }

  function showNote() {
    loadingEl.style.display = 'none';
    errorEl.style.display = 'none';
    bodyEl.style.display = 'block';
    editToggle.style.display = 'inline-block';

    titleInput.value = noteData.title || '';
    contentInput.value = noteData.content || '';
    titleInput.style.display = 'none';
    contentInput.style.display = 'none';
    saveBtn.style.display = 'none';
    rendered.style.display = 'block';
    isEditing = false;
    editToggle.textContent = '编辑';

    renderMarkdown();

    metaEl.innerHTML = `
      更新: ${noteData.updated_at ? new Date(noteData.updated_at).toLocaleString() : '-'}
      ${noteData.tags && noteData.tags.length ? ' | 标签: ' + noteData.tags.join(', ') : ''}
      ${noteData.favorite ? ' | ⭐ 收藏' : ''}
    `;
  }

  async function saveNote() {
    const title = titleInput.value.trim();
    const content = contentInput.value.trim();
    if (!title && !content) { showToast('请输入内容'); return; }

    saveBtn.disabled = true;
    statusEl.textContent = '保存中...';
    statusEl.style.color = 'var(--text-secondary)';

    try {
      const data = await window.api.updateNote(params.id, { title, content });
      noteData = data.note;
      if (window.notesCache) await window.notesCache.updateNote(noteData);
      statusEl.textContent = '✅ 已保存';
      statusEl.style.color = 'var(--success)';
      showNote(); // Switch back to view mode
    } catch (err) {
      statusEl.textContent = '❌ ' + err.message;
      statusEl.style.color = 'var(--danger)';
    }
    saveBtn.disabled = false;
  }

  page.querySelector('#note-back').addEventListener('click', () => window.router.navigate('notes'));
  editToggle.addEventListener('click', () => {
    toggleEdit();
    if (isEditing) {
      // Focus content on edit
      setTimeout(() => contentInput.focus(), 100);
    }
  });
  saveBtn.addEventListener('click', saveNote);

  if (params.id) {
    loadNote(params.id);
  } else {
    // New note mode
    loadingEl.style.display = 'none';
    bodyEl.style.display = 'block';
    isEditing = true;
    titleInput.style.display = 'block';
    contentInput.style.display = 'block';
    saveBtn.style.display = 'inline-block';
    editToggle.style.display = 'none';
    rendered.style.display = 'none';
  }

  return page;
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

window.router.register('note-detail', () => ({
  render: renderNoteDetail,
  destroy: () => {},
}));

})();
