# 手机端复刻桌面端能力 — 进度说明

> 目标：手机端通过 `@desk-notes/shared` 的 `WsClient` 单例直连桌面后端 WebSocket（端口 18765），按需访问全部 103 个 JSON-RPC 方法；同时保留 HTTP `/rpc` 作为离线/失败回落通道。

## 总览

```
┌───────────── Mobile (Capacitor + React) ─────────────┐
│  UI 组件                                              │
│     │                                                 │
│     ▼  src/lib/bridges/*.ts （按领域薄封装）          │
│  ┌──────────────────────────────────────────────┐    │
│  │  callRpc()  +  callRpcStream()  +  onEvent()  │    │
│  │  (src/lib/rpcClient.ts)                       │    │
│  └──────────────────────────────────────────────┘    │
│           │                          │                │
│           ▼ 优先                    ▼ 回落              │
│   shared WsClient 单例         fetch /rpc (HTTP)       │
└───────────────────┬─────────────────────────────────┘
                    │ WebSocket (ws://host:18765/ws)
                    ▼
                桌面后端
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `src/lib/rpcClient.ts` | WS 优先 / HTTP 回落的 `callRpc`、`callRpcStream`、`onEvent`、`configureWs`、`ensureWsConnected` |
| `src/lib/eventBridge.ts` | 把服务端推送（`sync.completed/failed`、`inbox.new`、`sandbox.requested`、`capability.requested`、`memory.surface`、`weather.refreshed`、`reminder.fired`、`mood.changed`、`agent.state.changed`、`usage.stats.updated`、`settings.changed`）扇出给消费者，sync 事件直接写回 `useMobileStore` |
| `src/lib/chatSocket.ts` | 兼容旧 ChatView 公共 API 的薄包装，内部委托 shared `WsClient` |
| `src/lib/bridges/` | 按领域拆分的 RPC 薄封装（详见下表） |

### bridges/ 一览

| 模块 | 覆盖 RPC |
|------|---------|
| `reminderBridge` | `reminder.create/list/delete` |
| `tagsBridge` | `tags.list`（带 5 分钟缓存） |
| `inboxBridge` | `inbox.add/list/classify` + 启动时 `pullInbox()` + `inbox.new` 事件合并 |
| `notesBridge` | `notes.list/get/create/update/delete/auto_tag/generate_summary` |
| `tasksBridge` | `tasks.list/get/create/update/delete/toggle` |
| `memoryBridge` | `memory.search/recent/related/related_text/surface_for_input/save_conversation_anchor/narrative.*` |
| `dailyBridge` | `daily.get/append` |
| `weatherBridge` | `weather.current`（15 分钟本地缓存） |
| `settingsBridge` | `settings.get/set` + `settings.changed` 事件订阅（带 10s 回环保护） |
| `vocabBridge` | `wordbook.*` + `word.*`（含 `word.dictation_check`）+ `translate.*`（含 `word_detail/extract_words/text/languages` + `translations.save`） |
| `systemBridge` | `system.status/info/health/active_tasks/tailscale_status` + `agent.state.summary` + `capability.*` + `usage.*` + `sandbox.preview` |
| `providerBridge` | `provider.list/add/update/remove/health` + `api_keys.*` + `model.list` |
| `aiBridge` | `ai.parse_intent` + `pattern.execute` + `chat.list_conversations/get_conversation/history/save_messages/compress_context` + `streamChatSend` |
| `learningBridge` | `url_learning.learn` + `info.brief/auto_config/fetch_proxy/personalize` + `report.generate` + `data.import_chat/import_chat_folder` + `knowledge.search/relations/unified_query` + `ocr.extract_text/extract_base64` |
| `syncBridge` | `sync.status/sync_now/configure/enable/disable/register_device` + `transfer.*` + `reading.*`（推送/文章） |

## 桌面专属（手机端不复刻）

- **Orb / 系统托盘 / ForceGraph 三维可视化**：与触控范式冲突；
- **`api_keys` 编辑器内联式**：手机端只暴露 `list/test`，敏感写操作仅在解锁后允许；
- **`pattern.execute` 的内联代码编辑器**：手机端只暴露调用，不暴露编辑；
- **后端启停 / 调试控制台**。

## 启动流程

`src/App.tsx` 在挂载时按顺序：
1. `configureWs(tokenManager.baseUrl)` 把 `__SIYI_WS_URL__` 推到全局；
2. `ensureWsConnected()` 拉起 shared WsClient 单例；
3. `registerEventBridge()` 注册全部服务端事件订阅；
4. `startInboxBridge()` 拉一次 `inbox.list` 并订阅 `inbox.new`。

卸载时全部清理，保证热重载/重新配对时干净。

## 业务调用示例

```ts
import {
  searchMemory, fetchCurrentWeather, getRemoteSetting, setRemoteSetting,
  bindRemoteSetting, listProviders, parseIntent, streamChatSend, getSystemHealth,
} from '../lib/bridges'

// 搜索记忆
const items = await searchMemory('深度学习')

// 拉天气
const weather = await fetchCurrentWeather()

// 跨设备设置同步
const theme = await getRemoteSetting<string>('theme')
await setRemoteSetting('theme', 'dark')
const unbind = bindRemoteSetting('theme', (_k, value) => applyThemeLocally(value as string))

// 健康检查
const health = await getSystemHealth(true)

// 解析意图
const intent = await parseIntent('帮我每天 8 点提醒喝水')

// 流式聊天（兼容 ChatView 的桌面 SSE 流，也可直接用这个）
for await (const chunk of streamChatSend('你好', sessionId)) {
  if (chunk.type === 'chunk') appendText(chunk.data)
}
```

## 回落与降级

- **WS 不可达**：`callRpc` 自动 fetch `/rpc`，业务无感；
- **未配对**：`callRpc` 抛 `Backend not paired`，bridge 函数捕获后返回空数组 / null；
- **桌面无该方法**：HTTP 404/JSON-RPC error → 业务函数返回 null/false，调用方可走纯本地路径。

## 后续

- 把 `useStore` 的部分 settings/词库等域改为"启动时拉远端 + 写时双写"模式（settingsBridge 已就绪）；
- 给 ChatView 增加"流式失败→HTTP 重试"路径；
- 在 SettingsView 暴露 `getSystemHealth` 看板。
