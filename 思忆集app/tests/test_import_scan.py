"""全模块导入检测"""
import sys, os, importlib

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
sys.path.insert(0, _parent)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

modules = [
    "ai.types",
    "ai.llm_resilience",
    "ai.client.base",
    "ai.client.fallback",
    "ai.client.config",
    "ai.provider_routing",
    "ai.llm_provider_topology",
    "assistant.intent_router",
    "assistant.autonomy",
    "assistant.cc_bridge",
    "ui.assistant.message_actions",
    "ui.assistant_intent_mixin",
    "ui.assistant.message_renderer",
    "app_runtime.app_context",
    "repositories",
    "repositories._chat_session",
    "repositories._protocols",
    "data.local_storage",
    "data.app_storage_context",
    "core.settings_keys",
    "core.observability",
]

pass_count = fail_count = 0
for mod_name in modules:
    try:
        importlib.import_module(mod_name)
        print(f"  [+] {mod_name}")
        pass_count += 1
    except Exception as e:
        msg = str(e).split("\n")[0][:120]
        print(f"  [-] {mod_name}: {msg}")
        fail_count += 1

print(f"\nPass: {pass_count}, Fail: {fail_count}")
