const { Notification, app, ipcMain } = require('electron')
const fs = require('fs')
const { state, NOTIFIED_FILE } = require('./state.cjs')
const { ensureDataDirs, atomicWriteFileSync } = require('./file-store.cjs')
const { _backendRpcCall } = require('./backend-relay.cjs')

let reminderTodos = []
let notifiedMap = {}
let reminderInterval = null
let _reminderPolling = false

function loadNotified() {
  try {
    if (fs.existsSync(NOTIFIED_FILE)) {
      notifiedMap = JSON.parse(fs.readFileSync(NOTIFIED_FILE, 'utf-8'))
    }
  } catch { /* ignore */ }
}

function saveNotified() {
  try {
    ensureDataDirs()
    atomicWriteFileSync(NOTIFIED_FILE, JSON.stringify(notifiedMap))
  } catch { /* ignore */ }
}

function showReminder(item) {
  if (!Notification.isSupported()) return
  const notification = new Notification({
    title: '⏰ 待办到期提醒',
    body: item.text,
  })
  notification.on('click', () => {
    if (state.win && !state.win.isDestroyed()) {
      state.win.show()
      state.win.focus()
    }
  })
  notification.show()
}

async function fetchTasksFromBackend() {
  try {
    const res = await _backendRpcCall('tasks.list', {})
    return res?.result || []
  } catch {
    return []
  }
}

async function pollReminders() {
  if (_reminderPolling) return
  _reminderPolling = true
  const now = Date.now()
  let overdueCount = 0
  let changed = false

  let todos = reminderTodos
  try {
    const backendTasks = await fetchTasksFromBackend()
    if (backendTasks && backendTasks.length > 0) {
      todos = backendTasks.map(t => ({
        id: t.id,
        text: t.title || t.text || '',
        done: t.is_completed || t.completed || false,
        dueDate: t.due_date ? new Date(t.due_date).getTime() : null,
        reminderTime: t.reminder_time ? new Date(t.reminder_time).getTime() : null,
      }))
    }
  } catch { /* fall back to reminderTodos */ }

  for (const todo of todos) {
    if (todo.done) continue
    const triggerTime = todo.reminderTime || todo.dueDate
    if (!triggerTime) continue
    if (triggerTime <= now) {
      overdueCount++
      const key = todo.id + ':due'
      if (!notifiedMap[key]) {
        showReminder(todo)
        notifiedMap[key] = true
        changed = true
      }
    }
  }

  if (changed) saveNotified()
  if (app.isReady()) {
    app.setBadgeCount(overdueCount)
  }
  _reminderPolling = false
}

function startReminderScheduler() {
  loadNotified()
  pollReminders()
  if (reminderInterval) clearInterval(reminderInterval)
  reminderInterval = setInterval(pollReminders, 60000)
}

function stopReminderScheduler() {
  if (reminderInterval) {
    clearInterval(reminderInterval)
    reminderInterval = null
  }
}

// ── IPC handler ─────────────────────────────────────────────────────────

ipcMain.handle('reminder:update', async (event, todos) => {
  if (!Array.isArray(todos)) return
  reminderTodos = todos
  const activeIds = new Set(todos.filter(t => !t.done && (t.reminderTime || t.dueDate)).map(t => t.id))
  for (const key of Object.keys(notifiedMap)) {
    const todoId = key.replace(':due', '')
    if (!activeIds.has(todoId)) {
      delete notifiedMap[key]
    }
  }
  pollReminders()
})

module.exports = {
  startReminderScheduler,
  stopReminderScheduler,
}
