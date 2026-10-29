/* 思忆集 PWA — 任务详情视图 */
(function () {
  'use strict';

  const STATUS_LABELS = {
    todo: '待办',
    in_progress: '进行中',
    completed: '已完成',
  };

  const STATUS_CYCLES = ['todo', 'completed'];

  const PRIORITY_LABELS = {
    high: { text: '高优先级', cls: 'badge-danger' },
    medium: { text: '中优先级', cls: 'badge-warning' },
    low: { text: '低优先级', cls: 'badge-info' },
  };

function renderTaskDetail(params) {
  const page = document.createElement('div');
  page.className = 'view-page';
  page.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px;">
      <button class="btn btn-outline" id="task-back" style="width:auto;padding:8px 16px;">← 返回</button>
      <button class="btn btn-primary" id="task-status-toggle" style="width:auto;padding:8px 16px;display:none;">切换状态</button>
    </div>
    <div id="task-detail-loading"><div class="spinner" style="margin:40px auto;"></div></div>
    <div id="task-detail-error" style="display:none;text-align:center;padding:40px 0;"></div>
    <div id="task-detail-body" style="display:none;">
      <div class="card">
        <div class="card-title" id="task-title" style="font-size:18px;font-weight:600;"></div>
        <div id="task-note" style="margin-top:8px;font-size:14px;color:var(--text);line-height:1.6;"></div>
        <div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:8px;">
          <span class="badge" id="task-status-badge"></span>
          <span class="badge" id="task-priority-badge"></span>
        </div>
        <div id="task-due" style="margin-top:8px;font-size:13px;color:var(--text-secondary);"></div>
        <div id="task-meta" style="margin-top:8px;font-size:12px;color:var(--text-secondary);padding-top:8px;border-top:1px solid var(--border);"></div>
      </div>
      <div id="task-status-msg" style="font-size:13px;text-align:center;margin-top:12px;"></div>
    </div>
  `;

  const loadingEl = page.querySelector('#task-detail-loading');
  const errorEl = page.querySelector('#task-detail-error');
  const bodyEl = page.querySelector('#task-detail-body');
  const titleEl = page.querySelector('#task-title');
  const noteEl = page.querySelector('#task-note');
  const statusBadge = page.querySelector('#task-status-badge');
  const priorityBadge = page.querySelector('#task-priority-badge');
  const dueEl = page.querySelector('#task-due');
  const metaEl = page.querySelector('#task-meta');
  const statusMsg = page.querySelector('#task-status-msg');
  const toggleBtn = page.querySelector('#task-status-toggle');

  let taskData = null;
  let currentStatus = 'todo';

  function getStatus(task) {
    return task.is_completed ? 'completed' : 'todo';
  }

  function getPriority(task) {
    if (task.priority && PRIORITY_LABELS[task.priority]) return task.priority;
    return null;
  }

  function getNextStatus(current) {
    const idx = STATUS_CYCLES.indexOf(current);
    return STATUS_CYCLES[(idx + 1) % STATUS_CYCLES.length];
  }

  function updateUI() {
    titleEl.textContent = escHtml(taskData.name || taskData.title || '无标题');
    noteEl.textContent = taskData.note || taskData.description || '';

    currentStatus = getStatus(taskData);
    const statusLabel = STATUS_LABELS[currentStatus] || '待办';

    statusBadge.textContent = statusLabel;
    statusBadge.className = 'badge';
    if (currentStatus === 'completed') {
      statusBadge.classList.add('badge-success');
    } else {
      statusBadge.classList.add('badge-info');
    }

    // Priority
    const prio = getPriority(taskData);
    if (prio && PRIORITY_LABELS[prio]) {
      priorityBadge.textContent = PRIORITY_LABELS[prio].text;
      priorityBadge.className = 'badge ' + PRIORITY_LABELS[prio].cls;
      priorityBadge.style.display = 'inline-block';
    } else {
      priorityBadge.style.display = 'none';
    }

    // Due date
    if (taskData.reminder_time) {
      dueEl.textContent = '截止: ' + new Date(taskData.reminder_time).toLocaleString();
      dueEl.style.display = 'block';
    } else {
      dueEl.style.display = 'none';
    }

    metaEl.innerHTML = `
      创建: ${taskData.created_at ? new Date(taskData.created_at).toLocaleString() : '-'}
      | 更新: ${taskData.updated_at ? new Date(taskData.updated_at).toLocaleString() : '-'}
    `;

    toggleBtn.textContent = '→ ' + STATUS_LABELS[getNextStatus(currentStatus)];
    toggleBtn.style.display = 'inline-block';

    statusMsg.textContent = '';
  }

  async function loadTask(id) {
    loadingEl.style.display = 'block';
    errorEl.style.display = 'none';
    bodyEl.style.display = 'none';

    // Try cache first
    const cached = window. tasksCache ? await window.tasksCache.getTask(id) : null;

    try {
      const data = await window.api.getTask(id);
      taskData = data.task;
      if (window.tasksCache) await window.tasksCache.updateTask(taskData);
      showTask();
    } catch (err) {
      if (cached) {
        taskData = cached;
        showTask();
        showToast('显示缓存数据');
        return;
      }
      loadingEl.style.display = 'none';
      errorEl.style.display = 'block';
      errorEl.innerHTML = `
        <div style="color:var(--danger);font-size:14px;margin-bottom:12px;">加载失败: ${escHtml(err.message)}</div>
        <button class="btn btn-outline" id="task-retry" style="width:auto;padding:8px 24px;margin:0 auto;">重试</button>
      `;
      errorEl.querySelector('#task-retry').addEventListener('click', () => loadTask(id));
    }
  }

  function showTask() {
    loadingEl.style.display = 'none';
    errorEl.style.display = 'none';
    bodyEl.style.display = 'block';
    updateUI();
  }

  async function toggleStatus() {
    const nextStatus = getNextStatus(currentStatus);
    toggleBtn.disabled = true;
    statusMsg.textContent = '切换中...';
    statusMsg.style.color = 'var(--text-secondary)';

    try {
      const data = await window.api.patchTask(params.id, { status: nextStatus });
      taskData = data.task;
      if (window.tasksCache) await window.tasksCache.updateTask(taskData);
      updateUI();
      statusMsg.textContent = '✅ 已切换为 ' + STATUS_LABELS[nextStatus];
      statusMsg.style.color = 'var(--success)';
    } catch (err) {
      statusMsg.textContent = '❌ ' + err.message;
      statusMsg.style.color = 'var(--danger)';
    }
    toggleBtn.disabled = false;
  }

  page.querySelector('#task-back').addEventListener('click', () => window.router.navigate('tasks'));
  toggleBtn.addEventListener('click', toggleStatus);

  if (params.id) {
    loadTask(params.id);
  }

  return page;
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

window.router.register('task-detail', () => ({
  render: renderTaskDetail,
  destroy: () => {},
}));

})();
