# -*- coding: utf-8 -*-
"""_image / _process / _info_sources 新旧对拍：注册面 + 行为。"""
import asyncio
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
with zipfile.ZipFile(r"D:\siyi_pyc_backup_20260808.zip") as zf:
    for name in ("_image", "_process", "_info_sources"):
        (tmp / f"legacy_{name}.pyc").write_bytes(zf.read(f"思忆集app/backend/handlers/{name}.pyc"))
sys.path.insert(0, str(tmp))
sys.path.insert(0, str(REPO / "思忆集app"))

import legacy__image as old_img  # noqa: E402
import legacy__process as old_proc  # noqa: E402
import legacy__info_sources as old_is  # noqa: E402
from backend.handlers import _image as new_img  # noqa: E402
from backend.handlers import _process as new_proc  # noqa: E402
from backend.handlers import _info_sources as new_is  # noqa: E402

fails = []


def chk(n, c):
    if not c:
        fails.append(n)
        print("FAIL:", n)


# ── 结构对拍 ──
chk("img doc", old_img.__doc__ == new_img.__doc__)
chk("img logger", old_img.logger.name == new_img.logger.name)
chk("proc doc", old_proc.__doc__ == new_proc.__doc__)
chk("proc logger", old_proc.logger.name == new_proc.logger.name)
chk("is doc", old_is.__doc__ == new_is.__doc__)
chk("is logger", old_is.logger.name == new_is.logger.name)
import inspect

chk("is register async", inspect.iscoroutinefunction(old_is.register) == inspect.iscoroutinefunction(new_is.register))


class FakeRegistry:
    def __init__(self):
        self.methods = {}

    def register(self, name, fn):
        self.methods[name] = fn


class FakeContext:
    """按需挂属性的空 context。"""


# ── _image 行为对拍 ──
class FakeImageService:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def generate(self, prompt, size="1024x1024", n=1):
        self.calls.append((prompt, size, n))
        if self.fail:
            raise RuntimeError("boom")
        return {"success": True, "images": [{"b64_data": "xxx", "format": "png"}]}


async def drive_image(register_fn):
    outs = []
    # 1) service 未注入
    reg, ctx = FakeRegistry(), FakeContext()
    register_fn(reg, ctx)
    outs.append(sorted(reg.methods))
    outs.append(await reg.methods["ai.generate_image"](prompt=""))
    outs.append(await reg.methods["ai.generate_image"](prompt="   "))
    outs.append(await reg.methods["ai.generate_image.status"]())
    # 2) service 注入 + 参数规范化（n 越界/size 非法）
    svc = FakeImageService()
    ctx2 = FakeContext(); ctx2.image_generator_service = svc
    reg2 = FakeRegistry(); register_fn(reg2, ctx2)
    outs.append(await reg2.methods["ai.generate_image"](prompt=" 一只猫 ", size="999x999", n=99))
    outs.append(svc.calls)
    outs.append(await reg2.methods["ai.generate_image"](prompt="x", n=-5))
    outs.append(svc.calls)
    # 3) service 抛异常
    svc_f = FakeImageService(fail=True)
    ctx3 = FakeContext(); ctx3.image_generator_service = svc_f
    reg3 = FakeRegistry(); register_fn(reg3, ctx3)
    outs.append(await reg3.methods["ai.generate_image"](prompt="x"))
    # 4) status：有 settings key
    class SR:
        def get_setting(self, k, d=""):
            return "sk-test" if k == "openai_api_key" else d
    ctx4 = FakeContext(); ctx4.image_generator_service = svc; ctx4.settings_repository = SR()
    reg4 = FakeRegistry(); register_fn(reg4, ctx4)
    outs.append(await reg4.methods["ai.generate_image.status"]())
    return outs


old_img_out = asyncio.run(drive_image(old_img.register_image))
new_img_out = asyncio.run(drive_image(new_img.register_image))
for i, (a, b) in enumerate(zip(old_img_out, new_img_out)):
    chk(f"img case {i}", a == b)


# ── _process 行为对拍 ──
class FakeLifecycleManager:
    def __init__(self):
        self.calls = []

    def list_processes(self, alive_only=False):
        self.calls.append(("list", alive_only))
        return [{"id": "p1", "alive": True}] if not alive_only else []

    def get_status(self, pid):
        self.calls.append(("status", pid))
        return {"id": pid, "cmd": "x"} if pid == "p1" else None

    def get_recent_output(self, pid, stream="stdout", limit=50):
        self.calls.append(("output", pid, stream, limit))
        return [f"line{i}" for i in range(min(limit, 2))]

    def terminate(self, pid):
        self.calls.append(("terminate", pid))
        return {"success": True, "terminated": pid}


async def drive_process(register_fn):
    outs = []
    # 无 manager
    reg, ctx = FakeRegistry(), FakeContext()
    register_fn(reg, ctx)
    outs.append(sorted(reg.methods))
    outs.append(await reg.methods["process.list"]())
    outs.append(await reg.methods["process.status"]())
    outs.append(await reg.methods["process.status"](process_id="p1"))
    outs.append(await reg.methods["process.output"](process_id="p1", stream="bad"))
    outs.append(await reg.methods["process.terminate"](process_id=""))
    # 直接挂 context
    m1 = FakeLifecycleManager()
    ctx1 = FakeContext(); ctx1.process_lifecycle_manager = m1
    reg1 = FakeRegistry(); register_fn(reg1, ctx1)
    outs.append(await reg1.methods["process.list"](alive_only=1))
    outs.append(await reg1.methods["process.status"](process_id=" p1 "))
    outs.append(await reg1.methods["process.status"](process_id="nope"))
    outs.append(await reg1.methods["process.output"](process_id="p1", stream="stderr", limit="3"))
    outs.append(await reg1.methods["process.terminate"](process_id="p1"))
    outs.append(m1.calls)
    # 回退路径：autonomous_assistant_manager.router
    class Router:
        process_lifecycle_manager = FakeLifecycleManager()
    class Autonomy:
        router = Router()
    ctx2 = FakeContext(); ctx2.autonomous_assistant_manager = Autonomy()
    reg2 = FakeRegistry(); register_fn(reg2, ctx2)
    outs.append(await reg2.methods["process.list"]())
    return outs


old_proc_out = asyncio.run(drive_process(old_proc.register_process))
new_proc_out = asyncio.run(drive_process(new_proc.register_process))
for i, (a, b) in enumerate(zip(old_proc_out, new_proc_out)):
    chk(f"proc case {i}", a == b)


# ── _info_sources 行为对拍（共用同一个 pyc 版 InfoSourceRegistry，状态隔离用各自 settings）──
class DictSettings:
    def __init__(self):
        self.d = {}

    def get_setting(self, k, default=None):
        return self.d.get(k, default)

    def set_setting(self, k, v):
        self.d[k] = v


async def drive_info_sources(register_fn):
    outs = []
    ctx = FakeContext(); ctx.settings_repository = DictSettings()
    reg = FakeRegistry()
    await register_fn(reg, ctx)
    outs.append(sorted(reg.methods))
    outs.append(await reg.methods["info.sources.list"]())
    outs.append(await reg.methods["info.sources.toggle"]())
    outs.append(await reg.methods["info.sources.toggle"](name="no_such_source"))
    outs.append(await reg.methods["info.sources.toggle"](name="hackernews", enabled=False))
    outs.append(await reg.methods["info.sources.add"]())
    outs.append(await reg.methods["info.sources.add"](name="hackernews", url="http://x"))
    outs.append(
        await reg.methods["info.sources.add"](
            name="myblog", url="https://example.com/rss", parser_type="rss", language="zh", display_name="我的博客"
        )
    )
    outs.append(await reg.methods["info.sources.remove"]())
    outs.append(await reg.methods["info.sources.remove"](name="hackernews"))
    outs.append(await reg.methods["info.sources.remove"](name="myblog"))
    outs.append(await reg.methods["info.sources.reset"]())
    return outs


old_is_out = asyncio.run(drive_info_sources(old_is.register))
new_is_out = asyncio.run(drive_info_sources(new_is.register))
for i, (a, b) in enumerate(zip(old_is_out, new_is_out)):
    chk(f"is case {i}", a == b)

if fails:
    print(f"\n共 {len(fails)} 项失败")
    sys.exit(1)
print("ALL MATCH: _image / _process / _info_sources 对拍通过")
