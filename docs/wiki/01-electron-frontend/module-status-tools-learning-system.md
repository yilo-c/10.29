# 模块现状评估 · 工具 / 学习 / 系统类

> 状态：✅ 现行（代码为准，实测核对）
> 生成日期：2026-09-20
> 方法：以代码为唯一真源核验。手段 = 行数统计 + `.call()` 精确提取真实 RPC（过滤 i18n 文案噪声）+ `callRpc()` 封装补捞 + 同名 `.test.tsx` 覆盖比对。每条结论带 `文件:行号`，可逐条复核。
> 范围：Electron 桌面端 `desk-notes/packages/desktop/src/components/` 下的工具 / 学习 / 系统类模块。

---

## 0. 一句话结论

这一层**全部是真实后端接线的成熟实现，无空壳**。真正的缺口不是"功能缺失"，而是**测试覆盖不均**——多个大体量、含外部 AI 依赖与调度算法的模块**没有同名测试**，是回归风险集中处。

---

## 1. 工具类

### AutomationModule（824 行）— cron 调度控制台
- **真实 RPC**：`cron.add` / `cron.list` / `cron.history` / `cron.run_now` / `cron.status` / `cron.toggle` / `cron.toggle_batch` + `ai_chat.list_actions`
- **能力**：定时任务的增删、启停、批量启停、立即执行、执行历史查看；与 `services/cron_service.py`（APScheduler + polling 双引擎）对应。
- **测试**：✅ 有 `AutomationModule.test.tsx`
- **风险点**：`toggle_batch` 批处理失败的回滚路径是否被测试覆盖，待核。

### SettingsModule（740 行）— AI / 备份 / 数据 / 设备配对设置
- **真实 RPC**：`data.import_chat`、`data.import_chat_folder`、`pairing.approve`、`pairing.pending`
- **能力**：聊天数据导入、设备配对审批；其余设置项多经 store / util 落库，不在本文件内直连。
- **测试**：⚠️ 仅 `SettingsModuleSidebarMeta.test.tsx`（测侧边栏元信息），**导入 / 配对主流程无测试**。
- **风险点**：第二大模块，主流程裸奔。

### OCRModule（235 行）— OCR + 语音转写
- **真实 RPC**：`ocr.extract_base64`、`ocr.status`、`transcribe.transcribe_base64`、`transcribe.status`
- **能力**：base64 图片 OCR、音频转写。
- **测试**：❌ 无同名测试
- **风险点**：大图 base64 上传的体积上限 / 失败降级分支需回归。

### TransferView（344 行）— 跨设备传送
- **真实 RPC**：`transfer.send`、`transfer.history`、`notes.create`；剪贴板图片 `clipboard.png`
- **能力**：文本 / 文件跨端传送、发送历史、落为笔记。
- **测试**：❌ 无同名测试
- **风险点**：`fileTooLarge` 等边界目前仅停留在文案层（`transfer.fileTooLarge`）。

### TranslationModule（506 行）— 翻译 + 语法分析
- **真实 RPC**：`translate.text`、`translate.full`；含语法从句 / 例句分析（`translation.grammar*`）
- **测试**：✅ 有 `TranslationModule.test.tsx`

---

## 2. 学习类

### FlashcardModule（613 行）— FSRS 间隔重复卡
- **数据层**：走 `callRpc('wordbook.list')` 等**封装调用**（`FlashcardModule.tsx:4` import `getWsClient`；`:99` `callRpc<...>('wordbook.list')`），**不是本地壳**。
- **算法证据**：`next_review` / `last_review`（`:30`）、`QUALITY_CONFIG` / `PRESET_DECKS`（`:7`）——典型 FSRS 调度字段。
- **测试**：❌ 无同名测试
- **风险点**：FSRS 调度（后端 `_fsrs_scheduler`）× 前端 review 流是最重要回归点，却裸奔。
- **核验教训**：初版正则只匹配 `.call(` 会漏 `callRpc()`，差点误判为本地壳——务必双模式提取。

### WordbankModule（586 行）— 词库 SRS
- **真实 RPC**：`word.add` / `word.delete` / `word.review` / `word.list` / `word.list_due` / `word.enrich_cloud` / `word.generate_examples` / `word.update` + `translate.word_detail`
- **测试**：❌ 无同名测试
- **风险点**：`enrich_cloud` / `generate_examples` 是外部 AI 依赖调用，失败降级路径关键。

---

## 3. 系统 / 管理类

### ManagementModule（288 行）— 监督式自优化 / 审计控制台
- **真实 RPC**：`audit.list`、`audit.stats`、`timeline.list` + `evolution.scan_and_report` / `confirm_upgrade` / `final_apply` / `skip_upgrade` / `last_briefing` / `status`
- **能力**：与 AGENTS.md「maintenance.proposals 白名单审批 / 每日 05:00 巡检只生成提案不执行」一致，执行须用户审批。
- **测试**：✅ 有 `ManagementModule.test.tsx`

### TaskQueueModule（182 行）— 后台任务队列控制台
- **真实 RPC**：`task_queue.list` / `cancel` / `stats` / `purge_completed`、`task_queue.dead_letter.list` / `requeue`
- **能力**：持久化任务队列（崩溃恢复）+ 死信处理。
- **测试**：✅ 有 `TaskQueueModule.test.tsx`

### NotificationModule（267 行）— 通知中心
- **真实 RPC**：`notification.list` / `mark_read` / `mark_all_read` / `delete` / `stats`
- **订阅事件**：`inbox.delivered` / `sync.failed` / `task.due` / `vocabulary.due_reminder` / `proactive.triggered` / `ai.usage_alert` / `job_hunter.new_jobs`（与 AGENTS.md 通知白名单吻合）
- **测试**：✅ 有 `NotificationModule.test.tsx`

### StatisticsModule（347 行）— 本地派生统计
- **能力**：completionRate / trends / dailyAvg 等，主要基于 store 计算，后端直连少。
- **测试**：❌ 无同名测试
- **备注**：因是纯派生计算，最适合做纯函数单测（低成本高收益）。

---

## 4. 测试覆盖缺口汇总（① 的客观依据）

| 模块 | 行数 | 同名测试 | 缺口性质 |
|---|---|---|---|
| OCRModule | 235 | ❌ | 外部上传/失败分支 |
| TransferView | 344 | ❌ | 边界仅文案 |
| FlashcardModule | 613 | ❌ | FSRS 调度回归裸奔 |
| WordbankModule | 586 | ❌ | 云 AI 依赖降级 |
| SettingsModule（主流程） | 740 | ⚠️ 部分 | 导入/配对无测 |
| StatisticsModule | 347 | ❌ | 纯函数易测未测 |
| AutomationModule | 824 | ✅ | 批处理回滚待核 |
| ManagementModule | 288 | ✅ | — |
| TaskQueueModule | 182 | ✅ | — |
| NotificationModule | 267 | ✅ | — |
| TranslationModule | 506 | ✅ | — |

叠加前一轮已确认的**内容/知识层缺口**：`KnowledgeGraphModule`(670)、`NotesModule`、`TodoModule` 亦无同名测试。

## 5. 建议的测试落地顺序（①）
按你指定的顺序：**KnowledgeGraph → Flashcard → AIManagement → Notes → Todo**。
原则：每个先读真实渲染 / 事件 / RPC，再落笔；`AIManagementModule.tsx` 目前在子仓为**未提交 WIP**，须待其干净后再动。

---

*本文档以代码为准；若后续代码演进，请同步更新本文件与状态标记。*
