# 架构符合性复审 — 近期全部改动逐项裁定（2026-09-16）

> 复审范围：S3-1 / S3-2 / NL-1 / S3-5 U1-U6（主仓 2ccd9b0→144b26c 链 + 子仓 2d68f30→4601c58 链）。
> 复审口径：是否契合「壳 + 可替换内核」架构、是否触及根因、消费面是否真实存在、是否引入新契约断裂。
> 本轮为纯审计，未做任何代码改动。

---

## 0. 总结论（一张表）

| 案号 | 改动 | 裁定 | 严重度 |
|---|---|---|---|
| S3-2 | 六处 RPC 异步化（入队信封） | **回退 RPC 入口切换，保留骨架**（重新设计消费面后再启用） | **P0 — 正在破坏已上线功能** |
| S3-1 | 里程碑弱词 LLM 判评 | 保留，补可观测性 | 低 |
| NL-1 | add_job 幂等去重 | 保留，但语义存疑，登记观察项 | 低-中 |
| U6 | settings.get 诚实错误 | 保留（治根因） | — |
| U2 | cron.toggle_batch + 一键静音 | 保留（治根因，双端齐） | — |
| U3 后端 | revoke 三处半截全修 + entry_id 归一 + memory.delete | 保留（治根因），附 1 个设计债 | 低 |
| U3 前端 | QuickSearch 字段错位修复 + 删除按钮 | 保留（治根因） | — |
| U1 | 无改动（销案） | 正确 | — |
| 元问题 | 门禁体系抓不住跨仓契约断裂 | **需补机制**（这是所有上述漏网的共因） | P1 |

---

## 1. S3-2：六处慢 RPC 异步化 —— 唯一「与消费面不符被强行启用」的改动，裁定回退切换

### 1.1 取证（今日实测，全部行号可复核）

后端（主仓 `思忆集app/`，task_queue 在生产**恒可用**——`backend/headless_main.py:779-784` 只要 local_data_store 存在即构建，无降级可能）：

| RPC | 后端行为（现状） | 前端消费方 | 前端期望形状 | 实际后果 |
|---|---|---|---|---|
| `translate.text` | `_ai_translate.py:294-300` 入队秒回 `{success,job_id,status:"queued"}` | `TranslationModule.tsx:156` `transResp?.translated` (30s 超时) | `{translated: str}` | **translated 恒为 undefined → 快速翻译永远返回空**，且因秒回不再超时，症状从「慢」变成「静默空结果」 |
| `translate.full` | `_ai_translate.py:342-348` 同上 | `TranslationModule.tsx:159` `fullResp?.translated/words/grammar` (90s) | `{translated,words,grammar}` | **深度翻译（生词卡+语法）整块功能坏死** |
| `evolution.scan_and_report` | `_evolution.py:66-77` 同上 | `ManagementModule.tsx:121-123` `if (res?.error) … else 显示"扫描完成"+立刻 reload` | 同步终局结果 | **入队瞬间即报"扫描完成"**——假成功话术，且 reload 时扫描还没跑完，列表恒为旧数据 |
| `personal.review` | `_personal.py:170-177` 同上 | `PersonalModule.tsx:105-106` `setReview(res)` | 回顾内容 dict | **UI 渲染的是 job_id 信封**，回顾内容不可见 |
| `knowledge.brief` | `_inbox_knowledge.py:376-383` 同上 | **前端零消费者**（brief 走 proactive 事件推送） | — | 死改动，无害但无益 |
| `report.daily_summary` | `_info_proxy.py:211-218` 同上 | **前端零消费者**（日报投递收件箱） | — | 同上 |

全前端 grep `job_id.*status` 轮询模式：**除 job_hunter（S3-2 之前的既有样板）外，没有任何消费方调用过这 6 个 `*.status` 轮询器**。子仓 `shared/src/rpc/methods.ts` 亦无登记。

### 1.2 为什么这是「治标且引入新病」

- **想治的病是真的**：30-90 秒同步等待确实「慢到像坏」。异步化方向本身与架构不冲突（task_queue 是既有机制，job_hunter 是既有样板）。
- **病根诊断错了**：这 6 处的真问题是「前端没有等待/进度 UI 的耐心预算」，正确顺序应是 **先建前端异步消费层（轮询 + 进度指示），再把后端入口切过去**。实际执行顺序反了——后端先切、前端从未适配。agent-I 交付的端到端测试（c430bee）全部按**信封形状**测后端自洽，没有一条测「前端真实消费形状」→ 19 条测试绿灯掩盖了 4 处用户可见坏死。这正是统一执行方案里定义的**共因 A（信封不忠实）**的复发，且这次是修复本身制造的信封不忠实。
- **与「已完成」口径不符**：MEMORY 记为「S3 质量批收口」，实际是「后端半边收口、前端半边从未开始」。

### 1.3 修正方案（契合当前架构）

**立即（回退批，改动极小）**：
1. 把 6 个 RPC 入口改回直接调用各自的真实现（`run_translate_text` / `run_translate_full` / `_run_personal_review_sync` 等——S3-2 已把这些抽成了独立函数，这正是它留下的**有价值的资产**，回退只是删掉 `enq = await _queued_rpc(...)` 那 4 行×6 处）。行为回到改动前（慢但正确）。
2. **保留**：`_async_rpc.py` 骨架、`cron_actions.py` 六组 worker 注册（665-1247 行）、POLLER_SCHEMA、5 个 `.status` 轮询器、`*_completed` 事件——它们零运行成本（无人入队即无人执行），是 S3-2b 的正确底座。
3. 同步改测试：`test_async_rpc_s32` / `test_async_rpc_contract_s32` 中断言「RPC 入口返回信封」的用例改为断言「入口同步返回终局结果；入队路径经 worker 直调仍成立」。
4. 回退批必须新增一条**契约回归测试**（防再犯）：对每个有前端消费者的 RPC，断言后端返回 dict 的键集 ⊇ 前端 TS 类型声明读取的键集（可用测试内硬编码键清单实现，键清单抄自子仓调用点）。

**以后（S3-2b，真异步化批次，独立立项）**：前端先做「提交→拿 job_id→轮询 .status→进度/结果渲染」的消费层（TranslationModule / ManagementModule / PersonalModule 三处），vitest 覆盖信封消费，全绿后再翻转后端入口。届时「慢到像坏」才算真治好。

---

## 2. S3-1：里程碑弱词 LLM 判评（2ccd9b0）—— 保留

- 契合架构：走 `ai_text_service.complete` json_mode 单次调用，守 I3（壳内轻量 AI 禁工具循环）；候选≤50/次、阈值 0.8、判评不可用即 drop——**宁缺毋滥是 fail-closed，方向正确**。
- 非强行粘贴：针对的是图谱里真实存在的弱词垃圾（历史实测「今天」「好的」成节点）。
- 附带问题（登记，不回退）：drop 是静默的——与全项目「失败反馈体系性缺失」同病。建议后续给 drop 计数发一个 `knowledge.judge.dropped` 事件，接入 AgentMonitor 即可，改动一行级别。

## 3. NL-1：add_job 同名同规格幂等去重（a4a8fb0）—— 保留，语义存疑观察

- 破案本身是对的：真根因 = `test_integration_boundary.py:109-125` 直连后端的 `cron.add` 残渣（zcode×8），非 `_ensure_*` bug；残渣已清（kv 23→15）。
- 去重是不是掩盖？**部分是**：它没有修复「集成测试为何能写进真实后端」这个隔离问题（conftest 声称 `SIYI_INTEGRATION_AUTOSTART=1` 走隔离实例，但残渣出现在了真实盘面——隔离失效的最后一环未钉死）。且「同名+同规格=重复」对用户是**可能合法**的意图（我要两个一模一样的提醒）。
- 裁定：保留（它确实止住了增量污染），但拆两个后续：① 钉死 conftest 集成测试的数据根隔离（写探针断言测试期 kv 无任何 job 增长）；② 若产品确认「允许完全相同的两个 job」，把去重从「静默复用既有」降为「返回 `warning: duplicate` 照常创建」。

## 4. U6：settings.get 伪成功 → 诚实错误（92808a5）—— 保留

治根因的教科书例：原返回 `{"message":…, "hint":"/api/v1/settings…"}` 既是伪成功（客户端无法判成败）又是作废 REST 迁移的残留话术。改后与全仓 handler 错误结构一致；前端零依赖已核实（无任何消费方读 `hint`）。无保留意见。

## 5. U2：cron.toggle_batch + 一键静音（144b26c + 子仓 2d68f30）—— 保留

- 根因真实：方案 §6 声称「批量 disable 已在」为不实，缺的是批量原语（N 次 toggle_job = N 次锁+落盘），补的正是这个，单锁一次落盘。
- 双端齐：后端 schema 注册 + 前端按钮/确认/`isAutonomousJob` 分类 + 各自测试。这是 U1-U6 里唯一「后端+前端+测试」同批闭环的项。
- 语义边界干净：只动自主循环类 job（cron/daily/weekly/monthly/interval），手动 one-shot 不误伤。

## 6. U3 后端：revoke 软删全链路（144b26c）—— 保留，附一个设计债

- 这是对「被撤销的记忆会复活」的**根因修复**，不是包装：三处半截（hybrid 召回不过滤 / 同步进引擎不过滤 / rebuild 冲掉标记）一次修全，加 `_pending_sync` 使撤销即时生效。`entry_id` 归一补写同样治根（legacy 与 hybrid 两路字段名分叉）。
- `memory.delete` RPC 语义定为软删=revoke，正确——与「永不物理删除」的改动标注基线一致。
- **设计债登记（不回退）**：`_async_rpc.py:57` 的 `_completed_results` 进程内内存字典是执行结果的第二个真源（队列 SQLite 是第一个），违反共因 C「双状态源」的教训——重启即失、多进程即分裂。当前因「前端无消费者」而无人受害；**S3-2b 启用异步消费前必须先解决**（正解：TaskRecord 加 result 列，或 poll 时从 capability 侧补取），否则异步化会在结果读取面复制当年的双源问题。

## 7. U3 前端：QuickSearch 信封修复 + 删除按钮（子仓 4601c58）—— 保留

四字段全错位（text/score/tags/timestamp vs entry_id/title/snippet/source_label/updated_at）是实测的「恒空白行」真 bug，修复即治根因。删除按钮依赖 memory.delete（已入库）；恢复/回收站 UI 归入已拍板的「记忆管理面板」（下一步），不构成半截功能——撤销后最坏情况是「删了暂时看不见列表」，数据本身软删可恢复，无损失。

## 8. U1：等待计时（销案）—— 处理正确

实查后确认 ChatView 已有逐秒计时 + 九类话术，未写任何代码。这是「报告口径与盘面不符，破案靠独立复核」的正例。

---

## 9. 元问题：为什么这类「强行加」能过全部本地门禁？（P1，建议立项）

S3-2 的断裂不是运气差，是结构洞：**主仓门禁（gate_all 11 道 + schema 棘轮）全部只审后端自洽，跨仓契约（后端信封 ↔ 子仓消费类型）无任何一道门禁覆盖**。CI 从未运行（P0-5）又拿掉了最后一层外部审视。两个可选机制（选一即可）：

1. **契约矩阵测试**（推荐，轻）：主仓维护 `tests/rpc_contract_matrix.json`：`{method, 前端消费文件:行, 期望键集}`，对表内每个方法断言返回键集；后端信封化改动不改表就红。
2. 把 S3-2 回退批第 4 条的契约回归测试扩成全量：从子仓 grep 所有 `client.call<T>` 的 T 键集，自动生成断言。

不立这个机制，下一轮「后端改形状」还会重演 S3-2。

---

## 10. 追加：U3 记忆管理面板（未建，先审）——原方案数据源有误，修正后再建

应你要求对「面板照建与否」做同口径审计。**面板本身保留**（你拍板的产品需求，记忆可撤销面是信任基础设施），**但原计划的数据源经不起复核**，若照建就是 S3-2 式「强行粘贴到名不副实的接口上」：

### 10.1 取证

1. **`memory.recent` 名不副实，不可作列表源**：实现 = `recall("")`（`_chat_memory.py:813-821`），而 `recall` 对空 query 的处理是 **兜底替换成用户 focus topics 前 3 项做相关性召回**（`index.py:187-188`）——返回的是「按关注话题相关性排的条目」，不是「最近记忆」；无时间排序、语义随配置漂移。
2. **硬性 20 条上限**：`index.py:222` `min(top_k, 20)` → 列表页永远最多 20 条，无 offset，无翻页原语。
3. **「恢复」后端不存在**：全仓 grep `restore|un_revoke` = 0 命中。revoked 条目在 recall/引擎同步面被全量过滤（U3 刚修的行为，正确），但因此面板既取不到「已撤销列表」（无 `include_revoked` 的 RPC 面），也没有恢复动作可调。`get_memories_by_type(include_revoked=True)`（index.py:859）有该参数但非 RPC、按 memory_type 过滤、无时间排序分页。

### 10.2 契合根因的修正方案（改后顺序执行）

- **批 1 后端原语补齐**（小，同 §U3 已修的 revoke 链路对称）：
  - `index.py` 新增 `list_entries(limit, offset, include_revoked)` —— 全量 JSON 扫描 + updated_at 倒序 + 直读 entries 不走 recall；
  - `index.py` 新增 `restore_memory(entry_id, reason)` —— 清 revoked 标记 + `updated_at` + 审计 + `_pending_sync=True`（revoked 过滤已修 → 恢复自动回引擎，复用现链路）；
  - RPC：`memory.list`（分页列表，含 revoked 标记字段）+ `memory.restore`；i18n 键；测试（分页/排序/revoked 显隐/restore 后再 recall 命中/引擎重同步）。
- **批 2 前端面板**（子仓）：双 tab「生效中 / 已撤销(可恢复)」，行=标题+来源标签+时间+操作钮；删除→memory.delete、恢复→memory.restore。溯源跳转：source_type/source_label 字段已在信封里，跳转目标按来源分派。
- **`memory.recent` 不改不废**：它语义是「focus 相关性快速面」，现有消费方（smoke/验收脚本）不受扰；面板不依赖它，审计登记为观察项（若产品将来确要「最近」语义再立新案改造）。

## 11. 建议执行顺序（等你拍板）

1. **S3-2 回退批**（P0，纯后端小改 + 测试改形状）：6 处入口恢复同步直调 run_*，骨架/worker/轮询器全保留。
2. `_completed_results` 双源方案随 S3-2b 立项，不在回退批动。
3. 契约矩阵门禁（1 项新测试文件）。
4. NL-1 两个后续（conftest 隔离钉死 / 去重语义）并入下批。
5. U3 记忆管理面板（你已拍板「现在就建」）照常。
