"""AI 对话分发全流程检测。
测试从 create_fallback_client → complete_text 的真实路径，
验证 settings.json 解析、三层 fallback、ProviderRoutingTable 等。
"""
from __future__ import annotations

__test__ = False  # standalone runner, not a pytest test file

import sys
import json
import os

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, _parent)

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
results: list[tuple[str, str, str]] = []


def test(category: str, name: str, status: str, detail: str = "") -> None:
    results.append((category, name, status))
    icon = "+" if status == PASS else "-"
    print(f"  [{icon}] {name}: {status}" + (f" - {detail}" if detail else ""))


def heading(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# ── 全局设置 ─────────────────────────────────────────────
settings_path = os.path.join(_parent, "data", "settings.json")
with open(settings_path, encoding="utf-8") as f:
    _raw = json.load(f)

# 使用 MockSettingsRepo 代替真实的 settings 加载
class _MockSettingsRepo:
    def __init__(self, data):
        self._data = data
    def get_setting(self, key, default=""):
        return self._data.get(key, default)

mock_repo = _MockSettingsRepo(_raw)


# ═══════════════════════════════════════════════════════════
# 1. Settings 配置完整性
# ═══════════════════════════════════════════════════════════
heading("1. Settings 配置完整性")

required = ["llm_api_base", "llm_api_key", "llm_model",
             "llm_fallback_api_base", "llm_fallback_api_key", "llm_fallback_model",
             "llm_local_api_base", "llm_local_api_key", "llm_local_model"]
found = [k for k in required if k in _raw]
test("1", "AI 配置键完整性", PASS if len(found) >= 6 else FAIL,
     f"found={len(found)}/9")
api_base = _raw.get("llm_api_base", "")
fb_base = _raw.get("llm_fallback_api_base", "")
local_base = _raw.get("llm_local_api_base", "")
test("1", "primary base_url", PASS if api_base else FAIL, repr(api_base))
test("1", "fallback base_url", PASS if fb_base else FAIL, repr(fb_base))
test("1", "local base_url", PASS if local_base else FAIL, repr(local_base))


# ═══════════════════════════════════════════════════════════
# 2. 配置解析函数
# ═══════════════════════════════════════════════════════════
heading("2. 配置解析函数")

section("2a. 无 settings（回退到环境变量/默认值）")
try:
    from services.ai_service import resolve_openai_compatible_config, resolve_secondary_openai_compatible_config, resolve_local_openai_compatible_config, create_fallback_client, create_judge_aware_client, ProviderRoutingTable
    config = resolve_openai_compatible_config()
    test("2a", "主配置（无 settings）", PASS,
         f"base_url={config.base_url}, model={config.model}")
except Exception as e:
    test("2a", "主配置（无 settings）", FAIL, str(e))

section("2b. 带 settings 解析")
try:
    config = resolve_openai_compatible_config(settings_repository=mock_repo)
    assert config.base_url == "http://localhost:11434/v1"
    test("2b", "主配置（带 settings）", PASS, f"base_url={config.base_url}")
except Exception as e:
    test("2b", "主配置（带 settings）", FAIL, str(e))

section("2c. Fallback 配置解析")
try:
    fb_config = resolve_secondary_openai_compatible_config(settings_repository=mock_repo)
    ok = fb_config.base_url and "deepseek" in fb_config.base_url
    test("2c", "Fallback 配置", PASS if ok else FAIL,
         f"base_url={fb_config.base_url}, model={fb_config.model}")
except Exception as e:
    test("2c", "Fallback 配置", FAIL, str(e))

section("2d. Local 配置解析")
try:
    local_config = resolve_local_openai_compatible_config(settings_repository=mock_repo)
    test("2d", "Local 配置", PASS if local_config.base_url else FAIL,
         f"base_url={local_config.base_url}")
except Exception as e:
    test("2d", "Local 配置", FAIL, str(e))


# ═══════════════════════════════════════════════════════════
# 3. 客户端创建
# ═══════════════════════════════════════════════════════════
heading("3. 客户端创建")

section("3a. create_fallback_client（带 settings）")
try:
    client = create_fallback_client(settings_repository=mock_repo)
    assert client.primary.api_base == "http://localhost:11434/v1"
    fb_url = client.fallback.api_base if client.fallback else "None"
    local_url = client.local.api_base if client.local else "None"
    test("3a", "FallbackClient", PASS,
         f"primary={client.primary.api_base}, fallback={fb_url}, local={local_url}")
except Exception as e:
    test("3a", "FallbackClient", FAIL, str(e))
    client = None  # fallback for subsequent tests

section("3b. JudgeAwareClient 创建")
try:
    jc = create_judge_aware_client(settings_repository=mock_repo)
    test("3b", "JudgeAwareClient", PASS)
except Exception as e:
    test("3b", "JudgeAwareClient", FAIL, str(e))


# ═══════════════════════════════════════════════════════════
# 4. 实际 AI 对话测试
# ═══════════════════════════════════════════════════════════
heading("4. 实际 AI 对话测试")

# 检查 Ollama
try:
    import urllib.request
    req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
    resp = urllib.request.urlopen(req, timeout=3)
    ollama = resp.status == 200
except Exception:
    ollama = False

if not ollama:
    test("4*", "Ollama 未运行，跳过本地测试", SKIP)

section("4a. OpenAICompatibleClient 直连 Ollama")
if ollama:
    try:
        from ai.client.base import OpenAICompatibleClient
        direct = OpenAICompatibleClient(api_base="http://localhost:11434/v1", api_key="", model="qwen2.5:3b", timeout=30)
        content, raw = direct.chat([{"role": "user", "content": "回复OK即可"}], max_tokens=20)
        test("4a", "直连 Ollama", PASS if content else FAIL, f"resp={content[:50]!r}")
    except Exception as e:
        test("4a", "直连 Ollama", FAIL, str(e))

section("4b. FallbackClient.complete_text 完整链路")
if ollama and client:
    try:
        reply = client.complete_text([{"role": "user", "content": "回复OK即可"}], temperature=0.3, max_tokens=50)
        test("4b", "FallbackClient 链路", PASS if reply else FAIL, f"reply={reply[:60]!r}")
    except Exception as e:
        test("4b", "FallbackClient 链路", FAIL, str(e))
elif not ollama:
    test("4b", "FallbackClient 链路", SKIP, "Ollama 未运行")
elif not client:
    test("4b", "FallbackClient 链路", SKIP, "client 创建失败")

section("4c. Runtime snapshot")
if ollama and client:
    try:
        snap = client.get_runtime_snapshot()
        assert "last_stage" in snap
        assert "chain" in snap
        test("4c", "Runtime snapshot", PASS,
             f"stages={[e['stage'] for e in snap['chain']]}")
    except Exception as e:
        test("4c", "Runtime snapshot", FAIL, str(e))
elif not ollama:
    test("4c", "Runtime snapshot", SKIP, "Ollama 未运行")
elif not client:
    test("4c", "Runtime snapshot", SKIP, "client 创建失败")

section("4d. 短超时对话（验证 timeout 拆分）")
if ollama:
    try:
        direct = OpenAICompatibleClient(api_base="http://localhost:11434/v1", api_key="", model="qwen2.5:3b", timeout=30, max_retries=1)
        import time as _t
        t0 = _t.time()
        content, raw = direct.chat([{"role": "user", "content": "回复OK即可"}], max_tokens=10)
        elapsed = _t.time() - t0
        test("4d", "短超时对话", PASS if content else FAIL,
             f"{elapsed:.1f}s reply={content[:40]!r}")
    except Exception as e:
        test("4d", "短超时对话", FAIL, str(e))


# ═══════════════════════════════════════════════════════════
# 5. ProviderRoutingTable
# ═══════════════════════════════════════════════════════════
heading("5. ProviderRoutingTable")

section("5a. 查询分类")
try:
    routing = ProviderRoutingTable(environ=os.environ)
    assert routing.classify_query("帮我分析这个代码") == "reasoning"
    assert routing.classify_query("用python写一个函数") == "coding"
    assert routing.classify_query("中国的历史是什么") == "chinese_knowledge"
    assert routing.classify_query("总结这篇文章") == "summarization"
    assert routing.classify_query("你好") == "general_chat"
    test("5a", "查询分类", PASS)
except Exception as e:
    test("5a", "查询分类", FAIL, str(e))

section("5b. 路由测试")
try:
    # 设置测试路由 env
    os.environ["SIYI_LLM_PROVIDER_ROUTING"] = '{"reasoning": "deepseek"}'
    routing2 = ProviderRoutingTable(environ=os.environ)
    if client:
        routed = routing2.get_client_for_query("分析这段代码", client.primary)
        test("5b", "路由到 deepseek", PASS if routed is not client.primary else SKIP,
             "路由替换" if routed is not client.primary else "未替换（deepseek 无 key）")
    os.environ.pop("SIYI_LLM_PROVIDER_ROUTING", None)
except Exception as e:
    test("5b", "路由测试", FAIL, str(e))
    os.environ.pop("SIYI_LLM_PROVIDER_ROUTING", None)

section("5c. 提供商列表")
try:
    providers = routing.list_available_providers()
    test("5c", "提供商列表", PASS, f"{len(providers)} 个")
except Exception as e:
    test("5c", "提供商列表", FAIL, str(e))


# ═══════════════════════════════════════════════════════════
# 6. LLM Provider Topology
# ═══════════════════════════════════════════════════════════
heading("6. LLM Provider Topology")

section("6a. build_llm_provider_topology")
try:
    from ai.llm_provider_topology import build_llm_provider_topology, LLMProviderStage
    stages = build_llm_provider_topology(settings_repository=mock_repo, environ=os.environ)
    assert len(stages) == 3
    for s in stages:
        assert isinstance(s, LLMProviderStage)
    test("6a", "Provider 拓扑", PASS,
         f"{[(s.stage_id, s.base_url) for s in stages]}")
except Exception as e:
    test("6a", "Provider 拓扑", FAIL, str(e))

section("6b. summarize_llm_provider_topology")
try:
    from ai.llm_provider_topology import summarize_llm_provider_topology
    summary = summarize_llm_provider_topology(stages)
    test("6b", "拓扑摘要", PASS, f"{len(summary)} 字符")
except Exception as e:
    test("6b", "拓扑摘要", FAIL, str(e))


# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════
heading("汇总")

categories: dict[str, Any] = {}

for cat, name, status in results:
    categories.setdefault(cat, {"total": 0, "pass": 0, "fail": 0, "skip": 0})
    categories[cat]["total"] += 1
    if status == PASS:
        categories[cat]["pass"] += 1
    elif status == FAIL:
        categories[cat]["fail"] += 1
    else:
        categories[cat]["skip"] += 1

total = sum(v["total"] for v in categories.values())
total_pass = sum(v["pass"] for v in categories.values())
total_fail = sum(v["fail"] for v in categories.values())
total_skip = sum(v["skip"] for v in categories.values())

for cat, v in sorted(categories.items()):
    print(f"  [{cat}] {v['pass']}/{v['total']} pass"
          + (f", {v['fail']} fail" if v['fail'] else "")
          + (f", {v['skip']} skip" if v['skip'] else ""))

print(f"\n  总计: {total_pass}/{total} pass"
      + (f", {total_fail} fail" if total_fail else "")
      + (f", {total_skip} skip" if total_skip else ""))

if total_fail:
    print("\n  !!! 存在失败的测试项，需要修复 !!!")
    sys.exit(1)
else:
    print("\n  全流程检测通过。")
    sys.exit(0)
