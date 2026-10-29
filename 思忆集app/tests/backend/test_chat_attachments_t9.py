# -*- coding: utf-8 -*-
"""Task #9 对话附件测试（2026-09-18）。

锁四件事：
  1. 文本附件（mime/后缀双判定）base64 解码内联进 message；
  2. 图片附件走 ai_client.chat_completion_vision（异步线程包装）注入描述；
     vision 失败 → 诚实标注、整轮不炸；
  3. 守卫：过大拒收 / dataURL 前缀剥离 / 无 vision 客户端降级 / 非附件时零副作用；
  4. chat.send 集成：attachments 参数在 gateway 判定前完成注入（enrichment 顺序）。
"""

from __future__ import annotations

import base64


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


async def test_text_attachment_inlined():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    out = await enrich_message_with_attachments(
        None,
        "总结一下这个文件",
        [{"name": "note.md", "mime": "text/markdown", "data_base64": _b64("# 标题\n正文内容")}],
    )
    assert out.startswith("总结一下这个文件")
    assert "attachment name='note.md'" in out
    assert "# 标题" in out
    assert "系统对附件的解析注入" in out


async def test_text_attachment_by_suffix_without_mime():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    out = await enrich_message_with_attachments(None, "看代码", [{"name": "main.py", "mime": "", "data_base64": _b64("print('hi')")}])
    assert "print('hi')" in out


async def test_dataurl_prefix_stripped():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    out = await enrich_message_with_attachments(
        None, "x", [{"name": "a.txt", "mime": "text/plain", "data_base64": "data:text/plain;base64," + _b64("hello")}]
    )
    assert "hello" in out
    assert "解码失败" not in out


async def test_image_attachment_uses_vision():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    calls = {}

    class FakeClient:
        def chat_completion_vision(self, *, text, image_base64, image_format, max_tokens):
            calls["text"] = text
            calls["fmt"] = image_format
            calls["b64"] = image_base64[:8]
            return {"content": "一只橘猫趴在键盘上"}

    class Ctx:
        ai_client = FakeClient()

    out = await enrich_message_with_attachments(
        Ctx(), "这图里是什么动物", [{"name": "cat.png", "mime": "image/png", "data_base64": _b64("fakeimagedata")}]
    )
    assert "一只橘猫趴在键盘上" in out
    assert calls["fmt"] == "png"
    assert "关键信息" in calls["text"]  # vision prompt 传达到位


async def test_image_jpg_normalized_to_jpeg():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    seen = {}

    class FakeClient:
        def chat_completion_vision(self, *, text, image_base64, image_format, max_tokens):
            seen["fmt"] = image_format
            return {"content": "ok"}

    class Ctx:
        ai_client = FakeClient()

    await enrich_message_with_attachments(Ctx(), "x", [{"name": "a.jpg", "mime": "image/jpeg", "data_base64": _b64("zz")}])
    # mime=image/jpeg → fmt=jpeg；name 后缀 jpg 不影响（mime 优先）
    assert seen["fmt"] == "jpeg"


async def test_vision_failure_degrades_honestly():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    class BrokenClient:
        def chat_completion_vision(self, **kw):
            raise RuntimeError("provider 401")

    class Ctx:
        ai_client = BrokenClient()

    out = await enrich_message_with_attachments(Ctx(), "看图", [{"name": "a.png", "mime": "image/png", "data_base64": _b64("zz")}])
    assert "图片解析失败" in out
    assert "provider 401" in out  # 诚实带原因


async def test_no_vision_configured_no_client():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    out = await enrich_message_with_attachments(object(), "看图", [{"name": "a.png", "mime": "image/png", "data_base64": _b64("zz")}])
    assert "未配置可用的视觉模型" in out


async def test_oversized_rejected_and_guarded():
    from backend.handlers.chat_paths import attachments as att

    big = "A" * (att.MAX_FILE_B64_BYTES + 10)
    out = await att.enrich_message_with_attachments(None, "x", [{"name": "big.txt", "mime": "text/plain", "data_base64": big}])
    assert "过大已拒收" in out
    assert big not in out


async def test_truncation_single_and_total_budget():
    from backend.handlers.chat_paths import attachments as att

    text = "字" * (att.MAX_TEXT_CHARS + 500)
    out = await att.enrich_message_with_attachments(None, "x", [{"name": "long.txt", "mime": "text/plain", "data_base64": _b64(text)}])
    assert "截断" in out

    many = [{"name": f"p{i}.txt", "mime": "text/plain", "data_base64": _b64("嗯" * 30_000)} for i in range(4)]
    out2 = await att.enrich_message_with_attachments(None, "x", many)
    assert "超出整轮附件预算" in out2


async def test_no_attachments_passthrough():
    from backend.handlers.chat_paths.attachments import enrich_message_with_attachments

    assert await enrich_message_with_attachments(None, "hello", None) == "hello"
    assert await enrich_message_with_attachments(None, "hello", []) == "hello"


async def test_chat_send_enriches_before_branches(monkeypatch):
    """集成：chat.send 带 attachments 时，进入执行路径前 message 已含注入块。

    手段：register_chat_memory 拿真实 chat_send 闭包；patch preflight 放行、
    patch 网关路由为 False、patch butler 路径捕获 message 并空收尾。
    """
    import backend.handlers._chat_memory as cm
    import backend.handlers.chat_paths.helpers as helpers

    captured = {}

    async def fake_preflight(context, send_chunk, control=None):
        return True

    async def fake_run(context, message, params, msgs, send_chunk, control=None):
        captured["message"] = message
        captured["msgs"] = msgs
        return
        yield ""  # noqa: 保持 async generator 形态

    monkeypatch.setattr(helpers, "preflight_ai_or_emit", fake_preflight)
    monkeypatch.setattr(cm, "_use_gateway_route", lambda context, params, message: False)
    monkeypatch.setattr(cm, "run_pydantic_agent_path", fake_run)

    class FakeRegistry:
        def __init__(self):
            self.handlers = {}

        def register(self, name, handler, **kw):
            self.handlers[name] = handler

        def register_with_schema(self, name, handler, **kw):
            self.handlers[name] = handler

        def __getattr__(self, item):
            # 其余注册面（set_risk / register_precheck 等）一律 no-op
            return lambda *a, **k: None

    reg = FakeRegistry()

    class Ctx:
        pass

    cm.register_chat_memory(reg, Ctx())
    chat_send = reg.handlers["chat.send"]

    async def send_chunk(*a, **k):
        return None

    async for _ in chat_send(
        send_chunk=send_chunk,
        message="读一下附件",
        attachments=[{"name": "s.txt", "mime": "text/plain", "data_base64": _b64("ATTACH-SENTINEL")}],
    ):
        pass

    assert "ATTACH-SENTINEL" in captured["message"]
    assert captured["message"].startswith("读一下附件")
    # build_messages 产出的 user 消息（msgs[0]）同样带注入（butler 路径 msgs 来自同一 message）
    user_msgs = [m for m in captured["msgs"] if m.get("role") == "user"]
    assert user_msgs and any("ATTACH-SENTINEL" in str(m.get("content", "")) for m in user_msgs)
