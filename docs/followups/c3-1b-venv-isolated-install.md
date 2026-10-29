# C3-1b 跟进：venv 隔离安装 + import 验证

**状态**：核心 API 已落地（提交 _见下_），尚未在 `EvolutionSandbox.run_tests` 主路径上启用。

## 已做的事

在 `思忆集app/evolution/sandbox.py` 中新增三个公开函数，把"建 venv → pip 装包 → probe import"拆成可单独调用的三段：

| 函数 | 作用 | 默认超时 |
|------|------|---------|
| `create_isolated_venv(parent_dir=None)` | `python -m venv` 建临时 venv，并自检 pip 可用 | 60 s（venv）+ 15 s（pip check）|
| `pip_install_in_venv(python_exe, pkg_spec, *, index_url=None, extra_args=None, timeout=None)` | 在指定 venv 内 `pip install --no-cache-dir pkg_spec` | 120 s |
| `run_isolated_install_probe(pkg_name, *, version=None, include_members=False, keep_venv=False, parent_dir=None, index_url=None)` | 完整流水线：建 venv → install → 用 venv 内 python 跑 `_probe_import_subprocess` → 默认清理 | 综合 |

`_probe_import_subprocess` 同时被加上了 `python_executable` 与 `timeout` 两个关键字参数，使得 probe 既能在主机 venv 跑（fast path，保留原行为），也能在新建的隔离 venv 内跑（slow path）。

### 安全要点
- `pip_install_in_venv` 拒绝包含 `;& | \` $ \n \r` 等 shell 元字符的 spec，并且只通过 `subprocess.run([...])` 列表传参，杜绝 shell 注入。
- `run_isolated_install_probe` 的 `pkg_name` 必须满足 `[A-Za-z0-9_-]+`，否则直接返回 `stage=venv` 失败。
- 所有 subprocess 都带 `timeout=`、`capture_output=True`、`encoding="utf-8"`、`errors="replace"`，崩溃 / 超时 / spawn 失败均映射为结构化 `{ok: False, reason: ...}`。
- 默认 `keep_venv=False`，`finally` 中 `shutil.rmtree(..., ignore_errors=True)` 清理。

## 测试

`思忆集app/tests/test_evolution_sandbox.py` 共 **26 个用例**：

- 原 12 个 subprocess-isolation 用例：全部保留并通过。
- 新增 13 个用例覆盖 `create_isolated_venv` / `pip_install_in_venv` / `run_isolated_install_probe` 的成功、失败、超时、安全防护、清理路径，全部通过 mock 完成（不真正建 venv）。
- 1 个端到端慢测 `test_run_isolated_install_probe_real_pip`：默认 SKIP，设 `SIYI_RUN_SLOW_VENV=1` 即真实建 venv + `pip install packaging` + probe。

最近一次运行：`25 passed, 1 skipped in 34.18s`。

## 尚未做的事（下一轮可选）

1. **把 `EvolutionSandbox.run_tests` 升级为可选 venv 模式**
   - 给 `EvolutionSandbox.__init__(sandbox_type=SANDBOX_TYPE_VENV)` 接入 `run_isolated_install_probe`，把 `import_test` / `basic_functionality` / `integration_check` 三步换成"先 install 再 probe"的真实验证。
   - 用 `finding.latest_version` 作为版本，`finding.name` 作为 pkg 名。
   - 失败时 `SandboxRun.metadata` 携带 `install_error` / `probe_error`，前端展示原因。
   - 默认仍走 `SANDBOX_TYPE_NONE`，避免单次 evolution 评估爆磁盘/网络。

2. **并发上限 & 磁盘配额**
   - 同一时刻最多 N=2 个 venv 在跑（asyncio.Semaphore）。
   - 全局临时目录大小阈值（如 2 GB）超出后强制清理最老的 venv。

3. **国内 index 默认值**
   - 当探测到环境是中国大陆出口时（可选探测 / settings 配置），默认 `index_url=https://pypi.tuna.tsinghua.edu.cn/simple`，避免 60s+ 超时。

4. **复用 venv 池**
   - 同一个 Python 版本的"空 venv"复用一份（拷贝代替重建），把每轮 60 s 的 `python -m venv` 砍到 ~5 s。

5. **Windows 长路径 / 杀软风险**
   - Windows 在 `Local\Temp` 下创建 venv 偶尔被 Defender 实时扫描卡住，必要时改到 `SIYI_DATA_DIR\sandbox_venvs\` 并加入排除项指南。

## 验收命令

```powershell
cd D:\h\3.25\思忆集\思忆集app
..\.venv3.11\Scripts\python.exe -m pytest tests/test_evolution_sandbox.py -v
# 期望：25 passed, 1 skipped

# 想跑真实端到端：
$env:SIYI_RUN_SLOW_VENV="1"
..\.venv3.11\Scripts\python.exe -m pytest tests/test_evolution_sandbox.py::test_run_isolated_install_probe_real_pip -v
```
