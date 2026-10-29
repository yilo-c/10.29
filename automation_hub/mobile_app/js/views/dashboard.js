/* 思忆集 PWA — 仪表盘视图 */
(function () {

function renderDashboard(params) {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <div id="dash-recent" style="margin-bottom:16px;">
      <h3 style="font-size:14px;font-weight:600;margin-bottom:8px;color:var(--text-secondary);">最近笔记</h3>
      <div id="dash-notes-list"></div>
    </div>
    <div id="dash-tasks" style="margin-bottom:16px;">
      <h3 style="font-size:14px;font-weight:600;margin-bottom:8px;color:var(--text-secondary);">活跃任务</h3>
      <div id="dash-tasks-list"></div>
    </div>
  `;

  loadDashboard(page);
  return page;
}

async function loadDashboard(page) {
  const notesContainer = page.querySelector('#dash-notes-list');
  const tasksContainer = page.querySelector('#dash-tasks-list');

  try {
    const data = await window.api.getDashboard();

    if (data.recent_notes && data.recent_notes.length) {
      notesContainer.innerHTML = data.recent_notes.map(n => `
        <div class="card" style="cursor:pointer;" data-id="${n.remote_id || n.id}">
          <div class="card-title">${escHtml(n.title || '无标题')}</div>
          <div class="card-sub">${escHtml((n.content || '').slice(0, 80))}</div>
        </div>
      `).join('');
      notesContainer.querySelectorAll('.card').forEach(el => {
        el.addEventListener('click', () => {
          window.router.navigate('note-detail', { id: el.dataset.id });
        });
      });
    } else {
      notesContainer.innerHTML = '<div class="card" style="color:var(--text-secondary);text-align:center;">暂无笔记</div>';
    }

    if (data.active_tasks && data.active_tasks.length) {
      tasksContainer.innerHTML = data.active_tasks.map(t => `
        <div class="list-item" data-id="${t.id}" style="display:flex;align-items:center;gap:8px;">
          <span style="flex:1;">${escHtml(t.title || '无标题')}</span>
        </div>
      `).join('');
      tasksContainer.querySelectorAll('.list-item').forEach(el => {
        el.addEventListener('click', () => {
          window.router.navigate('task-detail', { id: el.dataset.id });
        });
      });
    } else {
      tasksContainer.innerHTML = '<div class="card" style="color:var(--text-secondary);text-align:center;">暂无活跃任务</div>';
    }
  } catch (err) {
    notesContainer.innerHTML = '<div class="card" style="color:var(--danger);text-align:center;">加载失败</div>';
    tasksContainer.innerHTML = '';
  }
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

window.router.register('dashboard', () => ({
  render: renderDashboard,
  destroy: () => {},
}));

})();
