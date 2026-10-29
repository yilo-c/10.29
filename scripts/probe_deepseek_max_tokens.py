# -*- coding: utf-8 -*-
import urllib.request, json, os
key = os.environ.get("DEEPSEEK_API_KEY", "")
for mt in [256000, 32000, 8000]:
    payload = json.dumps({
        # deepseek-chat / deepseek-reasoner 已于 2026-07-24 退役
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": mt,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("max_tokens", mt, "-> HTTP", r.status)
    except urllib.error.HTTPError as e:
        print("max_tokens", mt, "-> HTTP", e.code, e.read()[:200])
    except Exception as e:
        print("max_tokens", mt, "-> ERR", repr(e)[:120])
