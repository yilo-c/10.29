"""诊断：chat 带工具链路（"查看桌面文件 → 汇报"）为什么不可用。

用法（先启动后端）：
    cd 思忆集app
    ..\.venv3.11\Scripts\python.exe -m backend.headless_main   # 另开终端
    cd ..\..
    .\.venv3.11\Scripts\python.exe scripts\diag_tool_loop.py

输出 4 个问题的答案：
  Q1 模型/Provider 是否配置可用        -> provider.list
  Q2 file.read 直连（不经 LLM）能否列桌面 -> file.read RPC on Desktop
  Q3 chat.send 是否真的进入工具循环      -> 统计 text / tool_progress chunk
  Q4 工具进度对前端是否可见              -> 打印每类 chunk 的数量与首条样例

判定速查（脚本末尾也会打印）：
  Q1 空 -> 去 settings 配 llm_api_key / provider_configs
  Q2 fail -> 桌面路径问题（OneDrive 重定向/中文用户名）或 file.read 受限
  Q3 无 tool_progress -> 管家循环没进来（查后端日志 butler_agent 相关 warning）
  Q3 有 tool_progress 但无最终文本 -> 模型工具循环中断（查 UsageLimits/超时）
  Q4 只有 tool_progress 无前端展示 -> 前端渲染问题（desk-notes 侧）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    import aiohttp
except ImportError:
    print("[FATAL] 需要 aiohttp：..\\.venv3.11\\Scripts\\python.exe -m pip install aiohttp")
    sys.exit(2)

# V2.1 批次1#4：接入统一 WS 认证（后台已强制认证）。
import ws_diag_auth  # noqa: E402  （与 helper 同处 scripts/，运行目录在 sys.path[0]）

WS_URL = os.environ.get("SIYI_WS_URL", "ws://127.0.0.1:18765/ws")
TIMEOUT = 90.0


class RpcError(RuntimeError):
    pass


async def main() -> int:
    async with aiohttp.ClientSession() as session:
        async with ws_diag_auth.ws_connect(session, WS_URL, timeout=TIMEOUT) as ws:
            seq = 0

            async def rpc(method: str, params: dict, stream: bool = False):
                """发送一个 JSON-RPC 请求，等待对应 id 的最终响应。"""
                nonlocal seq
                seq += 1
                rid = seq
                payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": {**params, "stream": stream}}
                await ws.send_str(json.dumps(payload, ensure_ascii=False))
                results = []
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=TIMEOUT)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("id") != rid:
                            continue
                        if "error" in data:
                            raise RpcError(f"{method}: {data['error']}")
                        results.append(data["result"])
                        res = data["result"]
                        if isinstance(res, dict) and res.get("type") == "done":
                            return results, res
                        if isinstance(res, dict) and res.get("type") in ("chunk", "error"):
                            return results, res
                        # 非流式：拿到 result 即返回
                        if not stream:
                            return results, res
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise RpcError(f"{method}: 连接关闭")

            print("=" * 70)
            print(f"连接 {WS_URL}")

            # ── Q0 连通性 ──
            try:
                _, pong = await rpc("ping", {})
                print(f"[Q0] ping -> {pong!r}")
            except Exception as exc:
                print(f"[Q0][FATAL] 后端不可达：{exc}")
                print("   请先启动后端：cd 思忆集app && ..\\.venv3.11\\Scripts\\python.exe -m backend.headless_main")
                return 1

            # ── Q1 模型/Provider 配置 ──
            print("-" * 70)
            print("[Q1] Provider / 模型配置")
            try:
                _, prov = await rpc("provider.list", {})
                items = prov if isinstance(prov, list) else prov.get("providers", []) if isinstance(prov, dict) else []
                if not items:
                    print("    ⚠️  provider.list 为空 -> 未配置可用模型，管家循环会静默回退（无工具）")
                for p in items[:10]:
                    if isinstance(p, dict):
                        print(
                            f"    - {p.get('name') or p.get('id')} model={p.get('model')} "
                            f"enabled={p.get('enabled', '?')} health={p.get('health', '?')}"
                        )
                    else:
                        print(f"    - {p}")
            except Exception as exc:
                print(f"    ⚠️  provider.list 失败：{exc}")

            # ── Q2 file.read 直连列桌面（不经 LLM）──
            print("-" * 70)
            print("[Q2] file.read 直连列桌面目录（绕过 LLM，纯测能力层）")
            desktop = str(Path.home() / "Desktop")
            print(f"    尝试路径：{desktop}")
            try:
                _, res = await rpc("file.read", {"path": desktop})
                if isinstance(res, dict) and res.get("success"):
                    typ = res.get("type")
                    cnt = res.get("count") or len(res.get("tree", []) or [])
                    print(f"    ✅ file.read 可用：type={typ} 条目数={cnt}")
                    tree = res.get("tree") or []
                    for item in (tree if isinstance(tree, list) else [])[:8]:
                        print(f"       - {item}")
                else:
                    print(f"    ❌ file.read 失败：{res}")
                    print("       原因猜测：桌面被 OneDrive 重定向（实际在 C:\\Users\\<名>\\OneDrive\\Desktop）")
                    print("       或 _read_roots() 白名单未含真实桌面。手工测：")
                    print('       file.read {"path": "C:\\\\Users\\\\你的用户名\\\\Desktop"}')
            except Exception as exc:
                print(f"    ❌ file.read RPC 异常：{exc}")

            # ── Q3 chat.send 是否进入工具循环 ──
            print("-" * 70)
            print("[Q3] chat.send 工具循环（统计 chunk 类型）")
            msg = "请用工具列出桌面目录 C:\\Users\\" + os.environ.get("USERNAME", "") + "\\Desktop 的前 5 个条目，并汇报"
            if len(sys.argv) > 1:
                msg = sys.argv[1]
            print(f"    测试消息：{msg}")
            try:
                chunks, final = await rpc("chat.send", {"message": msg}, stream=True)
                counts: dict[str, int] = {}
                samples: dict[str, str] = {}
                for c in chunks:
                    if not isinstance(c, dict):
                        continue
                    typ = str(c.get("type") or "raw")
                    counts[typ] = counts.get(typ, 0) + 1
                    if typ not in samples and c.get("data") or c.get("tool") or c.get("text"):
                        samples[typ] = json.dumps(c, ensure_ascii=False)[:200]
                print(f"    chunk 类型统计：{counts}")
                for typ, sample in samples.items():
                    print(f"      [{typ}] {sample}")
                if counts.get("tool_progress"):
                    print("    ✅ 管家循环进入工具调用 -> 模型发了工具请求")
                    if not counts.get("chunk"):
                        print("    ⚠️  但无最终文本 -> 工具循环中断（看后端日志 UsageLimits/超时）")
                else:
                    print("    ❌ 无 tool_progress -> 管家循环没进来（静默回退 simple_stream）")
                    print("       查后端日志关键词：butler agent unavailable / build failed / pydantic agent path failed")
            except Exception as exc:
                print(f"    ❌ chat.send 异常：{exc}")

            # ── Q4 前端可见性提示 ──
            print("-" * 70)
            print("[Q4] 前端展示")
            print("    desk-notes 需渲染 tool_progress chunk（工具调用卡片）。")
            print("    若 Q3 有 tool_progress 但界面上看不到，问题在前端，不在后端。")
            print("=" * 70)
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
