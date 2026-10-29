"""AgentGateway 适配器实现（M1）。

每个适配器把统一的 ``submit(prompt)`` 映射到一个具体内核的调用方式：

* ``DshGateway``        —— ``dsh --profile <profile> "task"``（headless 一次性会话）
* ``ClaudeCodeGateway`` —— ``claude -p --output-format json``
* ``CodexGateway``      —— ``codex exec --json``
* ``NoneGateway``       —— 内核关闭时的降级实现（功能路径不受影响）

替换内核不改这里；新增内核 = 新增一个适配器类 + factory 注册一行。

M2a：所有适配器继承 ``BaseGateway``，获得 submit_stream / resume / cancel 默认实现
（契约 §2.2），并在失败路径补 ``error_kind`` 归一化（契约 §2.3，CT-04）。
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterator

from .protocol import AgentRun, AgentRunEvent, BaseGateway

logger = logging.getLogger("siyi.agent_gateway.adapters")

# npm 生成的 .cmd/.bat 垫片里指向真实 JS 入口的参数（%dp0% 为垫片所在目录）：
#   "%_prog%"  "%dp0%\node_modules\@deepseek-ai\dsh\lib\bin.js" %*
_NPM_SHIM_JS_ARG = re.compile(r'"%dp0%\\([^"]+\.js)"', re.IGNORECASE)


def _resolve_spawn_command(exe: str) -> list[str]:
    """把一个 CLI 路径解析为 spawn 安全的 argv 前缀。

    Windows 上 npm 全局 CLI（dsh / claude / codex）都是 .cmd 批处理垫片。
    subprocess 直接 spawn 之会经 cmd.exe 重建命令行，**含换行的参数会在第一个
    换行处被 cmd.exe 截断**——多行 prompt 后半程整体丢失（2026-09-11 生产
    "内核说没有任务"事故根因：_build_kernel_prompt 拼出的多行消息经 dsh.CMD
    只剩首行「以下是近期对话记录（供理解上下文）：」，dsh 收不到任务正文）。

    垫片若能解析出 node JS 入口（npm 生成格式），则绕过 cmd.exe 用
    ``[node.exe, bin.js]`` 直调；原生 exe / 无法解析时原样返回 ``[exe]``。
    """
    if os.name != "nt" or not exe or not exe.lower().endswith((".cmd", ".bat")):
        return [exe]
    try:
        with open(exe, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return [exe]
    m = _NPM_SHIM_JS_ARG.search(text)
    if not m:
        return [exe]
    shim_dir = os.path.dirname(os.path.abspath(exe))
    bin_js = os.path.join(shim_dir, *m.group(1).split("\\"))
    if not os.path.isfile(bin_js):
        return [exe]
    # 与垫片同逻辑：同目录 node.exe 优先，否则 PATH 上的 node
    local_node = os.path.join(shim_dir, "node.exe")
    node = local_node if os.path.isfile(local_node) else shutil.which("node")
    if not node:
        return [exe]
    return [node, bin_js]


def _classify_error(stderr: str, returncode: int) -> str:
    """把 CLI 失败归一化为契约 §2.3 的 error_kind。"""
    s = (stderr or "").lower()
    if returncode == 0:
        return "internal"
    if any(k in s for k in ("401", "403", "unauthorized", "auth", "login", "invalid api", "api key", "apikey")):
        return "auth"
    if any(k in s for k in ("429", "402", "quota", "rate limit", "rate-limit", "too many requests", "insufficient")):
        return "quota"
    if any(k in s for k in ("timeout", "timed out", "timed-out", "deadline")):
        return "timeout"
    return "internal"


def _clean_env() -> dict:
    """dsh 作为独立内核进程，不应继承宿主注入的 ``NODE_OPTIONS`` 等 shim。

    否则 CodeBuddy 等 IDE 的 node-safe-delete-shim 会拦截 dsh 的 profile
    初始化删除操作，导致 dsh 启动即崩溃（SAFE_DELETE_BULK_CONFIRM_REQUIRED）。
    清空后 dsh 才能正常初始化并调用 LLM。
    """
    env = os.environ.copy()
    env.pop("NODE_OPTIONS", None)
    return env


def _write_model_patch_yaml(model_patch: str) -> str | None:
    """把 ``"provider/model"`` 写成 dsh ``--patch`` 一次性 overlay，返回补丁文件路径。

    方案 B（provider 注入）：不改 ``~/.dsh`` 全局 settings.yaml（全局手工配置
    并发不安全且已两次漂移——agent-default-model 误配图像模型、指向悬空
    provider），而是 spawn 时挂临时补丁覆盖层，subprocess.run 同步等待结束后
    由调用方删除，无持久状态。

    补丁语义与 siyi profile cordis.patch.yml 的 V6 修复同构：覆盖
    ``agent-default-model``。无效值（不含 ``/``）返回 None（不注入）。

    纠偏（2026-09-11 深夜复测）：此前怀疑"JSON_SCHEMA 禁止注释导致
    parsePatchList 崩溃"系误诊——现场用 js-yaml（同款
    ``JSON_SCHEMA.extend(!!js)``）复测，带 ``#`` 注释与 CRLF 行尾均可
    正常解析；当时的"崩溃"复现样本本身被工具链破坏了换行（出现字面
    ``/n``），属假阳性。真正的内核"没有任务"事故根因是 npm .cmd 垫片
    截断多行 argv（见 ``_resolve_spawn_command``），与本补丁格式无关。
    为最小暴露面仍保持"无注释、纯 JSON 兼容子集"写法。
    """
    provider, _, model = model_patch.strip().partition("/")
    if not provider or not model:
        logger.warning("model_patch=%r 不是 provider/model 形态，跳过 --patch 注入", model_patch)
        return None
    content = f"- id: agent-default-model\n  config:\n    provider: {provider}\n    model: {model}\n"
    fd, path = tempfile.mkstemp(prefix="siyi-dsh-patch-", suffix=".yml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def _extract_message_text(payload: dict) -> str:
    """从 Codex ``message`` 事件 payload 提取文本（content 数组兼容 str/dict）。"""
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts)
    return ""


def _parse_codex_events(stdout: str) -> list[AgentRunEvent]:
    """解析 ``codex exec --json`` 的 JSONL 事件流 → AgentRunEvent 列表。

    Codex CLI 事件（JSONL，每行一个对象）：
      - ``{"type":"item","payload":{"type":"shell_tool_call","tool":"shell","command":...,"status":...}}``
        → ``AgentRunEvent(kind="tool", ...)`` 结构化工具步骤（tool_events）
      - ``{"type":"item","payload":{"type":"text","text":"..."}}`` → delta
      - ``{"type":"message","payload":{"role":"assistant","content":[{"type":"text","text":"..."}]}}``
        → 最终结果文本（result）
    未知/解析失败行跳过（防御式）。

    说明：DSH 不暴露结构化工具事件（journal 已实证），tool_events 由
    ``codex exec --json`` 等真实输出结构化事件的内核提供；本函数纯解析、
    可用样本 JSONL 单测，不依赖 codex CLI 在场。
    """
    events: list[AgentRunEvent] = []
    result_text = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(evt, dict):
            continue
        etype = evt.get("type")
        payload = evt.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if etype == "item":
            ptype = payload.get("type")
            if ptype in ("shell_tool_call", "tool_call", "function_call"):
                events.append(
                    AgentRunEvent(
                        kind="tool",
                        payload={
                            "tool": payload.get("tool") or payload.get("name") or "shell",
                            "command": payload.get("command"),
                            "args": payload.get("args") or {},
                            "status": payload.get("status"),
                        },
                    )
                )
            elif ptype == "text" and payload.get("text"):
                events.append(AgentRunEvent(kind="delta", payload={"text": str(payload["text"])}))
        elif etype == "message":
            text = _extract_message_text(payload)
            if text:
                result_text = text
    if result_text:
        events.append(AgentRunEvent(kind="result", payload={"text": result_text, "usage": None}))
    return events


class NoneGateway(BaseGateway):
    """``agent_kernel.type = none`` 时的降级实现。"""

    name = "none"

    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun:
        return AgentRun(status="unavailable", error="agent kernel disabled (agent_kernel.type=none)", raw={"error_kind": "unavailable"})

    def health(self) -> dict:
        return {"kernel": self.name, "available": False, "note": "kernel disabled by config"}


class DshGateway(BaseGateway):
    """DeepSeek Harness 适配器：spawn ``dsh --profile <profile>`` 一次性会话。

    DSH headless 语义：新建持久会话 → 执行任务 → 打印最终答案到 stdout → 退出。
    """

    name = "dsh"

    def __init__(
        self,
        profile: str = "headless",
        cwd: str | None = None,
        timeout_s: int = 240,
        cli_path: str | None = None,
        model_patch: str = "",
        provider_env: dict[str, str] | None = None,
    ) -> None:
        self.profile = profile
        self.cwd = cwd
        self.timeout_s = timeout_s
        # 配置锚点 kernel_cli_path：锁定 dsh 可执行路径，不依赖 PATH（A7 换内核演练可指向任意路径）
        self.cli_path = cli_path
        # 方案 B（provider 注入）：model_patch = "provider/model"，spawn 时挂 --patch
        # overlay 覆盖 agent-default-model，不依赖 ~/.dsh 全局手工配置；
        # provider_env = 额外注入子进程的环境变量（apiKeyEnv → 密钥值，由
        # factory 从思忆集自身安全存储解析后传入，此处只管透传）。
        self.model_patch = model_patch
        self.provider_env = dict(provider_env) if provider_env else None

    def health(self) -> dict:
        exe = self.cli_path or shutil.which("dsh")
        # cli_path 锚点可能指向不存在路径：必须验证文件实际存在，
        # 否则 auto 探测会把坏路径误判为可用内核。
        available = exe is not None and os.path.isfile(exe)
        return {"kernel": self.name, "available": available, "exe": exe, "profile": self.profile}

    def _spawn_env(self) -> dict:
        """_clean_env 基础上合并 provider_env（provider 密钥经环境变量进入 dsh）。

        审批门禁定位（2026-09-09，每步确认语义）：dsh headless 是一次性会话，
        其内置 sandbox 默认 ``workspace-write + approval=ask`` 但在无交互应答
        通道时，`ask` 发起后无人应答 → 任务原地卡死（"danger-full-access upgrade
        requires approval, but no approval channel is available"）。因此这里注入
        ``DSH_PERMISSION_MODE=danger-full-access``：dsh 内部不再逐路径拦截/自问，
        审批门全部收敛到思忆集自身的确认链（react_executor / tool_policy →
        create_and_emit_confirmation → Electron 审批卡 → approval 后缓存结果续跑）。
        """
        env = _clean_env()
        if self.provider_env:
            env.update(self.provider_env)
        # dsh 内部审批关闭：路径级 ask 无人应答会卡死任务；改由思忆集统一审批。
        env["DSH_PERMISSION_MODE"] = "danger-full-access"
        return env

    def submit(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> AgentRun:
        exe = self.cli_path or shutil.which("dsh")
        if not exe or not os.path.isfile(exe):
            return AgentRun(status="unavailable", error="dsh CLI not found", raw={"error_kind": "unavailable"})
        target_profile = profile or self.profile  # M4：运行时 profile 覆盖（params.kernel 映射）
        timeout = timeout_s or self.timeout_s
        patch_path = _write_model_patch_yaml(self.model_patch) if self.model_patch else None
        try:
            cmd = [*_resolve_spawn_command(exe), "--profile", target_profile]
            if patch_path:
                cmd += ["--patch", patch_path]
            cmd.append(prompt)
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=self.cwd,
                env=self._spawn_env(),
            )
        except subprocess.TimeoutExpired:
            return AgentRun(status="failed", error=f"dsh timeout after {timeout}s", raw={"error_kind": "timeout"})
        finally:
            if patch_path:
                try:
                    os.remove(patch_path)
                except OSError:
                    pass
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        ok = proc.returncode == 0 and bool(out)
        raw = {"returncode": proc.returncode}
        if not ok:
            raw["error_kind"] = _classify_error(err, proc.returncode)
        return AgentRun(
            status="succeeded" if ok else "failed",
            result=out or None,
            error=None if ok else (err[-400:] or f"exit={proc.returncode}"),
            raw=raw,
        )

    def submit_stream(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> Iterator[AgentRunEvent]:
        # M4 成本分级（契约 §2.4）：把运行时 profile 覆盖透传给 submit。
        # P0（2026-08-16）：status(started) 先行产出 -- 用户发出请求瞬间即可
        # 在前端看到"内核已启动"notice，长任务期间由渐进式管道心跳保活。
        yield AgentRunEvent(kind="status", payload={"phase": "started"})
        run = self.submit(prompt, timeout_s=timeout_s, profile=profile)
        if run.result is not None:
            yield AgentRunEvent(kind="delta", payload={"text": run.result})
        if run.status == "succeeded":
            yield AgentRunEvent(
                kind="result",
                payload={"text": run.result or "", "usage": (run.raw or {}).get("usage")},
            )
        elif run.status == "unavailable":
            yield AgentRunEvent(
                kind="error",
                payload={"error_kind": "unavailable", "message": run.error or "unavailable"},
            )
        else:
            yield AgentRunEvent(
                kind="error",
                payload={
                    "error_kind": (run.raw or {}).get("error_kind", "internal"),
                    "message": run.error or "failed",
                },
            )

    def resume(self, session_ref: str, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> AgentRun:
        # M4：透传 profile 覆盖（无会话能力，退化为 submit）
        return self.submit(prompt, timeout_s=timeout_s, profile=profile)


class ClaudeCodeGateway(BaseGateway):
    """Claude Code 适配器：``claude -p --output-format json``。"""

    name = "claude_code"

    def __init__(self, model: str | None = None, max_turns: int = 8, timeout_s: int = 240) -> None:
        self.model = model
        self.max_turns = max_turns
        self.timeout_s = timeout_s

    def health(self) -> dict:
        exe = shutil.which("claude")
        return {"kernel": self.name, "available": exe is not None, "exe": exe, "note": "CLI installed; needs valid auth to run"}

    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun:
        exe = shutil.which("claude")
        if not exe:
            return AgentRun(status="unavailable", error="claude CLI not on PATH", raw={"error_kind": "unavailable"})
        cmd = [*_resolve_spawn_command(exe), "-p", "--output-format", "json", "--max-turns", str(self.max_turns)]
        if self.model:
            cmd += ["--model", self.model]
        cmd.append(prompt)
        timeout = timeout_s or self.timeout_s
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return AgentRun(status="failed", error=f"claude timeout after {timeout}s", raw={"error_kind": "timeout"})
        out = (proc.stdout or "").strip()
        text = ""
        data: dict[str, Any] = {}
        try:
            data = json.loads(out) if out else {}
            text = str(data.get("result") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
        ok = proc.returncode == 0 and bool(text)
        raw = {"returncode": proc.returncode, "is_error": data.get("is_error")}
        if not ok:
            raw["error_kind"] = _classify_error((proc.stderr or ""), proc.returncode)
        return AgentRun(
            status="succeeded" if ok else "failed",
            result=text or None,
            error=None if ok else ((proc.stderr or "").strip()[-400:] or f"exit={proc.returncode}"),
            raw=raw,
        )


class CodexGateway(BaseGateway):
    """OpenAI Codex CLI 适配器：``codex exec --json``（流式 JSON 行，取最终消息）。"""

    name = "codex"

    # Codex 原生产出结构化工具事件（shell_tool_call）与增量文本 → 声明能力
    _default_capabilities: dict[str, bool] = {
        "stream": True,
        "tool": True,
        "confirm": False,
        "resume": False,
        "cancel": False,
    }

    def __init__(self, model: str | None = None, timeout_s: int = 240) -> None:
        self.model = model
        self.timeout_s = timeout_s

    def health(self) -> dict:
        exe = shutil.which("codex")
        return {"kernel": self.name, "available": exe is not None, "exe": exe}

    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun:
        exe = shutil.which("codex")
        if not exe:
            return AgentRun(status="unavailable", error="codex CLI not on PATH", raw={"error_kind": "unavailable"})
        cmd = [*_resolve_spawn_command(exe), "exec", "--json"]
        if self.model:
            cmd += ["--model", self.model]
        cmd.append(prompt)
        timeout = timeout_s or self.timeout_s
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return AgentRun(status="failed", error=f"codex timeout after {timeout}s", raw={"error_kind": "timeout"})
        text = ""
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(evt, dict) and evt.get("message"):
                text = str(evt["message"])  # 取最后一条带 message 的事件
        ok = proc.returncode == 0 and bool(text)
        raw = {"returncode": proc.returncode}
        if not ok:
            raw["error_kind"] = _classify_error((proc.stderr or ""), proc.returncode)
        return AgentRun(
            status="succeeded" if ok else "failed",
            result=text or None,
            error=None if ok else ((proc.stderr or "").strip()[-400:] or f"exit={proc.returncode}"),
            raw=raw,
        )

    def submit_stream(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> Iterator[AgentRunEvent]:
        """流式提交：解析 ``codex exec --json`` 的 JSONL 事件，产出结构化 tool/delta/result。

        Codex 是当前暴露结构化工具事件（``shell_tool_call`` 等）的内核之一，
        前端可据 ``tool`` 事件展示工具步骤进度。
        """
        exe = shutil.which("codex")
        if not exe:
            yield AgentRunEvent(
                kind="error",
                payload={"error_kind": "unavailable", "message": "codex CLI not on PATH"},
            )
            return
        yield AgentRunEvent(kind="status", payload={"phase": "started"})
        cmd = [*_resolve_spawn_command(exe), "exec", "--json"]
        if self.model:
            cmd += ["--model", self.model]
        cmd.append(prompt)
        timeout = timeout_s or self.timeout_s
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            yield AgentRunEvent(
                kind="error",
                payload={"error_kind": "timeout", "message": f"codex timeout after {timeout}s"},
            )
            return
        events = _parse_codex_events(proc.stdout or "")
        if not events:
            yield AgentRunEvent(
                kind="error",
                payload={
                    "error_kind": _classify_error((proc.stderr or ""), proc.returncode),
                    "message": (proc.stderr or "").strip()[-400:] or f"exit={proc.returncode}",
                },
            )
            return
        yield from events


def adapter_kwargs(cls: type, kwargs: dict) -> dict:
    """按适配器构造签名过滤配置键（未知键不传入，避免 TypeError）。"""
    sig = inspect.signature(cls.__init__)
    valid = {k for k in sig.parameters if k != "self"}
    return {k: v for k, v in kwargs.items() if k in valid}
