/* 思忆集 PWA — 任务列表视图 */
(function () {
  'use strict';

function renderTasksList() {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="font-size:16px;font-weight:600;">任务</h3>
      <button class="icon-btn" id="tasks-refresh" title="刷新">⟳</button>
    </div>
    <div id="pull-indicator" class="pull-indicator" style="display:none;">↓ 下拉刷新</div>
    <div id="tasks-list"></div>
    <div id="tasks-skeleton" style="display:none;padding:8px 0;"></div>
    <div id="tasks-loading" class="spinner" style="display:none;margin:20px auto;"></div>
    <div id="tasks-error" style="display:none;text-align:center;padding:20px 0;"></div>
  `;

  loadTasks(page);
  page.querySelector('#tasks-refresh').addEventListener('click', () => loadTasks(page, true));

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
      loadTasks(page, true).then(() => {
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
    html += `<div class="card" style="padding:12px 16px;display:flex;align-items:center;gap:8px;">
      <div class="skeleton" style="width:20px;height:20px;border-radius:50%;flex-shrink:0;"></div>
      <div style="flex:1;">
        <div class="skeleton" style="width:${50 + Math.random() * 40}%"></div>
        <div class="skeleton" style="width:30%;height:10px;margin-top:4px;"></div>
      </div>
    </div>`;
  }
  return html;
}

async function loadTasks(page, reset = false) {
  const container = page.querySelector('#tasks-list');
  const skeleton = page.querySelector('#tasks-skeleton');
  const spinner = page.querySelector('#tasks-loading');
  const errorEl = page.querySelector('#tasks-error');

  if (reset) container.innerHTML = '';

  // Show skeleton on first load
  if (!container.children.length || reset || container.innerHTML.includes('没有任务')) {
    skeleton.style.display = 'block';
    skeleton.innerHTML = skeletonHtml(5);
    errorEl.style.display = 'none';
  } else {
    spinner.style.display = 'block';
  }

  try {
    const data = await window.api.getTasks();
    skeleton.style.display = 'none';
    spinner.style.display = 'none';

    // Update cache
    if (window.tasksCache) {
      window.tasksCache.setAll(data.tasks || []).catch(() => {});
    }

    if (!data.tasks || !data.tasks.length) {
      container.innerHTML = '<div class="empty-state"><div class="icon">✅</div><p>没有任务</p></div>';
      return;
    }

    const pending = data.tasks.filter(t => !t.is_completed);
    const completed = data.tasks.filter(t => t.is_completed);

    let html = '';
    if (pending.length) {
      html += `<h4 style="font-size:13px;color:var(--text-secondary);margin:8px 0;">待办 (${pending.length})</h4>`;
      pending.forEach(t => { html += taskItemHtml(t); });
    }
    if (completed.length) {
      html += `<h4 style="font-size:13px;color:var(--success);margin:8px 0;">已完成 (${completed.length})</h4>`;
      completed.forEach(t => { html += taskItemHtml(t); });
    }
    container.innerHTML = html;

    container.querySelectorAll('.list-item').forEach(el => {
      el.addEventListener('click', () => window.router.navigate('task-detail', { id: el.dataset.id }));
    });
  } catch (err) {
    skeleton.style.display = 'none';
    spinner.style.display = 'none';

    // Try cache if offline
    if (window.tasksCache && (!container.children.length || reset)) {
      try {
        const cached = await window.tasksCache.getAll();
        if (cached && cached.length) {
          container.innerHTML = '';
          const pending = cached.filter(t => !t.is_completed);
          const completed = cached.filter(t => t.is_completed);
          let html = '';
          if (pending.length) html += `<h4 style="font-size:13px;color:var(--text-secondary);margin:8px 0;">待办 (${pending.length})</h4>` + pending.map(t => taskItemHtml(t, true)).join('');
          if (completed.length) html += `<h4 style="font-size:13px;color:var(--success);margin:8px 0;">已完成 (${completed.length})</h4>` + completed.map(t => taskItemHtml(t, true)).join('');
          container.innerHTML = html;
          container.querySelectorAll('.list-item').forEach(el => {
            el.addEventListener('click', () => window.router.navigate('task-detail', { id: el.dataset.id }));
          });
          showToast('显示缓存数据');
          return;
        }
      } catch (e) { /* ignore */ }
    }

    errorEl.style.display = 'block';
    errorEl.innerHTML = `
      <div style="color:var(--danger);font-size:14px;margin-bottom:12px;">加载失败</div>
      <button class="btn btn-outline" id="tasks-retry" style="width:auto;padding:8px 24px;margin:0 auto;">重试</button>
    `;
    errorEl.querySelector('#tasks-retry').addEventListener('click', () => loadTasks(page, reset));
  }
}

function taskItemHtml(t, isCached = false) {
  return `<div class="list-item" data-id="${t.remote_id || t.id}" style="display:flex;align-items:center;gap:8px;">
    <span style="flex:1;">${escHtml(t.name || t.title || '无标题')}</span>
    ${t.is_completed ? '<span style="color:var(--success);">✓</span>' : ''}
    ${isCached ? '<span style="font-size:10px;color:var(--text-secondary);">(缓存)</span>' : ''}
  </div>`;
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

window.router.register('tasks', () => ({
  render: renderTasksList,
  destroy: () => {},
}));

})();
