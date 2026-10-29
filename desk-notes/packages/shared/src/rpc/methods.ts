/* ── RPC 方法常量 — 消除桌面端/手机端硬编码方法名 ── */

export const RpcMethods = {
  system: {
    ping: 'ping',
    status: 'system.status',
    info: 'system.info',
    activeTasks: 'system.active_tasks',
  },
  chat: {
    send: 'chat.send',
    history: 'chat.history',
    saveMessages: 'chat.save_messages',
    listConversations: 'chat.list_conversations',
    getConversation: 'chat.get_conversation',
  },
  notes: {
    list: 'notes.list',
    create: 'notes.create',
    get: 'notes.get',
    update: 'notes.update',
    delete: 'notes.delete',
    listTrashed: 'notes.list_trashed',
    restore: 'notes.restore',
    permanentDelete: 'notes.permanent_delete',
    autoTag: 'notes.auto_tag',
    generateSummary: 'notes.generate_summary',
  },
  tasks: {
    list: 'tasks.list',
    create: 'tasks.create',
    get: 'tasks.get',
    update: 'tasks.update',
    delete: 'tasks.delete',
    toggle: 'tasks.toggle',
  },
  transfer: {
    send: 'transfer.send',
    history: 'transfer.history',
    upload: 'transfer.upload',
    download: 'transfer.download',
    files: 'transfer.files',
    delete: 'transfer.delete',
    tokenInfo: 'transfer.token_info',
  },
  inbox: {
    list: 'inbox.list',
    add: 'inbox.add',
    update: 'inbox.update',
    relay: 'inbox.relay',
    classify: 'inbox.classify',
  },
  reading: {
    push: 'reading.push',
    get: 'reading.get',
    list: 'reading.list',
    delete: 'reading.delete',
    saveArticle: 'reading.save_article',
    listArticles: 'reading.list_articles',
    deleteArticle: 'reading.delete_article',
  },
  wordbook: {
    list: 'wordbook.list',
    create: 'wordbook.create',
    rename: 'wordbook.rename',
    delete: 'wordbook.delete',
  },
  word: {
    list: 'word.list',
    add: 'word.add',
    update: 'word.update',
    delete: 'word.delete',
    review: 'word.review',
    listDue: 'word.list_due',
  },
  knowledge: {
    search: 'knowledge.search',
    graph: 'knowledge.graph',
    relations: 'knowledge.relations',
    traverse: 'knowledge.traverse',
    unifiedQuery: 'knowledge.unified_query',
  },
  web: {
    search: 'web.search',
    fetch: 'web.fetch',
  },
  ocr: {
    extractText: 'ocr.extract_text',
    extractBase64: 'ocr.extract_base64',
  },
  kernel: {
    status: 'kernel.status',
  },
  info: {
    brief: 'info.brief',
    cachedItems: 'info.cached_items',
    summarize: 'info.summarize',
    personalize: 'info.personalize',
  },
  settings: {
    get: 'settings.get',
    set: 'settings.set',
  },
  apiKeys: {
    list: 'api_keys.list',
    create: 'api_keys.create',
    update: 'api_keys.update',
    delete: 'api_keys.delete',
    test: 'api_keys.test',
  },
  provider: {
    list: 'provider.list',
    add: 'provider.add',
    remove: 'provider.remove',
    update: 'provider.update',
    health: 'provider.health',
  },
  sandbox: {
    preview: 'sandbox.preview',
    approve: 'sandbox.approve',
    reject: 'sandbox.reject',
    listPending: 'sandbox.list_pending',
  },
  urlLearning: {
    learn: 'url_learning.learn',
  },
  reminder: {
    list: 'reminder.list',
    create: 'reminder.create',
    delete: 'reminder.delete',
  },
  weather: {
    current: 'weather.current',
  },
  model: {
    list: 'model.list',
  },
} as const

export type RpcMethod = (typeof RpcMethods)[keyof typeof RpcMethods][keyof (typeof RpcMethods)[keyof typeof RpcMethods]]
