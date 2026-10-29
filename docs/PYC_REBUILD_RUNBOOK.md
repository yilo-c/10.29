# 丢失源码 pyc 重建 — 自动执行手册（Runbook）

> ## ✅ 工程已完工（2026-08-08）
>
> **82 个丢失源码的模块已全部重建完成，仓库零裸 pyc。**本手册保留作为方法论存档（若未来再发生类似事故可直接复用）。
>
> **验收基线（2026-08-08）**：
> - 根级 pytest：3450 过 / 15 跳过 / 4 xfailed，零真实失败（高负载下 4 个时序敏感用例抖动，单跑全过）
> - automation_hub：179/179 全绿
> - 前端 typecheck ✅；vitest 43 个测试文件全过
> - WS 冒烟 16/16；后端冷启动正常
> - 覆盖率门禁 20.02% ≥ 20%
> - 守卫脚本 `scripts/check_no_sourceless_pyc.py` 已接入 `.github/workflows/python-tests.yml` 防再发
> - 原始 pyc 备份：`D:\siyi_pyc_backup_20260808.zip`（建议推送到远程仓库后再删除）
>
> 下文为重建方法论存档（历史任务书原文）。

> **本文件是给 AI 编码工具（Claude Code / CodeBuddy / 其他 Agent）直接阅读并执行的任务书。**
> 请严格按顺序执行，每个模块独立走完五步流程并 git 提交，再做下一个。

## 0. 背景（必读）

思忆集app 有 63 个 Python 模块源码丢失（2026-07-18 疑似清理脚本误删），当前仅靠**裸 legacy .pyc**（无源码的字节码文件）运行。
你的任务是：**把每个 .pyc 反汇编还原为语义完全一致的 .py 源码**，验证通过后删除对应 .pyc。

关键事实：
- Python 版本锁定 `.venv3.11\Scripts\python.exe`（3.11.15），**不得更换解释器**（pyc 是 3.11 字节码）
- 所有原始 pyc 已备份到 `D:\siyi_pyc_backup_20260808.zip`（含完整目录结构），任何时刻都可回滚
- git 已有基线提交，**每完成一个模块必须提交一次**
- 仓库根目录：`D:\h\3.25\思忆集`，主源码：`D:\h\3.25\思忆集\思忆集app`

### 红线（违反任何一条立即停止并报告）
1. **禁止修改任何行为**：常量字符串、错误文案、dict 键、日志格式必须与字节码完全一致。即使发现疑似 bug 也不要修，除非人类明确批准。
2. **禁止删除 pyc 直到对拍 + 测试全部通过**。
3. **禁止执行任何 `*.pyc` 批量清理命令**。
4. **禁止升级/切换 Python 版本**。
5. 涉及加密、密钥、SQL 语句的模块（下文标 ★），对拍必须覆盖加解密往返。

## 1. 环境命令速查

配套脚本位于 `.pytest-tmp\`（工作副本，产出也写回该目录），`scripts\pyc_rebuild\` 下有同一套脚本的 git 受控备份（若 `.pytest-tmp` 缺失，先从备份复制回去再用）。

```powershell
cd D:\h\3.25\思忆集
$py = ".\.venv3.11\Scripts\python.exe"

# 查看当前还剩哪些无源码模块
& $py -c "from pathlib import Path; [print(p.relative_to('思忆集app'), p.stat().st_size) for p in sorted(Path('思忆集app').rglob('*.pyc')) if '__pycache__' not in p.parts and not p.with_suffix('.py').exists()]"
```

## 2. 单模块五步流程（核心，逐字照做）

设目标模块为 `MOD`（如 `backend.handlers._rss`），对应 pyc 为 `思忆集app\backend\handlers\_rss.pyc`。

### 第 1 步：提取 API 与文档（UTF-8 落盘，避免控制台乱码）

```powershell
# handlers 专用提取器（含嵌套闭包 docstring + 全量反汇编）
& $py .pytest-tmp\handler_extract_utf8.py MOD h_<短名>

# 通用提取器（非 handlers 模块用）
& $py .pytest-tmp\pyc_extract.py MOD
```

产出 `.pytest-tmp\h_<短名>_docs.json`（模块/函数/闭包 docstring，UTF-8 原文）和 `.pytest-tmp\h_<短名>_dis.txt`（字节码反汇编）。

**还要取模块级导入**（函数反汇编里看不到）：

```powershell
& $py -c "import sys, dis, io; sys.path.insert(0,'思忆集app'); import MOD as m; code=m.__spec__.loader.get_code(m.__name__); buf=io.StringIO(); dis.dis(code, file=buf); [print(l.strip()) for l in buf.getvalue().splitlines() if 'IMPORT' in l]"
```

**还要取 logger 名与函数是否 async**：

```powershell
& $py -c "import sys, inspect; sys.path.insert(0,'思忆集app'); import MOD as m; print(getattr(getattr(m,'logger',None),'name',None)); [print(n, inspect.iscoroutinefunction(o)) for n,o in vars(m).items() if inspect.isfunction(o)]"
```

### 第 2 步：读反汇编并转写源码

- 读 docs.json 拿到**精确 docstring**（逐字使用，不要润色）
- 读 dis.txt 的嵌套函数段落还原逻辑。翻译要点：
  - `BUILD_CONST_KEY_MAP` → dict 字面量，键顺序就是常量元组顺序
  - `JUMP_IF_TRUE_OR_POP` + `LOAD_CONST ''` + `LOAD_METHOD strip` → `(x or "").strip()`
  - `LOAD_GLOBAL (NULL + fn)` → 全局函数调用（意味着该名字在模块级导入或内建）
  - `LOAD_DEREF` → 闭包捕获的外层变量
  - `KW_NAMES (...)` → 关键字参数调用
  - `BUILD_STRING` + `FORMAT_VALUE` → f-string
  - `GET_AWAITABLE` → `await`；`RETURN_GENERATOR` → 函数是 `async def`
  - 异常表（ExceptionTable）→ try/except 范围
- 复杂函数转写不确定时，对**旧 pyc 的这个函数单独 `dis.dis`** 再看一遍：
  ```powershell
  & $py -c "import sys, dis; sys.path.insert(0,'思忆集app'); import MOD as m; dis.dis(m.FUNC)"
  ```

把还原的源码写到对应的 `.py` 路径（pyc 路径把 `.pyc` 换成 `.py`）。

### 第 3 步：行为对拍（新旧同输入比输出）

照抄模板 `.pytest-tmp\compare_handlers_3.py`（handlers 版）或 `.pytest-tmp\compare_ut.py`（服务版），改模块名后放到 `.pytest-tmp\compare_<短名>.py`。模板核心套路：

```python
import sys, tempfile, zipfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
with zipfile.ZipFile(r"D:\siyi_pyc_backup_20260808.zip") as zf:
    (tmp / "legacy_xxx.pyc").write_bytes(zf.read("思忆集app/<相对路径>/xxx.pyc"))
sys.path.insert(0, str(tmp))
sys.path.insert(0, str(REPO / "思忆集app"))
import legacy_xxx as old      # 旧 pyc 版
import <模块路径> as new        # 新源码版
# 之后：常量相等 → dir/签名相等 → 同输入调用比输出
```

对拍必须覆盖：① 全部常量/文档字符串；② 每个公开函数的正常路径、参数越界路径、依赖缺失路径；③ 异常路径（抛同样异常比错误信息）。
**handlers 模块额外必须验证**：注册出的 RPC 方法名集合完全一致（用 FakeRegistry 收集）。

```powershell
& $py .pytest-tmp\compare_<短名>.py
# 必须输出 ALL MATCH 才能继续
```

### 第 4 步：删除 pyc + 回归测试

```powershell
Remove-Item "思忆集app\<相对路径>\xxx.pyc"
# 确认加载的是 .py：
& $py -c "import sys; sys.path.insert(0,'思忆集app'); import MOD as m; assert m.__file__.endswith('.py'), m.__file__; print('OK')"
# 跑相关测试（按模块主题选关键词）：
& $py -m pytest 思忆集app/tests -q -p no:cacheprovider --tb=line -k "<关键词>"
```

若测试中有失败，先判断是你的重建错误还是测试本身问题；不确定就停下报告，不要改测试。

### 第 5 步：提交

```powershell
git add -A
git commit -m "rebuild: 重建 <模块路径>（对拍通过，N 相关用例绿）"
```

## 3. backend/handlers 专用模板（已完成 3 个范例）

所有 handlers 模块结构同构，**照抄这三个已重建范例**：

| 范例文件 | 特点 |
|---|---|
| `思忆集app\backend\handlers\_process.py` | 同步 register + 共享辅助闭包 `_get_xxx()` |
| `思忆集app\backend\handlers\_image.py` | 同步 register + try/except + 模块级 import asyncio |
| `思忆集app\backend\handlers\_info_sources.py` | **async register** + 模块级导入业务类 + 闭包捕获 register 局部变量 |

注册调用点参考 `backend\handlers\__init__.py`：同步的直接 `_x.register_xxx(registry, context)`，异步的前缀 `await`（`_info_sources` / `_rss` / `_info_items` 是 await 形式）。
对拍脚本范例：`.pytest-tmp\compare_handlers_3.py`（FakeRegistry + FakeContext + 行为驱动）。

## 4. 剩余模块清单与执行顺序（按此顺序做）

**启动路径关键（backend/handlers，12 个，先做）** — 括号内为 pyc 字节数（越小越简单）：
1. `_rss`(7353) → 2. `_transcription`(8108) → 3. `_audit`(10390) → 4. `_cron`(11533) →
5. `_provider_health`(11810) → 6. `_task_queue`(12550) → 7. `_feedback`(13465) →
8. `_info_items`(13835) → 9. `_job_hunter`(14817) → 10. `_tools`(25314) →
11. `_timeline`(28464) → 12. `_external_content`(36983)

**services 业务层（6 个）**：`info_categorizer`(7710) → `info_translator`(10531) →
`knowledge_organizer`(10603) → `info_source_registry`(16141) → `process_lifecycle_manager`(15935) →
`api_key_rotation_service`(22249)

**其他单模块**：`services/transcription_service`(10342)、`services/rss_subscriber`(26249)、
`integrations/kimi_sync`(18484)、`integrations/readwise_client`(18491)、
`backend/process_watchdog`(16418)、`backend/job_hunter_scheduler`(576)、
`ai/provider_health_monitor`(23957)、`ai/client/_deprecated/_judge_aware_client`(29417，deprecated 可最后做)、
`assistant/` 下 6 个（`_autonomy_codex`、`_autonomy_governance`、`task_planner`、`tool_bridge`、
`generic_tool_bridge`、`capability/_dynamic_tools`、`capability/_tool_management`；
`capability_router_recovered`(89116) 体积巨大，单独一轮处理）

**低危/可降级**（先跑引用体检，无人引用的与人类确认后可仅存档不重建）：
`alembic/` 3 个、`orchestration/drivers/` 3 个、`workflow/` 2 个、`services/job_hunter/` 全家 17 个。
引用体检脚本：`.pytest-tmp\missing_src_inventory.py`。

## 5. 全部完成的验收标准

1. `& $py -m pytest -q -p no:cacheprovider`（根级全量）零失败（基线 3452+ 过）
2. 后端冷启动 + WS 冒烟：
   ```powershell
   Start-Process -FilePath "$PWD\.venv3.11\Scripts\python.exe" -ArgumentList "-m","backend.headless_main" -WorkingDirectory "$PWD\思忆集app" -WindowStyle Hidden
   Start-Sleep 18
   & $py .pytest-tmp\ws_smoke_readonly.py   # 期望 16/16 通过
   ```
3. 第 1 步的盘点命令输出为空（没有无源码 pyc）

## 6. 已完成模块（全部 82 个，勿重复做）

**批次 A（core，4）**：env_loader、metrics、secure_key_storage、secret_resolver
**批次 B（ai，5）**：aggregation_telemetry、provider_quota、usage_tracker、aggregation_strategies、aggregator
**批次 C（services/handlers 等）**：storage_transaction、info_constants、info_categorizer、info_translator、info_source_registry、knowledge_organizer、process_lifecycle_manager、api_key_rotation_service、kimi_sync、readwise_client、rss_subscriber、transcription_service、process_watchdog、job_hunter_scheduler、job_hunter 全家（17 个）
**handlers（15）**：_image、_process、_info_sources、_rss、_transcription、_audit、_cron、_provider_health、_task_queue、_feedback、_info_items、_job_hunter、_tools、_timeline、_external_content
**assistant/adapters（8）**：chat_detector、tool_spec、_autonomy_codex、_autonomy_governance、task_planner、tool_bridge、generic_tool_bridge、capability/（_dynamic_tools、_tool_management、capability_router_recovered）、adapters/base
**其他**：agent_context_vars（下沉 core）、sandbox_manager、ai/provider_health_monitor、ai/client/_deprecated/_judge_aware_client、alembic/、orchestration/drivers/、workflow/ 等
