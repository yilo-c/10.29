/* 思忆集 PWA — 笔记列表视图 */
(function () {
  'use strict';

function renderNotesList() {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="font-size:16px;font-weight:600;">全部笔记</h3>
      <button class="icon-btn" id="notes-refresh" title="刷新">⟳</button>
    </div>
    <div id="pull-indicator" class="pull-indicator" style="display:none;">↓ 下拉刷新</div>
    <div id="notes-list"></div>
    <div id="notes-skeleton" style="display:none;padding:8px 0;"></div>
    <div id="notes-loading" class="spinner" style="display:none;margin:20px auto;"></div>
    <div id="notes-error" style="display:none;text-align:center;padding:20px 0;"></div>
  `;

  loadNotes(page);
  page.querySelector('#notes-refresh').addEventListener('click', () => loadNotes(page, true));

  // ── Pull-to-refresh (touch) ─────────────────────
  let startY = 0;
  let pulling = false;
  const contentEl = document.getElementById('app-content');
  const pullIndicator = page.querySelector('#pull-indicator');

  page.addEventListener('touchstart', (e) => {
    if (contentEl.scrollTop <= 0) {
      startY = e.touches[0].clientY;
      pulling = true;
    }
  }, { passive: true });

  page.addEventListener('touchmove', (e) => {
    if (!pulling) return;
    const diff = e.touches[0].clientY - startY;
    if (diff > 40) {
      pullIndicator.textContent = '释放刷新';
      pullIndicator.style.display = 'block';
    } else if (diff > 10) {
      pullIndicator.textContent = '↓ 下拉刷新';
      pullIndicator.style.display = 'block';
    } else {
      pullIndicator.style.display = 'none';
    }
  }, { passive: true });

  page.addEventListener('touchend', () => {
    if (pulling && pullIndicator.style.display === 'block' && pullIndicator.textContent === '释放刷新') {
      pullIndicator.textContent = '刷新中...';
      loadNotes(page, true).then(() => {
        pullIndicator.style.display = 'none';
      });
    }
    pulling = false;
    pullIndicator.style.display = 'none';
  });

  return page;
}

function skeletonHtml(count) {
  let html = '';
  for (let i = 0; i < count; i++) {
    html += `<div class="card" style="padding:12px 16px;">
      <div class="skeleton" style="width:${60 + Math.random() * 30}%"></div>
      <div class="skeleton" style="width:${80 + Math.random() * 20}%;height:12px;"></div>
      <div class="skeleton" style="width:30%;height:10px;"></div>
    </div>`;
  }
  return html;
}

let notesCursor = null;
let notesLoading = false;

async function loadNotes(page, reset = false) {
  const container = page.querySelector('#notes-list');
  const skeleton = page.querySelector('#notes-skeleton');
  const spinner = page.querySelector('#notes-loading');
  const errorEl = page.querySelector('#notes-error');

  if (reset) {
    notesCursor = null;
    container.innerHTML = '';
  }

  if (notesLoading) return;
  notesLoading = true;

  // Show skeleton on first load (no content yet)
  if (!container.children.length || reset || container.innerHTML.includes('empty-state')) {
    skeleton.style.display = 'block';
    skeleton.innerHTML = skeletonHtml(5);
    errorEl.style.display = 'none';
  } else {
    spinner.style.display = 'block';
  }

  try {
    const data = await window.api.getNotes(notesCursor);
    skeleton.style.display = 'none';
    spinner.style.display = 'none';

    // Update cache
    if (window.notesCache && (!notesCursor || reset)) {
      window.notesCache.setAll(data.notes || []).catch(() => {});
    }

    if (!data.notes || !data.notes.length) {
      if (!notesCursor) {
        container.innerHTML = '<div class="empty-state"><div class="icon">📝</div><p>还没有笔记</p></div>';
      }
      notesLoading = false;
      return;
    }

    if (reset || !notesCursor) {
      container.innerHTML = '';
    }

    data.notes.forEach(n => {
      const el = document.createElement('div');
      el.className = 'list-item';
      el.innerHTML = `
        <div class="card-title">${escHtml(n.title || '无标题')}</div>
        <div class="card-sub">${escHtml((n.content || '').slice(0, 100))}</div>
        <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">${n.updated_at ? new Date(n.updated_at).toLocaleDateString() : ''}</div>
      `;
      el.addEventListener('click', () => window.router.navigate('note-detail', { id: n.remote_id || n.id }));
      container.appendChild(el);
    });

    notesCursor = data.next_cursor || null;
  } catch (err) {
    skeleton.style.display = 'none';
    spinner.style.display = 'none';

    // Try cache if offline
    if (window.notesCache && (!container.children.length || reset)) {
      try {
        const cached = await window.notesCache.getAll();
        if (cached && cached.length) {
          container.innerHTML = '';
          cached.forEach(n => {
            const el = document.createElement('div');
            el.className = 'list-item';
            el.innerHTML = `
              <div class="card-title">${escHtml(n.title || '无标题')}</div>
              <div class="card-sub">${escHtml((n.content || '').slice(0, 100))}</div>
              <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">${n.updated_at ? new Date(n.updated_at).toLocaleDateString() : ''} (缓存)</div>
            `;
            el.addEventListener('click', () => window.router.navigate('note-detail', { id: n.remote_id || n.id }));
            container.appendChild(el);
          });
          showToast('显示缓存数据');
          notesLoading = false;
          return;
        }
      } catch (e) { /* ignore */ }
    }

    errorEl.style.display = 'block';
    errorEl.innerHTML = `
      <div style="color:var(--danger);font-size:14px;margin-bottom:12px;">加载失败: ${escHtml(err.message)}</div>
      <button class="btn btn-outline" id="notes-retry" style="width:auto;padding:8px 24px;margin:0 auto;">重试</button>
    `;
    errorEl.querySelector('#notes-retry').addEventListener('click', () => loadNotes(page, reset));
  }

  notesLoading = false;
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

window.router.register('notes', () => ({
  render: renderNotesList,
  destroy: () => { notesCursor = null; notesLoading = false; },
}));

})();
