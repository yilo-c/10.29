/**
 * appBridgeClient.ts — QWebChannel bridge client for 思忆集 desktop app.
 *
 * Provides typed wrappers around the QWebChannel AppBridge object injected
 * by PyQt6 via QWebChannel. All service calls return Promises.
 *
 * Usage:
 *   import { siyiApi } from './appBridgeClient';
 *   const notes = await siyiApi.notes.getNotes('recent');
 *
 * Signal subscriptions:
 *   import { bridgeSignals } from './appBridgeClient';
 *   bridgeSignals.onDataChanged((key, payload) => { ... });
 */

// ── Types ───────────────────────────────────────────────────────

interface ServiceResult {
  ok: boolean;
  data: any;
  error: string | null;
}

interface SiyiBridge {
  // Signals (Python → JS)
  data_changed: {
    connect(callback: (key: string, payload: any) => void): void;
  };
  system_notification: {
    connect(callback: (title: string, message: string, level: string) => void): void;
  };
  pet_mood_changed: {
    connect(callback: (petId: string, mood: string) => void): void;
  };
  sync_status_changed: {
    connect(callback: (status: string, message: string, progress: number) => void): void;
  };

  // Slot (JS → Python)
  call_service(
    service_name: string,
    method: string,
    params: object | null,
    callback: (result: ServiceResult) => void,
  ): void;
}

// ── Bridge accessor ─────────────────────────────────────────────

function getBridge(): SiyiBridge {
  const bridge = (window as any).bridge as SiyiBridge | undefined;
  if (!bridge) {
    throw new Error('QWebChannel bridge not available. Ensure the app is running with QWebEngineView.');
  }
  return bridge;
}

// ── Generic call wrapper ────────────────────────────────────────

async function callService<T>(
  service: string,
  method: string,
  params: Record<string, any> = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    try {
      getBridge().call_service(service, method, params, (result: ServiceResult) => {
        if (result.ok) {
          resolve(result.data as T);
        } else {
          reject(new Error(result.error || 'Unknown error'));
        }
      });
    } catch (e) {
      reject(e);
    }
  });
}

// ── Signal subscriptions ────────────────────────────────────────

export const bridgeSignals = {
  onDataChanged(callback: (key: string, payload: any) => void): () => void {
    getBridge().data_changed.connect(callback);
    return () => {}; // disconnect not supported by QWebChannel JS API
  },

  onSystemNotification(
    callback: (title: string, message: string, level: string) => void,
  ): () => void {
    getBridge().system_notification.connect(callback);
    return () => {};
  },

  onPetMoodChanged(callback: (petId: string, mood: string) => void): () => void {
    getBridge().pet_mood_changed.connect(callback);
    return () => {};
  },

  onSyncStatusChanged(
    callback: (status: string, message: string, progress: number) => void,
  ): () => void {
    getBridge().sync_status_changed.connect(callback);
    return () => {};
  },
};

// ── Data types (mirroring Python service contracts) ───────────────

export interface Note {
  id: string;
  title: string;
  content: string;
  created: string;
}

export interface CreateNoteInput {
  title: string;
  content?: string;
}

export interface UpdateNoteInput {
  title?: string;
  content?: string;
}

export interface Task {
  id: string;
  title: string;
  done: boolean;
}

export interface CreateTaskInput {
  title: string;
  description?: string;
}

export interface InboxItem {
  id: string;
  content: string;
  source: string;
  status?: string;
}

export interface UsageStats {
  snapshot: {
    today_cost: number;
    month_cost: number;
    total_calls: number;
    tier_breakdown: Record<string, number>;
  };
  daily: {
    date: string;
    total_cost: number;
    total_calls: number;
    tier_breakdown: Record<string, { cost: number; count: number }>;
    vs_pure_cloud_saving?: {
      pure_cloud_estimated: number;
      actual_cost: number;
      savings_percent: number;
    };
  };
  monthly: {
    total_cost: number;
    total_calls: number;
    days_tracked?: number;
  };
}

// NewsFeed removed — news module was deprecated in 2026-05 cleanup

export interface ServiceHealth {
  check_type: string;
  status: string;
  issues: Array<{type: string; msg: string}>;
}

export interface SyncStatus {
  status: string;
  last_sync: string | null;
  progress: number;
}

export interface Settings {
  theme: string;
  language: string;
}

export interface PetMood {
  pet_id: string;
  mood: string;
  applied: boolean;
  real: boolean;
}

// ── Typed service API ────────────────────────────────────────────

export const siyiApi = {
  doctor: {
    runCheck: (checkType?: string) =>
      callService<ServiceHealth>('doctor', 'run_check', { check_type: checkType }),
    getReport: (reportId?: string) =>
      callService<any>('doctor', 'get_report', { report_id: reportId }),
    getDebugAssets: () =>
      callService<any>('doctor', 'get_debug_assets'),
  },

  assistant: {
    ask: (query: string, context?: Record<string, any>) =>
      callService<any>('assistant', 'ask', { query, context }),
    getSuggestions: () =>
      callService<any[]>('assistant', 'get_suggestions'),
    executeAction: (actionId: string, params?: Record<string, any>) =>
      callService<any>('assistant', 'execute_action', { action_id: actionId, params }),
    getUsageStats: () =>
      callService<UsageStats>('assistant', 'get_usage_stats'),
  },

  sync: {
    triggerSync: (domain?: string) =>
      callService<any>('sync', 'trigger_sync', { domain }),
    getStatus: () =>
      callService<SyncStatus>('sync', 'get_status'),
    getHistory: (limit?: number) =>
      callService<any[]>('sync', 'get_history', { limit }),
  },

  pet: {
    launch: (petId?: string) =>
      callService<any>('pet', 'launch', { pet_id: petId }),
    close: (petId?: string) =>
      callService<any>('pet', 'close', { pet_id: petId }),
    setMood: (petId: string, mood: string) =>
      callService<PetMood>('pet', 'set_mood', { pet_id: petId, mood }),
    getMoods: (petId?: string) =>
      callService<string[]>('pet', 'get_moods', { pet_id: petId }),
  },

  settings: {
    getAll: () =>
      callService<Settings>('settings', 'get_all'),
    update: (key: string, value: any) =>
      callService<any>('settings', 'update', { key, value }),
    testApiConnection: (apiType: string, config: Record<string, any>) =>
      callService<any>('settings', 'test_api_connection', { api_type: apiType, config }),
  },

  database: {
    query: (table: string, filters?: Record<string, any>) =>
      callService<any[]>('database', 'query', { table, filters }),
    insert: (table: string, data: Record<string, any>) =>
      callService<any>('database', 'insert', { table, data }),
    update: (table: string, id: string, data: Record<string, any>) =>
      callService<any>('database', 'update', { table, id, data }),
    delete: (table: string, id: string) =>
      callService<any>('database', 'delete', { table, id }),
  },

  inbox: {
    getItems: (filter?: string) =>
      callService<InboxItem[]>('inbox', 'get_items', { filter }),
    addItem: (content: string, source?: string) =>
      callService<any>('inbox', 'add_item', { content, source }),
    summarize: () =>
      callService<any>('inbox', 'summarize'),
  },

  notes: {
    getNotes: (filter?: string) =>
      callService<Note[]>('notes', 'get_notes', { filter }),
    createNote: (input: CreateNoteInput) =>
      callService<Note>('notes', 'create_note', input),
    updateNote: (id: string, updates: Partial<UpdateNoteInput>) =>
      callService<Note>('notes', 'update_note', { id, ...updates }),
    deleteNote: (id: string) =>
      callService<void>('notes', 'delete_note', { id }),
  },

  tasks: {
    getTasks: (filter?: string) =>
      callService<Task[]>('tasks', 'get_tasks', { filter }),
    createTask: (input: CreateTaskInput) =>
      callService<Task>('tasks', 'create_task', input),
    updateTask: (id: string, updates: Partial<CreateTaskInput>) =>
      callService<Task>('tasks', 'update_task', { id, ...updates }),
    completeTask: (id: string) =>
      callService<Task>('tasks', 'complete_task', { id }),
  },

};
