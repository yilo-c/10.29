# 思忆集债务清零·补设计·执行清单 v2.0

> 重写：2026-08-23（v1 同日生成，生成时即已大面积过时，判定见 §0）
> 依据：代码实测核查（本文件所有论断附 `文件:行号` 证据）+ [ADR-0000 需求基线](adr/0000-product-requirements-and-architecture-direction.md) + [全栈评审报告 2026-08-23](../deliverables/思忆集全栈评审报告-2026-08-23.md) + [gstack 执行计划 v1.1](../deliverables/gstack/project-status-and-execution-plan-2026-08-22.md)
> 路线图真源仍是 gstack 计划；本文件是**债务清零 + 设计补课**专项清单，与其互补不重复（关系见附录 B）
> 执行环境：Windows / pwsh；仓库根 `D:\h\3.25\思忆集`；Python `.venv3.11\Scripts\python.exe`；全部命令在仓库根执行

## 给执行模型的前置指令（每次会话开头原文粘贴）

```text
你是执行工程师。规则：
1. 任务分两类：[执行] 任务零判断照做；[决策] 任务先向用户展示选项表和默认建议，得到确认后再执行。
2. [执行] 任务严格按编号顺序；做完立即运行"验收"命令，输出符合"通过条件"才输出 "[TASK-x] PASS"。
3. 验收不通过 -> 停止，输出 "[TASK-x] FAIL: <验收命令完整输出>"，等待人工指示。
4. 所有命令为 pwsh 语法；禁止 rm -rf 等 Unix 命令；删除仅用本清单给出的 Remove-Item。
5. 禁止修改本清单未列出的任何文件。
6. 禁止跳步、合并任务或自由发挥；遇到清单与代码不符 -> 停止报告，不自行裁决。
```

---

## 0. v1 处置结论：为什么重写

v1（本文件旧版）按"弱模型 100% 照做"标准编写，但经代码级核查：

1. **约 70% 任务已失去对象**——2026-08-22 晚的 T1.x/T3.x 提交（分支 `fix/p0-2026-06-18`，HEAD `c2d6e301`）已用不同方式完成或覆盖其阶段 2/3/4/6（判定表如下）。
2. **阶段 1 方向性错误**——v1 要注册的 `inbox_service` 属性在架构上**有意不存在**（真实属性 `assistant_inbox_service`；`思忆集app/tests/test_appcontext_startup_check.py:77` 明确断言 `inbox_service` 不得进启动白名单）。照做 v1 的 TASK-011/012 会与已落地的 P0-2 修复直接冲突。
3. **执行模型自相矛盾**——声称零判断，实际含判断题（TASK-010 标记定义/使用、TASK-011 A/B 分支、TASK-040 "以现有风格为准"）；命令混用 Unix 语法（`wc -l`/`tail`/`2>/dev/null`/`mkdir -p` 在 pwsh 下失败）；TASK-070 基线记录时序倒置；TASK-071 要求合并回 `main` 但仓库**无 main 分支**。
4. **文档自身违规**——未登记 `docs/INDEX.md`（违反工作区规则"新文档必登记"），而其主题恰是"文档防漂移"。

### v1 逐阶段判定表（核查证据）

| v1 阶段 | v1 内容 | 代码现状（2026-08-23 实测） | 判定 |
|---|---|---|---|
| 0 | 切分支、合并回 `main` | 本地仅 `fix/p0-2026-06-18` 一个分支，无 `main`；remote 有 origin/cto 两个但本地无任何 remote-tracking ref | ❌ 失效 |
| 1 | 注册 `inbox_service` + 预检 | 已有 `_context_builder.py:630` `_CRITICAL_ATTRIBUTES` 启动自检（含 `assistant_inbox_service`）+ `test_appcontext_startup_check.py` 防回归。**但 v1 描述的活故障确实存在残骸**（见问题 A-1） | ⚠️ 方向错误 / 残留有效 |
| 2 | git mv 16 文件 + .gitignore | 16 文件全部在磁盘且全部 untracked；`.gitignore` 已含 `_*.py`/`_old_*.*`/`$null`/`web_dist/`/`claudejiaoben/`/`archive/`（T3.1 已做 `git rm --cached`） | ❌ 已过时（磁盘清理仍有效，见 B-1） |
| 3 | 删 `python-ci.yml`、删 continue-on-error | `python-ci.yml` 已不存在（仅剩 android-build/desk-notes-ci/python-tests 三条流水线）；continue-on-error 已全部移除（T1.2 完成） | ❌ 无对象 |
| 4 | 契约测试加 callRpc/syncCall | 已完成（`思忆集app/tests/test_jsonrpc_contract.py:30-32` `RE_FRONTEND_CALL`，2026-08-20）。v1 的文件路径还是错的（写根 `tests/`，实际在 `思忆集app/tests/`） | ✅ 已完成 |
| 5 | `gen_inventory.py`（按 `def rpc_` 提取） | 未做；且 **handlers 中 `def rpc_` 前缀匹配数为 0**（方法靠 `registry.register("name", fn)` 注册）——照抄会得到 "OK: 0 methods" | ⚠️ 需求仍成立，方案作废 |
| 6 | 新建 `.pre-commit-config.yaml` | 已存在且更强（ruff+format+大文件门禁，T3.3） | ✅ 已完成 |

---

## 1. 现状基线快照（2026-08-23 实测）

| 项 | 状态 |
|---|---|
| 分支 | 仅 `fix/p0-2026-06-18`（HEAD `c2d6e301`），**无 main，无 upstream 跟踪** |
| 远端 | `origin` = github.com/vjkbhj54445/4.17-siyiji；`cto` = github.com/cto-hosted/f5cs67xg；本地无任何 remote-tracking ref |
| CI | `.github/workflows/`：android-build / desk-notes-ci / python-tests；ruff 增量门、裸 except 棘轮、bandit high 基线、mypy 棘轮均已真阻塞（T1.2） |
| 门禁 | pre-commit 已接线（ruff lint+format）；覆盖率门禁 20.0%（实测 20.9%） |
| RPC 方法 | 唯一注册名实测 355（正则口径；AGENTS/gstack 记 357，动态注册模块口径差异） |
| 子仓 | `desk-notes` 为无 `.gitmodules` 的 gitlink，自身独立 repo（HEAD `e7b363e`），仍有 17 个宠物资源文件未提交 |
| 活日志 | `backend.dev.log`（8-22 18:51 止）尾部躺着一个未处理 AttributeError（问题 A-2） |
| CI 实况 | 2026-08-24 更新：doc 一致性检查**已绿**（0 失败，commit `efce8312` 移除 19 份新旧双份）；但 `check_core_integrity.py` / `run_pip_audit.py` 两步引用幽灵脚本必红（A-6 -> M2-1）；coverage 21 > 20.91 为故意红（-> M2-2 显式豁免） |

---

## 2. 问题清单（全部代码实证）

### A. 活故障（P0，当日修）

**A-1 proactive.tick 死属性回归（v1 所述 P0 的真实残骸）**
- 证据：`思忆集app/services/cron_actions.py:66,73,82` 仍引用 `context.inbox_service`；派发点 `_context_cron.py:70` `execute_cron_action(self, ...)` 传入的 `self` 就是 AppContext 本体；AppContext（`app_context.py`，基类 Cron/Memory/ConfigContextMixin）**不存在** `inbox_service` 属性（全仓 grep 无 property/setattr/`__getattr__` 定义）。
- 后果：每次 tick 第 66 行被内层 `except` 吞成 debug 静默；第 82 行 `ProactiveTriggerEngine(inbox_service=context.inbox_service, ...)` 抛 AttributeError -> 外层 except -> **整个 proactive.tick 返回 failed**（warning "主动代理触发器执行失败"）。主动建议链路（ADR-0000 管家角色、next-phase 主线④）实际瘫痪。
- 历史：gstack 计划 §2.2 记 2026-08-21 "#1 修 proactive.tick 属性错误"，但后续 cron_actions 拆分/重构时**回归**——同文件 `:149` 注释表明 suggestion_daily 修过（改用 `personal_*_repository`），proactive.tick 三处漏改。这是"修了但没防线"复发模式的又一实例。

**A-2 graph_extractor LLM 返回形状未校验（backend.dev.log 现行错误）**
- 证据：`思忆集app/knowledge/graph_extractor.py:407-408` `json.loads(reply)` 后直接 `data.get(...)`；LLM 偶发返回 JSON 数组时抛 `AttributeError: 'list' object has no attribute 'get'`——`backend.dev.log` 尾部实测在案。knowledge.ingest_conversation / 知识入库 cron 受影响，属第二大脑主线（P0#4/#6）上的静默损耗。

**A-3 版本控制单点：全部债务冲刺成果仅存本机磁盘**
- 证据：`git branch -vv` 无 upstream；本地无任何 remote-tracking ref；remote origin/cto 已配置。T1–T3 系列 + 本 v2 的修复若不推送，一次磁盘故障即清零（对照 2026-07-18 源码丢失事故、`docs/PYC_REBUILD_RUNBOOK.md`）。

**A-4 文档数字核对机制缺位（gstack T2.3 交付物与声称不符）**
- 证据：gstack 计划 §5.3.1 T2.3 标 "✅ 完成 `scripts/check_doc_facts.py` + CI 步骤"；实测**全仓不存在** `check_doc_facts.py`（`Get-ChildItem -Recurse` 零命中），python-tests.yml 仅有 `check_doc_index_consistency.py`（:66）。AGENTS.md 数字（51/357）确实手工更正过，但防漂移的脚本与 CI 门禁**不存在**——"文档说做了但没做"，正是本项目反复出现的漂移模式。

**A-5 CI 文档一致性门禁当前红：归档操作只复制未移动（2026-08-23 核查新发现）**
- 证据：`python scripts/check_doc_index_consistency.py` 实测 19 项 `[FAIL] 新旧双份`；抽查 `docs/roadmap-next.md` 与 `docs/archive/roadmap-next.md` 字节级相同；`git ls-files docs/archive` 计数为 0（archive/ 已 gitignore、副本未跟踪），而 docs/ 原件仍被跟踪。
- 后果：该检查已接 CI（`python-tests.yml:66`）--当前每次 push 必红，一切"CI 全绿"验收基准失效。INDEX §四/§七 所记 2026-08-23 "已归档 19 份" 实际是复制到未跟踪目录后未 `git rm` 原件。

**A-6 CI 引用幽灵脚本：两个门禁步骤必炸（2026-08-24 执行期新发现）**
- `python-tests.yml:49` 引用 `scripts/check_core_integrity.py`、`:80` 引用 `scripts/run_pip_audit.py`，实测两者**均不存在**（scripts/ 全目录核对，同文件其余 10 个引用全部在场）。这两步从未可能绿过--"门禁加了但从未跑过"模式的又一实例（同类：A-5、T1.2 修过的 broad-except 缺脚本）。处置 -> TASK-M2-1。

**A-7 pre-commit 钩子从未安装（2026-08-24 执行期新发现）**
- `.pre-commit-config.yaml` 已入库（commit `0dcee3da`，T3.3 标 ✅），但本工作副本 `.git/hooks/pre-commit` **不存在**，本地提交全程无钩子检查。配置存在 ≠ 机制通电。处置 -> TASK-M1-2（install）+ TASK-M6（会话卫生守则）。

**A-8 事故遗留无源码 .pyc 仍挂活代码导入链（2026-08-24 执行期新发现）**
- 事实：`assistant/capability/_dynamic_tools.py:23-24` 以 `from ..generic_tool_bridge / from ..tool_spec` 导入，而 `assistant/` 下这两模块**只有 2026-07-18 事故遗留的 .pyc（无源码、未跟踪）**。实测 `assistant.tool_spec` 从 `assistant\tool_spec.pyc` 加载——生产路径（capability_router → DynamicToolDomain）跑**陈旧字节码**，且与代码库其余部分使用的 `services/tooling/`（08-15 迁移版）**并存为两份 ToolSpecRegistry**。`capability_router.py:36-43` 用 try/except 包住导入，失败会静默降级（DynamicToolDomain=None），掩盖问题。
- 暴露方式：M2-1 done_check 首跑，`check_no_sourceless_pyc` 守卫**首次真正抓到违例**（此前从未跑过）。
- 修复（已做）：导入切到 `services.tooling.*`（与 §5.4.1 迁移一致）+ 删除两个遗留 .pyc + 回归测试 `test_dynamic_tools_sourceless_import.py`（钉死必须来自迁移版）。SHA 见 `git log --grep A-8`。
- 教训（M5 病灶链延伸）：工具层迁移未同步更新旧相对导入——"迁移漏网"是新一类病灶。

### B. 治理与卫生债

- **B-1 根目录磁盘垃圾超出 v1 清单**：v1 列 16 个文件，实测另有 25 个 `siyi_desktop_*crash*.log`、4 个 `pytest_*.log`、`.obs-backend.log`、`electron.dev.log`、`manual_run.log`、`test_out.txt`、`smoke_ws.py`、`新建 文本文档.txt`（全部 untracked，多数已被 `*.log` 等 ignore 规则覆盖）。
- **B-2 本文件 v1 未登记 INDEX**（v2 一并修复，见 TASK-V1-04）。
- **B-3 desk-notes 子仓尾巴**：17 个文件未提交（宠物帧图/引擎 ts）；gitlink 无 `.gitmodules` 的结构债维持现状（T3.3 已论证不撬动）。
- **B-4 deprecated 方法仍在线**：`backend/handlers/_remote.py:94` `remote.shell` 自 S11（2026-08-07）标 deprecated 但仍注册，无下线时间表。
- **B-5 过期示例误导**：`services/content_organizer_service.py:82` docstring 教人写 `app_context.inbox_service`（死属性）。

### C. 从未设计的维度（v1 与 gstack T1–T4 均未覆盖；对照 ADR-0000）

- **C-1 安全设计无威胁模型页**：① WS token 支持经 URL `?token=` 传递（`ws_server.py:167`），会泄入代理/网关日志；② `cloudflare/worker/src/sync.ts` devices 表只有 SELECT/INSERT OR REPLACE，**无设备吊销**——手机丢失后其同步 token 永久有效；③ 动态工具 `tool.register` 是常驻特权执行面（risk=high 有沙箱，但移动端审批链路未定义）。
- **C-2 机密生命周期无统一设计**：env / settings.json / SecureKeyStorage（Windows DPAPI 不支持命名存取）/ rotation Fernet 四套并存；AGENTS 自认 `_default_secret_resolver` 第三段（settings 加密 token）**未实现**；E2E 同步口令（PBKDF2 派生）无轮换 runbook。
- **C-3 schema 演进无框架**：`local_storage` 有 `STORAGE_SCHEMA_VERSION`、settings 有迁移（局部好），但主 SQLite 无 `schema_migrations` 机制（automation_hub 有 Alembic，主应用没有）；sync envelope v4 有版本号但无 `min_client_version` 兼容协商——"旧桌面 + 新手机"混跑策略未设计。
- **C-4 需求对齐缺口（评审报告 §4 已定位，无任务承接）**：① C3 约束硬违反——automation_hub personal 域是第二套可写 CRUD 存储；② C5 约束缺口——行为采集在活跃 Electron 前端是盲区（PyQt6 monitor 禁用、前端零上报），"第二大脑"对应用内行为失明；③ P1#18 覆盖率 20.9% vs 目标 ≥80%，无上升路径任务。
- **C-5 发布工程缺口**：`docs/release/` 模板与回滚预案已有（2026-08-22），但 **git tag 为空、无 CHANGELOG、无版本号策略**。
- **C-6 依赖治理缺口**：`pip-audit.exe` 已装入 venv 但无基线/无 CI 接线；无 dependabot；pnpm audit 未接。
- **C-7 可观测闭环缺口**：计数器/健康面板已有（gstack §3.1），但无错误 digest——A-2 类错误在 log 里躺数月无人看见；cron 失败告警只有阈值（P0-4），无周期性汇总。

---

## 3. 执行计划

> 排序原则（承 gstack §5.1）：先止血与备份（V0）→ 治理对齐（V1）→ 需求对齐（V2，对应 ADR P0/P1 缺口）→ 设计补课（V3，穿插）→ 防线固化（V4）。

> **2026-08-24 增补（机制优先）**：执行 24 小时内本计划自身已漂移（C-6/V2-03 数小时内过时，A-6/A-7 为执行期新发现），证实 §0 对 v1 的病理判定同样适用于 v2 载体本身。新增 **§6 机制化闭环（M1-M7），优先级插到所有未完成任务之前**：M2（闭合一次绿灯）> M1（完成定义机械化）> V1 收尾 > V2/V3/V4。自 2026-08-24 起，一切"完成"以 M1 状态货币计（commit SHA + 该 commit CI 绿 + 已 push），纯文字 ✅ 不再有效。

### V0 止血（当日）

**TASK-V0-01 [决策+执行] 推送远端备份（对应 A-3）**
- 决策：推送到哪个远端？默认建议：**两个都推**（origin 为主，cto 为镜像），分支名不变。
- 命令：
  ```powershell
  git push -u origin fix/p0-2026-06-18
  git push -u cto fix/p0-2026-06-18
  ```
- 验收：`git branch -vv` 显示 `[origin/fix/p0-2026-06-18]`；GitHub 两仓库网页可见同一 HEAD。
- 通过：两条 push 无报错，`git rev-parse HEAD` 与远端一致。

**TASK-V0-02 [执行] 修 A-1：proactive.tick 三处死属性（文件：`思忆集app/services/cron_actions.py`）**
- 动作 1：在 `_register_proactive_tick` 的 `_handler` 函数体开头（`enabled = ...` 之前）插入：
  ```python
  inbox_svc = getattr(context, "assistant_inbox_service", None)
  ```
- 动作 2：`:66` `if context.inbox_service is not None:` 改为 `if inbox_svc is not None:`
- 动作 3：`:73` `context.inbox_service.deliver_suggestion(` 改为 `inbox_svc.deliver_suggestion(`
- 动作 4：`:82` `inbox_service=context.inbox_service,` 改为 `inbox_service=inbox_svc,`
- 动作 5：在 `思忆集app/tests/test_cron_actions.py` 末尾追加用例（仿该文件既有 stub 模式）：构造只含 `assistant_inbox_service=None`、`settings_repository`（`get_setting` 返回默认）、`task_repository/note_repository=None` 的 stub context，调 `execute_cron_action(ctx, "proactive.tick", {})`，断言返回 `status == "completed"`（缺失属性不再引发 failed）。
- 验收：
  ```powershell
  Select-String -Path 思忆集app/services/cron_actions.py -Pattern 'context\.inbox_service'
  .venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_cron_actions.py -q
  ```
- 通过：第一条**零输出**；pytest 全过（含新用例）。

**TASK-V0-03 [执行] 修 A-2：graph_extractor 形状校验（文件：`思忆集app/knowledge/graph_extractor.py`）**
- 动作 1：`:407` `data = json.loads(...)` 之后、`:408` `llm_entities = ...` 之前插入：
  ```python
  if not isinstance(data, dict):
      logger.warning("graph_extractor: LLM 返回非对象 JSON(%s)，回退模式提取", type(data).__name__)
      return self._extract_with_patterns(text, source_id)
  ```
- 动作 2：新建 `思忆集app/tests/test_graph_extractor_shapes.py`：mock `ai_client.complete_text` 分别返回 `'[{"e":1}]'`（list 形状）与 `'{"entities":[{"name":"测试","type":"tool"}],"relations":[]}'`，断言前者不抛异常且走 patterns 回退、后者正常产出。
- 验收：`.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_graph_extractor_shapes.py -q`
- 通过：`2 passed`。

**TASK-V0-04 [执行] 修 B-5：docstring 示例**
- 动作：`思忆集app/services/content_organizer_service.py:82` `inbox_service=app_context.inbox_service,` 改为 `inbox_service=app_context.assistant_inbox_service,`
- 验收：`Select-String -Path 思忆集app/services/content_organizer_service.py -Pattern 'app_context\.inbox_service'` 零输出。

**TASK-V0-05 [执行] 回归与提交**
- 命令：
  ```powershell
  .venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_cron_actions.py 思忆集app/tests/test_graph_extractor_shapes.py 思忆集app/tests/test_appcontext_startup_check.py -q
  git add 思忆集app/services/cron_actions.py 思忆集app/knowledge/graph_extractor.py 思忆集app/services/content_organizer_service.py 思忆集app/tests/test_cron_actions.py 思忆集app/tests/test_graph_extractor_shapes.py
  git commit -m "fix(P0): proactive.tick 死属性回归修复 + graph_extractor LLM 形状校验 (debt-v2 A-1/A-2/B-5)"
  git push
  ```
- 通过：pytest 零失败；push 成功。

### V1 治理对齐（本周）

**TASK-V1-01 [决策] 分支规范化（对应 A-3 尾巴）**
- 选项：A. 从当前 HEAD 建 `main` 并双推，`fix/p0-2026-06-18` 保留归档（**默认建议**——恢复常规语义，后续债务任务直接在 main 或短分支上做）；B. 维持单分支现状（零成本，但分支名持续误导）。
- 确认后执行（选 A 时）：
  ```powershell
  git branch main fix/p0-2026-06-18
  git push -u origin main
  git push -u cto main
  ```

**TASK-V1-02 [执行] 根目录磁盘清理（对应 B-1；全部 untracked，直接物理删除）**
- 命令（逐条；`$null` 必须 -LiteralPath）：
  ```powershell
  Remove-Item -LiteralPath '$null','_or_models.json','_analyze_sessions.py','_backend_methods.txt','_chat_reg_tmp.log','_extract_content.py','_frontend_methods.txt','_frontend_methods2.txt','_probe_mcp.py','_probe_tq.py','_trigger_collect.py','_sessions_content.txt','_sessions_summary.txt','_old_InfoFeedView.tsx','_old_InfoModule.tsx','_old_InfoSectionConfig.tsx','_old_InfoSectionPresets.ts','smoke_ws.py','test_out.txt','新建 文本文档.txt','.obs-backend.log','electron.dev.log','manual_run.log'
  Remove-Item 'siyi_desktop_crash_*.log','siyi_desktop_debug_crash_*.log','pytest_baseline_root.log','pytest_p03_diag.log','pytest_p03_diag2.log','pytest_stage1_regression.log'
  ```
- 保留：`backend.dev.log`/`backend.dev.err.log`（T4.1 灰度观察期对照证据，观察期结束再清）。
- 验收：`Get-ChildItem -File -Filter *_* | Measure-Object | Select -Expand Count` 与 `Get-ChildItem siyi_desktop_*.log` 均为预期（前者仅剩 `backend.dev*` 等保留项，后者零输出）。

**TASK-V1-03 [执行] 落地 inventory 生成 + CI 接线（对应 A-4 + v1 阶段 5 修正版）--2026-08-24 状态：完成**
- 状态（M1 货币）：`scripts/gen_inventory.py` 已建成（契约正则 + 动态前缀 + os.walk 目录剪枝扫 SIYI_*，避开 node_modules 坏符号链接）；`docs/inventory.md` 已生成（375 方法 / 86 变量）；`--check` 防漂移模式本地绿；INDEX 登记 + CI 步骤（用 `--check` 替代原计划的 `git diff --exit-code`，更强且与本地 done_check 复用同一命令）随本批更新同 commit 入库，SHA 见 `git log --grep V1-03`。
- 动作 1：新建 `scripts/gen_inventory.py`——**必须**复用 `思忆集app/tests/test_jsonrpc_contract.py:21-23` 的 `RE_BACKEND_REGISTER` 正则（`registry.register(?:_with_schema)?\s*\(\s*["']([^"']+)["']`）扫 `思忆集app/backend/handlers/**/*.py`，加上 `DYNAMICALLY_REGISTERED_PREFIXES` 前缀清单（同文件 :71-79）；环境变量扫 `(SIYI_[A-Z0-9_]+)`。输出 `docs/inventory.md`（头部标注"自动生成勿手改"+生成时间）。
- 动作 2：`docs/INDEX.md` 登记 `docs/inventory.md`（标注：自动生成产物，勿手改）。
- 动作 3：`.github/workflows/python-tests.yml` 的 "Doc index consistency check" 步骤后追加一步：
  ```yaml
  - name: Inventory drift check
    run: |
      python scripts/gen_inventory.py
      git diff --exit-code docs/inventory.md
  ```
- 验收：`.venv3.11\Scripts\python.exe scripts/gen_inventory.py` 输出方法数（预期 355–368 区间，以脚本口径为准并写入 inventory.md 首行）；`git diff --exit-code docs/inventory.md` 通过。
- 通过：本地生成后无 diff；CI 步骤存在且无 continue-on-error。

**TASK-V1-04 [执行] 文档治理修复（对应 B-2 + A-4 勘误）**
- 动作 1：~~INDEX 登记~~ **已完成（2026-08-23，v2 重写时同步登记于「工程 / 治理」节，勿重复添加）**。
- 动作 2：gstack 计划 §5.3.1 T2.3 行末追加勘误：`> 勘误 2026-08-23：check_doc_facts.py 与 CI 步骤当时未实际落地（全仓零命中）；等效实现由 debt-sprint v2 TASK-V1-03 接管（gen_inventory.py + CI drift check）。AGENTS.md 数字更正属实。`
- 验收：`python scripts/check_doc_index_consistency.py` 通过；gstack 计划含勘误字样。

**TASK-V1-05 [决策] remote.shell 下线路径（对应 B-4）**
- 选项：A. **默认建议**——两步走：先在 AGENTS.md 方法表与 `_remote.py` 模块 docstring 加 "⚠️ deprecated (S11)，计划 v0.2 移除" 标注（零风险，本任务直接做）；下个发布周移除注册并留 changelog 条目。B. 立即移除（需先 grep 确认前端零调用：`Get-ChildItem -Recurse desk-notes/packages -Include *.ts,*.tsx | Select-String 'remote\.shell'`）。
- 执行（A 的第一步）：改 AGENTS.md「远程控制」表与 `_remote.py:5-11` docstring，验收 `Select-String -Path AGENTS.md -Pattern 'remote.shell'` 行含 deprecated 字样。

**TASK-V1-06 [执行] 修复 CI 红：删除 19 份新旧双份的 docs/ 原件（对应 A-5）**
- 前提：archive/ 副本已在磁盘且字节级相同（已抽查验证），git 历史另存原件，删除无信息损失。
- 动作（一条命令，git rm 保留磁盘副本也可，这里直接移除以免再触发双份检查）：
  ```powershell
  git rm docs/PROJECT_HANDBOOK.md docs/architecture-audit-2026-06-22.md docs/architecture-audit-2026-06-22-part2.md docs/audit-2026-06-18.md docs/cloud-sync-roadmap.md docs/code-review-fix-plan-2026-06-18.md docs/deep-development-roadmap.md docs/feature-alignment-plan.md docs/frontend-backend-alignment-plan-2026-08-09.md docs/frontend-followup-roadmap-2026-08-09.md docs/frontend-production-alignment-plan.md docs/frontend-status-report-2026-08-09.md docs/full-integration-test-plan-2026-08-17.md docs/landing-plan-2026-08-09.md docs/p1-6-local-storage-performance-evaluation.md docs/remediation-plan-2026-08-07.md docs/roadmap-next.md docs/todo-unimplemented-features.md docs/工作台模块体检报告-2026-08-20.md
  ```
- 验收：`.venv3.11\Scripts\python.exe scripts/check_doc_index_consistency.py`
- 通过：`0 failures`（输出末尾无 FAIL 行，exit=0）。随后并入 V1 阶段收尾提交一并 push（节奏见 TASK-V4-02）。

### V2 需求对齐（2–3 周；承接评审报告 P0 建议 1/2 + C5）

**TASK-V2-01 [执行·收尾] automation_hub personal 双源收敛（对应 C-4①，C3 约束）--2026-08-24 状态更新**
- 状态更新：裁决**已完成**（`docs/decisions/2026-08-23-automation-hub-single-source.md`：桌面唯一权威源、hub 降级只读镜像）；镜像锁已实现（`automation_hub/api/routes/personal_factory.py:19-33`）+ 测试在场（`automation_hub/tests/test_personal_mirror_lock.py`）。以下原始选项仅存档。
- 背景：评审报告 §3 修正①——hub personal_* 是第二套可写 CRUD（`automation_hub/api/routes/personal_notes.py` 直连自有 db），与桌面 `domain/models`+`repositories` 双真相。hub 整体已边缘化（`docs/automation-hub-disposition.md` 为真源）。
- 选项：A. **默认建议**——hub personal_* 路由加开关并默认关闭（`HUB_ENABLE_PERSONAL_WRITE=0`），声明桌面后端为唯一可写真相源，hub 侧仅保留只读 mirror 能力；B. 宣布 hub 为权威源、桌面经 client 读写（工作量大且与边缘化处置矛盾）；C. 维持双写（违反 C3，持续漂移，不建议）。
- 确认后：按 A 执行——hub 路由模块入口加环境开关 + 默认拒绝响应 + `automation-hub-disposition.md` 追加裁决段 + 3 个路由级测试（开/关/默认关）。
- **残留动作（已完成，2026-08-24）**：① `personal_factory.py` 镜像锁**默认翻转为开**（`SIYI_HUB_PERSONAL_MIRROR_LOCK` 未设即锁定，仅设 `=0` 显式关闭供迁移回退），测试断言默认拒绝（`test_personal_mirror_lock.py` 4 passed）；② `docs/automation-hub-disposition.md` 已补"桌面权威、hub 镜像"裁决记录段（V2-01 标注）。
- 验收：默认配置下非桌面来源写 hub personal_* 被拒（已由测试验证）；disposition 含裁决记录。

**TASK-V2-02 [执行] Electron 前端行为事件上报，补 C5 采集盲区（对应 C-4②）--2026-08-24 已完成**
- 前置核查结论：`usage.record` 已注册（`_capabilities.py` → `services/usage_stats.record_action`），但**未接 behavior_logger**（当时无该调用方之外的采集落点）。
- 后端接线（已完成，commit `9ccdc171`）：`record_action` 同步投递 `BehaviorEvent(category=app, source=frontend, privacy=local)`（C5：仅本地、不上云）；3 回归测试 `test_usage_record_behavior_wiring.py`。
- 前端埋点（已完成，desk-notes commit `03a620b`，父仓 gitlink `0a29f74a`）：新增 `packages/desktop/src/utils/trackUsage.ts`（fire-and-forget 经 getWsClient→usage.record）；埋点 = 模块导航（MainWindow `module.open`+模块名）/ 新建便签（App.tsx new-note `capture.note`）/ 新建待办（QuickTodoInput `capture.task`）/ 收件（InboxModule `capture.inbox`）/ 聊天发送（useChatStream `chat.send`）。typecheck 通过 + vitest 70 文件 689 用例全过。
- 适应性说明：桌面主窗口模块清单为 chat/notes/todo/dashboard/knowledge-network/agent/automation/settings（无独立 inbox 导航），故 module.open 覆盖上述 8 模块；inbox 采集落在 InboxModule 的收件动作上。
- 备注（记录在案）：前端改动在 desk-notes 独立 repo 提交并跑 `pnpm test`+`tsc` 通过（承 T3.3 教训）；**小问宠物 WIP 未提交**（用户归属，按要求保持原样，与本次改动文件不重叠）。

**TASK-V2-03 [执行] 覆盖率棘轮（对应 C-4③，P1#18）--2026-08-24 修正**
- 事实修正：`coverage_gate.py` 的 `DEFAULT_THRESHOLD` 已是 **21.0**（AGENTS.md 手写 20.0 已过时，又一处文档漂移实例），CI 用 `--threshold 21` 且注释明言"实测 20.91%、故意红倒逼补测试"。故意的红必须显式豁免记录（-> TASK-M2-2），避免淹没"意外的红"信号。
- 修正后动作：完成 M4/V4 新增测试（proactive.tick / graph_extractor / 镜像锁默认等）后实测新覆盖率；稳定超过 22 后 `DEFAULT_THRESHOLD` 21.0 -> 22.0；此后每完成一个 V2/V3 任务顺手 +1（年内目标 30）。
- 验收：注入测试--临时改 99 必 FAIL，恢复 22 必 PASS 且实测 ≥22。
- 以下原始方案仅存档：
- 动作 1：跑实测：`.venv3.11\Scripts\python.exe scripts/coverage_gate.py --threshold 20` 记录当前值（基线 20.9%）。
- 动作 2：`scripts/coverage_gate.py` 的 `DEFAULT_THRESHOLD` 20.0 -> 22.0（第一档；此后每完成一个 V2/V3 任务顺手 +1，2026 年内目标 30，30 后按目录重估到 60/80 路线）。
- 动作 3：python-tests.yml 覆盖率步骤去 continue-on-error（若仍有）改硬阻塞。
- 验收：注入测试——临时把 threshold 改 99 跑一次必须 FAIL，恢复 22 必须 PASS。
- 通过：注入红、正常绿，实测 ≥22。

### V3 设计补课（穿插执行，单项独立可合）

**TASK-V3-01 [决策+执行] WS token 通道收敛（对应 C-1①）--2026-08-24 已完成**
- 决策：URL `?token=` 通道默认**关闭**（token 进 URL 会留痕于日志/历史/代理缓存，应走 header/cookie）；保留环境开关 `SIYI_WS_ALLOW_URL_TOKEN`，默认 `0`，一个观察期后删除通道。
- 执行（commit 见本批）：`_verify_auth` URL 分支包 `if os.environ.get("SIYI_WS_ALLOW_URL_TOKEN", "0") == "1":`；AGENTS.md 环境变量表已补该键；`docs/inventory.md` 重新生成（env 86→87）。
- 验收：新增 3 回归测试（`test_ws_server.py`）——默认拒绝正确 `?token=`（无 header 非 localhost）；`SIYI_WS_ALLOW_URL_TOKEN=1` 放行；**header 主通道默认不受影响**（防 V3-01 误伤）。18 passed。

**TASK-V3-02 [执行] Worker 设备吊销端点（对应 C-1②）**
- 动作：`cloudflare/worker/src/sync.ts` devices 表加 `revoked_at INTEGER`（迁移幂等：`ALTER TABLE ... ` 包 try/catch）；新增路由 `POST /revoke-device`（鉴权同 register_secret）：置 `revoked_at=unixepoch()`；既有 token 校验 SQL 追加 `AND (revoked_at IS NULL)`。
- 验收：本地 wrangler 测试或最小单测：注册 -> 吊销 -> 旧 token 请求 401；未吊销设备不受影响。

**TASK-V3-03 [执行] 补齐密钥解析第三段（对应 C-2，AGENTS 已承认未实现）**
- 动作：定位 `GenericToolBridge._default_secret_resolver` 的 `return None` 兜底（`Select-String -Path 思忆集app/assistant/*.py,思忆集app/services/*.py -Pattern '_default_secret_resolver'`），在 SecureKeyStorage 分支后追加第三段：读 settings 加密 token 并 `SecureKeyStorage.decrypt`（失败返回 None，不抛）。
- 验收：单测覆盖三段优先级（env > keyring > settings 解密）。

**TASK-V3-04 [执行] 同步口令轮换 runbook（对应 C-2）**
- 动作：新建 `docs/security/sync-key-rotation-runbook.md`：口令更换流程（新口令派生新钥 -> 逐设备重新加密推送 -> Worker 旧数据过期策略 -> 回退）；INDEX 登记。
- 验收：check_doc_index_consistency 通过；文档含回退一节。

**TASK-V3-05 [执行+决策] schema 演进最小框架（对应 C-3）**
- 执行 a：`思忆集app/data/sqlite_store.py` 建库时加 `schema_migrations` 表（version INTEGER PK, applied_at）+ `current_schema_version()` 读值 + 预留 `apply_migrations()` 空骨架（首个迁移登记当前版本号）。
- 决策 b：sync envelope 是否加 `min_client_version` 字段（默认建议：加，服务端拒绝低于下限的客户端并返回明确错误码）。
- 验收 a：新库/旧库启动后 migrations 表存在且版本正确；单测 2 例。

**TASK-V3-06 [执行] 发布工程起步（对应 C-5）**
- 动作 1：根新建 `CHANGELOG.md`（Keep a Changelog 格式；回填 2026-08 里程碑：审计 writer 独立库/CI 硬化/写放大 -99.7%/上帝模块拆分/子进程隔离灰度等，从 gstack 计划 §2.2 提炼）。
- 动作 2：`git tag -a v0.1.0 -m "质量治理期基线"` 并推送。
- 动作 3：AGENTS.md 增 3 行版本策略（semver；tag 即发布；CHANGELOG 必更）。
- 验收：`git tag -l` 含 v0.1.0；CHANGELOG 有 Unreleased + v0.1.0 两节。

**TASK-V3-07 [执行] 依赖治理接线（对应 C-6）--2026-08-24 收窄**
- 事实修正：pip-audit **已接线** CI（python-tests.yml:79-80 P2-3 步骤），但被引用的 `scripts/run_pip_audit.py` 不存在（-> A-6 / TASK-M2-1 先补脚本 + `scripts/pip-audit-baseline.txt` 基线）。本任务收窄为 dependabot + 周期审计 job + 前端 pnpm audit；原动作 2 的"首跑基线入库"部分移交 M2-1。以下原始方案仅存档：
- 动作 1：`.github/dependabot.yml`（pip + npm 两生态，weekly，目录分别为 `/` 与 `/desk-notes`）。
- 动作 2：python-tests.yml 加周期 job（`schedule: weekly` + workflow_dispatch）：`.venv3.11\Scripts\pip-audit.exe -r requirements-desktop.txt --format json -o scripts/pip-audit-baseline.json`（首跑入库为基线；后续跑对比，新增漏洞才红）。
- 动作 3：desk-notes CI 加 `pnpm audit --prod --audit-level high` 基线同模式。
- 验收：dependabot 配置合法（`gh api /repos/.../dependabot` 或首周 PR 出现）；pip-audit 基线文件入库。

**TASK-V3-08 [执行] 错误 digest 周报（对应 C-7）**
- 动作：新建 cron action `system.error_digest`（注册进 `services/cron_actions.py` 既有注册表，模式同 proactive.tick）：每周一 08:00 解析 `backend.dev.log`/`backend.dev.err.log` 近 7 天 ERROR/Traceback，按异常签名聚合计数，产出 Top 20 -> `assistant_inbox_service.deliver_suggestion(text="每周错误摘要: N 类异常 Top3...", action="trace.list")`。
- 验收：`execute_cron_action(ctx, "system.error_digest", {})` 单测返回 completed 且建议文本非空（用临时日志文件注入）。

### V4 防线固化（收尾）

**TASK-V4-01 [执行] 注入测试汇总**
- 对本清单新增的所有 CI 门禁（inventory drift / coverage / pip-audit / dependabot）各做一次"故意红"验证（临时改坏 -> CI 红 -> 还原 -> 绿），结果记入本文件 §5 表。

**TASK-V4-02 [执行] 全量回归 + 收尾提交**
- 命令：`.venv3.11\Scripts\python.exe -m pytest -q`（仓库根，基线 3450 passed / 15 skipped / 4 xfailed）；`git push`。
- 通过：失败数不高于基线。

**TASK-V4-03 [人工确认项]**
- [ ] desk-notes 子仓 17 个宠物资源文件提交（在其独立 repo 跑 pnpm test 后）
- [ ] T4.1 子进程隔离灰度观察期满后的转正/回退决策（照 gstack 计划，不归本清单）
- [ ] backend.dev.log 观察期结束后的清理

---

## 4. 执行模型修正（v1 教训固化）

1. **执行/决策二分**：v1 把判断题伪装成执行题（"标记定义/使用"、"情况 A/B"）。v2 所有需裁量之处显式标 [决策]，给选项表 + 默认建议，人工确认后才进执行。
2. **pwsh 原生**：全部命令 PowerShell 语法；无 `wc`/`tail`/`mkdir -p`/`2>/dev/null`。
3. **正则先验证再入清单**：v1 的 `def rpc_` 假设产出 "0 methods"。v2 的 inventory 任务明确锚定已验证的 `RE_BACKEND_REGISTER`（test_jsonrpc_contract.py:21）。
4. **每任务带反证验收**：门禁类任务必须做"故意红"注入（承 gstack §5.8 闭环反证）。
5. **属性名以测试锁定为准**：凡改 context 属性，先对照 `test_appcontext_startup_check.py` 白名单语义，禁止凭记忆写属性名。

## 5. 附录

### A. 任务-问题映射

| 任务 | 消灭的问题 | 复发模式（承 v1 附录） |
|---|---|---|
| V0-01/02/03/04/05 | A-1 A-2 A-3 B-5 | 修了但没验证 / 没备份 |
| V1-01/02/03/04/05/06 | A-3 A-4 A-5 B-1 B-2 B-4 | 知道了但没执行 / 文档说做了但没做 / CI 假红 |
| V2-01/02/03 | C-4①②③ | 需求文档有约束、代码无人承接 |
| V3-01~08 | C-1~C-3 C-5~C-7 | 从未设计的维度 |
| V4 | 全部门禁 | 修了但没防线 |
| M1-M7（§6） | A-6 A-7 + L2-L5 过程病根 | 完成自报化 / 反馈环从未闭合 / 门禁装饰化 / 静态计划腐化 |

### B. 与 gstack 执行计划（T1–T4）的关系

- gstack T1.1/T1.2/T2.x/T3.1–T3.6/T4.2/T4.3 绝大部分已 ✅，v2 不重复；T4.1 灰度观察继续按 gstack 推进。
- v2 承接 gstack **未覆盖**的空白：活故障残骸（A-1/A-2）、远端备份（A-3）、T2.3 缺失交付物（A-4）、需求对齐三缺口（C-4）、七个从未设计维度（C-1~C-3/C-5~C-7）。
- 冲突处理：若 gstack 后续 v1.2 与本文件任务重叠，以时间新者为准并回写两边索引。

### C. V4-01 门禁注入演练记录（2026-08-24，先红后绿逐项）

> 手法：临时改坏/临时文件 -> 该门禁必须红 -> 还原 -> 该门禁必须绿。以下 rc 为门禁脚本退出码。

| 门禁 | 注入手法 | 观测到红 | 还原后绿 |
|---|---|---|---|
| 无裸 pyc | 创建无源码 `思忆集app/v401pyc.pyc` | 红 rc=1（发现裸 .pyc） | 删除后 rc=0 ✅ |
| 层边界 | 建 `ai/client/_v401inj.py` 含 `import assistant` | 红 rc=1（import 违规） | 删除后 rc=0 ✅ |
| doc 一致性 | 建 docs 顶层孤儿 `docs/v401-inject.md` | 红 rc=1（孤儿 FAIL） | 删除后 rc=0 ✅ |
| inventory 防漂移 | 手改 `docs/inventory.md` 追加假行 | 红 rc=1（DRIFT） | 还原后 rc=0 ✅ |
| 核心模块完整性 | `check_core_integrity.py` CRITICAL_FILES 塞缺失路径 | 红 rc=1（[MISSING]） | 还原后 rc=0 ✅ |
| 无被忽略源码 | 建被 gitignore 吞的 `backend/_old_v401inj.py`（done_check 口径 PYTHONUTF8） | 红 rc=1（FAIL 1 个） | 删除后 rc=0 ✅ |
| coverage | 阈值改 99 | 红（22.51%<99） | 阈值 22 绿（22.51%≥22）✅（V2-03 同次完成） |
| pip-audit | 空基线+无网络双 WARN 均 exit 0，**离线本地无法注入红** | —（环境受限） | M2-3 网络/基线就绪后按 M2-1 塞假 ID 可红 |
| dependabot | CI 侧配置，本地不可注入；`.github/dependabot.yml` 存在（pip+npm weekly） | —（CI 只读） | 配置在场即通过（`gh api` 校验留给 CI） |
| ruff 棘轮(改) | pre-commit ruff 钩子同源，提交即拦同类违规；**离线 pre-commit 无法初始化（需 github.com）阻塞提交注入** | 现态绿 | 绿；红路径=CI 在真实 diff 上跑 |
| mypy 棘轮(改) | 需已提交新 mypy 错；同受 pre-commit 离线阻塞 | 现态绿（413 全在基线） | 绿；红路径 M5 已演示（cron_actions 死属性） |
| broad-except 棘轮(改) | 同上需已提交新裸 except | 现态绿 | 绿 |
| git 工作树干净 | 脏文件即红（当前 V4-02 未提交已红，预期） | 红（4 项未提交，V2-03/V3-06 改动） | 绿待 V4-02 commit（受阻，见下） |

**环境发现（V4-01 演练期）：** 本仓库 `.git/hooks/pre-commit`（ruff+format）首次运行需从 github.com 克隆初始化 ruff-pre-commit 环境，而当前网络不可达（push 403 / github.com:443 全阻）→ `git commit` 卡死在钩子初始化、**无法产生任何提交**。此阻断同时影响：a) 三个"棘轮(改)"门禁需要"已提交新违规"注入，离线无法构造；b) **V4-02 的 commit 步**将因此受阻。生产化钩子在联网 CI/首次缓存后正常，但本地离线提交被锁死 —— 需人工在可联网环境 `pre-commit install`/缓存后，或由用户裁断处置，不自行 `--no-verify`。

## 6. 机制化闭环（M1-M7）--2026-08-24 增补

> 优先级：**先于所有未完成 V 任务**。增补依据：本计划执行 24 小时内自身漂移（C-6/V2-03 过时、A-6/A-7 新发现），证实 §0 对 v1 的病理判定同样适用于 v2 载体本身。治本不靠第五份清单，靠改变工作方式的机制：**纪律是贬值资产，机制是复利资产**。M 系列任务的状态自本文起以 M1 货币计（commit SHA + 该 commit CI 绿 + 已 push）。

### 6.0 已完成（M1 货币，截至 2026-08-24）

| 项 | 内容 | 证据 |
|---|---|---|
| V0-02/03/04/05 | A-1/A-2/B-5 修复 + 17 测试绿 | commit `d6bfce99` |
| 计划 v2.0 + INDEX 登记 | 本文前身 | commit `8e9e3ead` |
| V1-06 / A-5 | 19 份双份归档清除，doc 检查 0 失败 | commit `efce8312` |
| V1-02 / B-1 | 根目录清理（跟踪文件出库 7 个） | commit `d511d980` |
| V1-03 / M3 前半 | gen_inventory + docs/inventory.md + CI drift 步骤 | 本批提交（`git log --grep V1-03`） |
| V2-01 裁决核验 | hub 镜像锁已实现、默认关（残留见 V2-01） | `docs/decisions/2026-08-23-automation-hub-single-source.md` + `personal_factory.py:19-33` |
| M2-1/M1-1/V1-04/V1-05 | 两幽灵脚本补齐 + done_check 一键全门禁 + gstack T2.3 勘误 + remote.shell 弃用标注 | commit `3abbb8aa` |
| A-8 修复 | 陈旧 .pyc 导入链切迁移版 + 删除遗留 .pyc + 回归测试（裸 pyc 守卫首抓） | `git log --grep A-8` |
| V2-02 | usage.record→behavior_logger 接线 + 前端 5 处埋点（module.open/capture.*/chat.send） | 后端 `9ccdc171`；前端 desk-notes `03a620b`；gitlink `0a29f74a` |
| V3-01 | WS URL token 通道默认关闭（SIYI_WS_ALLOW_URL_TOKEN 门控）+ 3 回归测试 + inventory 87 | `edbb2795` |
| M2/M5 棘轮硬化 | 本地 ruff/mypy 盲点修复（查 main..HEAD 非仅未提交）+ mypy 行号漂移容错 + 清 A-8/V2-02 新类型错误 | `21b465d6` |
| M5 首批病灶 | cron 三模块 + test_cron_actions 清零移出基线（1218→1205 行）+ CI 零容忍硬门禁 + 全量 780→764 | `656cfdc9` |
| 仓库完整性（根因级） | 恢复 78 个被 gitignore 吞掉的生产 .py（data/ 存储层、app_runtime/_context_*、ai/client、repositories、memory、build 工具包等）+ 锚定 data//_*.py//build/ 三规则 + check_ignored_source 守卫；fresh-checkout 8 核心模块导入验证通过 | `16652dad` |
| V3-06（发布工程起步） | CHANGELOG.md（Keep a Changelog，[Unreleased]+[v0.1.0]-2026-08-24）+ git tag `v0.1.0`→`c9c5346d` + AGENTS.md 增 3 行版本策略（semver/tag 即发布/CHANGELOG 必更） | CHANGELOG `4687f386`；AGENTS.md `1d6ca333`；**tag/commit 仅本地，push 待 M2-3 网络恢复（github.com:443 不可达）后闭合** |

### TASK-M2-1 [执行] 补齐 CI 幽灵脚本（A-6）

- 动作 1：实现 `scripts/check_core_integrity.py`：核对关键入口模块存在且非空（首批清单：`backend/headless_main.py`、`backend/ws_server.py`、`backend/method_registry.py`、`backend/event_bridge.py`、`app_runtime/app_context.py`、`app_runtime/_context_builder.py`、`services/cron_actions.py`、`services/cron_service.py`、`sync/cloud_sync_client.py`、`core/audit_rollback.py`；清单内置于脚本，缺一退出码 1 并列缺失项）。风格复用 `scripts/check_no_sourceless_pyc.py`。
- 动作 2：实现 `scripts/run_pip_audit.py`：包装 pip-audit 对 `requirements-desktop.txt` + `pyproject.toml` 声明依赖审计；`scripts/pip-audit-baseline.txt` 存放已接受漏洞 ID（每行一条），**新增**漏洞才退出码 1；`--gen-baseline` 首跑生成基线。pip-audit 不可用时输出 WARN 并退出码 0（CI 需先 `pip install bandit pip-audit`，该步骤已在 workflow :44-45 存在）。
- 验收（先红后绿）：两脚本本地退出码 0；临时向清单塞一个不存在的路径 / 基线塞一个假 ID 均能红。

### TASK-M2-2 [决策] coverage 故意红的显式豁免

- 选项：A. **默认建议**--保持阈值 21（倒逼补测试），在 python-tests.yml 该步骤注释 + 本节登记"此项红为预期，跟踪 TASK-V2-03"；B. 降回 20.9 过渡（信号干净但放弃倒逼）。**选 A**（2026-08-24）。
- 验收：CI 文件含豁免注释；本节含豁免登记。

### TASK-M2-3 [执行] push 闭合反馈环（= V0-01/V1-01 收口；依赖用户配置 GitHub 凭据）

- 动作：`git push -u origin fix/p0-2026-06-18`；`git push -u cto fix/p0-2026-06-18`；`git branch main fix/p0-2026-06-18` 后 `git push -u origin main`。
- 验收：GitHub Actions 首跑完成：除豁免项（coverage）外全绿；run URL 回填本节。这是本项目**第一次**闭合 push->CI->红绿回路。

### TASK-M1-1 [执行] done_check.py 一键全门禁

- 动作：新建 `scripts/done_check.py` 顺序聚合：`check_doc_index_consistency` / `gen_inventory --check` / `check_no_sourceless_pyc` / `check-layer-boundaries` / `check_core_integrity` / `run_pip_audit` / `gen_quality_baseline --check-changed` / `mypy_ratchet --check-changed` / `scan_broad_except --check-changed` / `git status --porcelain` 空检查，逐项输出 PASS/FAIL + 末尾汇总表 + 总退出码（coverage 可选 `--with-coverage`）。
- 验收：一条命令得全部门禁汇总；V4-01 注入演练时任意单项红能拉低总码。

### TASK-M1-2 [执行] pre-commit install（A-7）

- 动作：`.venv3.11\Scripts\pre-commit.exe install`（或 `python -m pre_commit install`）。
- 验收：`Test-Path .git/hooks/pre-commit` 为 True；下一次 commit 出现钩子运行输出。

### TASK-M5 [执行·持续] 按病灶缩 mypy 基线 --2026-08-24 首批病灶已清零

- 事实：`scripts/mypy-baseline.txt` 原 1218 行，格式 `路径:行:列: error ...`，天然支持按路径前缀过滤收缩。
- **首批（proactive.tick 事故链路）已完成**：`services\cron_actions.py`（2 错）、`app_runtime\_context_cron.py`（3 错）、`backend\handlers\_cron.py`（0 错）清零并移出基线（连同 `tests\test_cron_actions.py` 7 条同病灶），基线 1218→1205 行；全量 mypy 错误 780→764 全在基线内。
  - 修复要点：`KEY_PROACTIVE_ENABLED` 改从定义源 `core.settings_keys` 导入；`entry_id: str` 显式类型；`SettingsRepository` 从 `repositories._protocols` 导入；mixin TYPE_CHECKING 补 `local_data_store` 契约；`execute_cron_action(cast("AppContext", self), ...)`；测试 fake ctx 返回 `Any`。
- **CI 硬门禁已加**（`.github/workflows/python-tests.yml`）：`Cron lesion mypy zero-tolerance gate (M5)`——三模块全量零错误硬门禁，不随棘轮基线放宽（本地已验证 `Success: no issues found in 3 source files`，与 CI 命令一致）。
- **恢复文件暴露债（2026-08-24，仓库完整性 16652dad 连带）**：`sync/_cloud_sync_entity_handlers.py` 30 错（DI mixin TYPE_CHECKING 契约 + `_detect_conflict` 签名放宽 + 调用处 `if local and`）、`_cloud_sync_crypto.py` F821 真 bug（aiohttp 懒导入）、`test_cron_actions` 补 Any——全量 mypy 764→731，sync 75 测试过（`4ca47f08`）。
- **基线真值同步（2026-08-24）**：基线 1205→890 行。分析发现 205 条陈旧条目（错误已修但基线未同步）+ 140 条当前错误未覆盖（存量隐藏债，未来编辑会被误当"新增"阻塞）。新增 `mypy_ratchet.py --gen-baseline`（M3 事实生成不复制：基线=当前全部已知错误），重建后基线=当前真值（723 唯一指纹）。修复 `assistant/execution/action_broker.py` 8 错（`get_capability` 返回 None 时无守卫 → 真 AttributeError 隐患，补 `capability_not_found` 守卫与 `_prepare_and_validate` 一致；42 测试过）。新增 `scripts/mypy_baseline_diff.py` 分析工具。
- **`ai/__init__.py` 死 re-export 清理（2026-08-24）**：307 行 auto-generated 聚合从 ~50 模块 re-export 全部符号，52 原始错（12 唯一指纹）。全仓审计证实**零消费**（所有代码走子模块导入；`character_runtime` 的 SINK_* 等 re-export 运行时本就静默失败）；且 11 个包的同类 auto-generated re-export 模式全仓皆死。重写为最小化包入口（保留 P3-12 不变量），52 错清零、`import ai` 不再拖入 ~50 模块。16 回归测试过，全量 723→711。
- **`repositories/_note_sync_mixin.py` DI mixin 契约（2026-08-24）**：19 错（`data_store`/`_load_notes`/`_save_notes` 宿主契约缺失）→ 类内 TYPE_CHECKING 声明（与 _context_cron/_cloud_sync_entity_handlers 同款）+ 1 处 `existing_note.get("id")` 改 `["id"]`（显式化 id 必在场不变量，防静默 None 当 id）。52 测试过，全量 711→692。
- **低频活跃模块收尾（2026-08-24 会话，692→520 唯一指纹）**：`integrations/chat_importer.py`(19)、`backend/headless_main.py`(13)、`backend/process_watchdog.py`(5)、`ai/client/config.py`(16)、`backend/handlers/__init__.py`(15)、`services/cron_service.py`(8)、`knowledge/md_exporter.py`(11)、`desktop/doctor_service.py`(11)、`services/react_executor.py`(18)、`app_runtime/app_context.py`(18 含跨文件)、`ai/client/fallback.py`(10)、`assistant/_autonomy_codex.py`(11)、`assistant/_router_content.py`(7)、`sync/file_sync_service.py`(7)、`core/settings_migration.py`(5)、`orchestration/drivers/trae_driver.py`(8)、`repositories/_vocab_sync_mixin.py`(6)、`services/ocr_service.py`(6) 已清零移出基线并逐轮 `--gen-baseline` 同步。修复覆盖 M5 全部分类（DI getattr/setattr、mixin TYPE_CHECKING、跨源导入修正、统计袋 dict[str,Any]、真类型守卫/改名、frozen dataclass 累加、env Mapping 等）；其中 react_executor 顺带修 `assistant.butler_agent`→`assistant.agents.butler_agent` 迁移漏网真 bug、file_sync_service 修 frozen SyncResult 原地累加真 bug。
- 剩余低频活跃模块（待续）：`assistant/_autonomy_governance.py`(8)、`assistant/organize_action_helper.py`(8, PyQt6 遗留)、`core/file_watcher.py`(8, watchdog 类型)、`knowledge/retrieval/vector_store.py`(8, ChromaDB)、`integrations/automation_hub_client.py`(7)、`mcp_server/tools.py`(7) 等。
- 验收：向 cron_actions 注入一行 `context.inbox_service` 死属性 -> 门禁红（与 V4-01 同一次演练完成）。

### TASK-M6 [执行] AI 会话卫生守则写入 AGENTS.md 首段

- 文本要点：任何 AI 会话**开工前 `git status` 必须干净**（不干净先确认归属再动）；**收工必须 commit + push**（凭据可用时）；**禁止 staged 残留**；长任务分批小 commit；本守则由 done_check 的 porcelain 检查兜底。
- 验收：AGENTS.md 顶部含该守则段（含"为什么"一句话：250 文件 staged 事件 + 0 push 历史）。

### TASK-M7 [执行] 分析冻结条款（自我约束，常驻）

- 条款：本计划全部任务按 M1 货币验证完成之前，**不启动第四轮全面评审、不新写全景计划文档**；仅允许病灶触发的窄切片分析（≤半天，结论并入现有任务或修订本文，不另立新文档）。
- 验收：本节存在即生效；违反须先修订本节并说明理由。

### 6.9 M 系列执行顺序

M2-1 -> M2-2 -> M2-3（凭据就绪即推）-> M1-2 -> M1-1 -> M6 -> M5（持续）-> M7（常驻）。V1-01/04/05 与 M2 并行收尾；V2/V3/V4 按原节次序随后。
