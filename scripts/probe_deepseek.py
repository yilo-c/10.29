import os, json, urllib.request

key = os.environ.get("DEEPSEEK_API_KEY")
print("KEY_SET:", bool(key), "KEY_PREFIX:", (key or "")[:6])

body = {
    # deepseek-chat / deepseek-reasoner 已于 2026-07-24 退役
    "model": "deepseek-v4-flash",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 16,
    "stream": False,
}
req = urllib.request.Request(
    "https://api.deepseek.com/chat/completions",
    data=json.dumps(body).encode("utf-8"),
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("STATUS:", r.status)
        txt = r.read().decode("utf-8", "replace")
        print("RESP:", txt[:500])
except urllib.error.HTTPError as e:
    print("HTTPError STATUS:", e.code)
    print("BODY:", e.read().decode("utf-8", "replace")[:500])
except Exception as e:
    print("ERR:", repr(e))
