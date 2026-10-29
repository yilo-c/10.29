"""diag_ai_chain.py — AI 对话链路分段计时探针（T1 诊断收口工具）。

背景：AI 对话（如"帮我整理项目的笔记"）经常数百秒无输出。根因链见
deliverables/diag-ai-hang-2026-08-30.md。本脚本把链路各环节的延迟拆成
可复测的分段计时，供修复前后对照。

用法（仓库根目录）：
    python scripts/diag_ai_chain.py                       # 默认测 qwen2.5:3b，judge 超时 15s
    python scripts/diag_ai_chain.py --model qwen3:4b      # 对照模型
    python scripts/diag_ai_chain.py --cold --json         # 链路模拟前卸载模型（复现生产冷态故障）
    python scripts/diag_ai_chain.py --timeout 60 --budget 90

分段：
  [1] tags       Ollama 已安装模型清单（仅本地端点）
  [2] direct x2  直连 chat/completions 两次（热态基线）
  [3] privacy    PrivacyClassifier 对 JUDGE_SYSTEM_PROMPT 与用户 prompt 的分类（红证据）
  [4] chain      生产回退链模拟：local(1 次) → local_coder(N 次+指数退避)，逐次计时

只做只读诊断：不写任何文件、不改设置；对 Ollama 的唯一副作用是 --cold 时的
keep_alive=0 卸载（可恢复）。退出码：0 正常；2 链路总耗时超过 --budget。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "思忆集app"

JUDGE_SYSTEM_PROMPT = None  # 尽力从应用代码加载；失败则用占位并如实标注
PRIVACY_CLASSIFIER = None


def _try_import_app_code() -> None:
    """把思忆集app 加入 sys.path 并加载 JUDGE_SYSTEM_PROMPT / PrivacyClassifier。"""
    global JUDGE_SYSTEM_PROMPT, PRIVACY_CLASSIFIER
    try:
        sys.path.insert(0, str(APP_DIR))
        from assistant.routing.judge_decision import JUDGE_SYSTEM_PROMPT as _jsp
        from assistant.routing.privacy_classifier import PrivacyClassifier as _pc

        JUDGE_SYSTEM_PROMPT = _jsp
        PRIVACY_CLASSIFIER = _pc()
    except Exception as exc:  # noqa: BLE001 — 诊断工具须在任何环境下可用
        print(f"[warn] 应用代码加载失败（privacy/chain 段将降级）: {exc}", file=sys.stderr)
        if str(APP_DIR) in sys.path:
            sys.path.remove(str(APP_DIR))


def _post_chat(base_url: str, model: str, messages: list, timeout: float, max_tokens: int) -> tuple[bool, float, str]:
    """一次 chat/completions 调用。返回 (ok, 耗时秒, 摘要)。"""
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        dt = time.perf_counter() - t0
        text = ""
        try:
            text = (data["choices"][0]["message"].get("content") or "")[:50]
        except Exception:  # noqa: BLE001
            pass
        return True, dt, text
    except Exception as exc:  # noqa: BLE001
        return False, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}"[:120]


def _ollama_tags(base_url: str) -> list[str] | None:
    """GET /api/tags 模型清单（非 Ollama 端点返回 None）。"""
    host = base_url.split("/v1")[0]
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=5) as resp:
            tags = json.loads(resp.read())
        return [m["name"] for m in tags.get("models", [])]
    except Exception:  # noqa: BLE001
        return None


def _unload(base_url: str, model: str) -> bool:
    """keep_alive=0 卸载模型（模拟冷加载）。"""
    host = base_url.split("/v1")[0]
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    req = urllib.request.Request(host + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 对话链路分段计时探针")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--timeout", type=float, default=15.0, help="每段超时秒数（judge 路由生产值为 15）")
    ap.add_argument("--max-tokens", type=int, default=150)
    ap.add_argument("--prompt", default="帮我整理项目的笔记")
    ap.add_argument("--cold", action="store_true", help="链路模拟前卸载模型（keep_alive=0）复现生产冷态 408")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--budget", type=float, default=30.0, help="链路模拟总耗时报警阈值（秒）")
    ap.add_argument("--retries-coder", type=int, default=3, help="local_coder 腿重试次数（生产默认 3）")
    ap.add_argument("--skip-chain", action="store_true", help="跳过第 [4] 段链路模拟")
    args = ap.parse_args()

    _try_import_app_code()
    sections: list[dict] = []

    # [1] tags
    tags = _ollama_tags(args.base_url)
    sections.append({"stage": "tags", "models": tags})

    # [2] direct x2
    user_msgs: list[dict] = [{"role": "user", "content": "hi"}]
    ok1, dt1, txt1 = _post_chat(args.base_url, args.model, user_msgs, max(60.0, args.timeout), 16)
    ok2, dt2, _ = _post_chat(args.base_url, args.model, user_msgs, max(60.0, args.timeout), 16)
    sections.append(
        {
            "stage": "direct",
            "call1_s": round(dt1, 2),
            "call1_ok": ok1,
            "call2_s": round(dt2, 2),
            "call2_ok": ok2,
            "note1": txt1,
        }
    )

    # [3] privacy 红证据
    if PRIVACY_CLASSIFIER is not None and JUDGE_SYSTEM_PROMPT is not None:
        sections.append(
            {
                "stage": "privacy",
                "classify_judge_system_prompt": PRIVACY_CLASSIFIER.classify(JUDGE_SYSTEM_PROMPT),
                "hits_in_judge_prompt": [p for p in PRIVACY_CLASSIFIER._SENSITIVE_PATTERNS if p in JUDGE_SYSTEM_PROMPT],
                "classify_user_prompt": PRIVACY_CLASSIFIER.classify(args.prompt),
            }
        )
    else:
        sections.append({"stage": "privacy", "skipped": "app code unavailable"})

    # [4] 生产回退链模拟：judge 式消息 + 短超时 + max_retries=1/3
    if not args.skip_chain:
        if args.cold:
            ok_unload = _unload(args.base_url, args.model)
            sections.append({"stage": "unload", "ok": ok_unload})
        sys_msgs = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT or "路由决策占位"},
            {"role": "user", "content": args.prompt},
        ]
        attempts: list[dict] = []
        chain_t0 = time.perf_counter()
        # local 腿：max_retries=1（ai/client/config.py:472/:522）
        ok, dt, note = _post_chat(args.base_url, args.model, sys_msgs, args.timeout, args.max_tokens)
        attempts.append({"stage": "local", "attempt": 1, "ok": ok, "s": round(dt, 2), "note": note if not ok else "OK"})
        # local_coder 腿：max_retries=3 + 2**attempt 退避（OpenAICompatibleClient 默认）
        if not ok:
            for attempt in range(1, args.retries_coder + 1):
                ok, dt, note = _post_chat(args.base_url, args.model, sys_msgs, args.timeout, args.max_tokens)
                attempts.append({"stage": "local_coder", "attempt": attempt, "ok": ok, "s": round(dt, 2), "note": note if not ok else "OK"})
                if ok:
                    break
                if attempt < args.retries_coder:
                    time.sleep(2**attempt)
        chain_total = time.perf_counter() - chain_t0
        sections.append({"stage": "chain_sim", "attempts": attempts, "total_s": round(chain_total, 2), "exhausted": not ok})

    if args.json:
        print(json.dumps({"base_url": args.base_url, "model": args.model, "sections": sections}, ensure_ascii=False, indent=1))
    else:
        print(f"== diag_ai_chain: {args.base_url} model={args.model} timeout={args.timeout}s ==")
        for sec in sections:
            print(json.dumps(sec, ensure_ascii=False))
        chain = next((s for s in sections if s.get("stage") == "chain_sim"), None)
        if chain:
            verdict = "EXHAUSTED（生产日志 fallback_exhausted 复现）" if chain["exhausted"] else "RECOVERED on retry"
            print(f"== 链路总耗时 {chain['total_s']}s（budget {args.budget}s）→ {verdict} ==")

    chain = next((s for s in sections if s.get("stage") == "chain_sim"), None)
    if chain and chain["total_s"] > args.budget:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
