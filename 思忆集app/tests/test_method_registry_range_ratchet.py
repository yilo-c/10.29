"""N-E 范围棘轮测试：method_registry 分页参数（limit/offset）通用数值校验。

背景（八轮实测 D-2 / 批 S2）：376 个静态注册方法仅约 5% 带 JSON Schema，
无 schema 方法对 `limit="abc"` / `limit=10**9` 完全无感 → handler 层 int()
抛 ValueError → -32603。本批在 dispatch / dispatch_stream 入口对约定命名
分页参数做通用棘轮：合法归一（"50"→50）、非法拒绝（-32602）、越界拒绝。

口径（backend/method_registry.py PAGE_RANGES）：
  - int（非 bool）直通；整值 float / 纯数值字符串 → int 归一**原地回写**；
  - bool / 非整值 float / 非数值字符串 / 其他类型 → InvalidParamsError(-32602)；
  - limit ∈ [0, 10000]、offset ∈ [0, 1_000_000]；limit=0 放行（存量
    handler「0=默认」语义）；显式 None 放行；缺省键不校验。
  - 归一发生在 jsonschema 之前 → schema {"type":"integer"} 对 "50" 不再误杀。

隔离：全部裸 MethodRegistry + echo handler，纯内存，不依赖真实后端。
"""

from __future__ import annotations

import pytest

from backend.method_registry import PAGE_LIMIT_MAX, PAGE_OFFSET_MAX, MethodRegistry
from core.errors import InvalidParamsError


@pytest.fixture
def reg() -> MethodRegistry:
    """裸注册中心 + echo handler：记录 handler 实际收到的参数供回写断言。"""
    r = MethodRegistry()
    seen: dict[str, dict] = {}

    async def echo(**kwargs):
        seen.update(kwargs)
        return kwargs

    async def echo_stream(send_chunk, **kwargs):
        seen.update(kwargs)
        await send_chunk("ok")
        yield "done"

    r.register("test.echo", echo)
    r.register("test.echo_stream", echo_stream)
    r._seen = seen  # type: ignore[attr-defined]
    return r


def _code(exc: InvalidParamsError) -> int:
    return exc.code


async def kwecho(**kwargs):
    return kwargs


# ── 归一（字符串/整值 float → int 回写） ──────────────────────────────


class TestNormalization:
    async def test_numeric_string_limit_coerced_and_written_back(self, reg) -> None:
        out = await reg.dispatch("test.echo", {"limit": "50"})
        assert out == {"limit": 50}, "handler 必须收到归一后的 int 而非原始字符串"
        assert reg._seen["limit"] == 50

    async def test_padded_and_signed_string_accepted(self, reg) -> None:
        out = await reg.dispatch("test.echo", {"limit": " 20 ", "offset": "+5"})
        assert out == {"limit": 20, "offset": 5}

    async def test_integral_float_coerced(self, reg) -> None:
        out = await reg.dispatch("test.echo", {"limit": 20.0})
        assert out == {"limit": 20}

    async def test_plain_int_untouched(self, reg) -> None:
        out = await reg.dispatch("test.echo", {"limit": 50, "offset": 0})
        assert out == {"limit": 50, "offset": 0}

    async def test_absent_keys_not_validated(self, reg) -> None:
        out = await reg.dispatch("test.echo", {"query": "abc"})
        assert out == {"query": "abc"}, "未携带 limit/offset 的调用不受棘轮影响"

    async def test_explicit_none_passthrough(self, reg) -> None:
        out = await reg.dispatch("test.echo", {"limit": None, "offset": None})
        assert out == {"limit": None, "offset": None}, "显式 None 交由 handler 表达「未指定」"

    async def test_caller_dict_not_mutated(self, reg) -> None:
        """归一写回的是 dispatch 私有副本，调用方原始对象（ws json 解析产物）
        不能被污染——重放/审计日志依赖原值。"""
        original = {"limit": "50"}
        await reg.dispatch("test.echo", original)
        assert original == {"limit": "50"}


# ── 非法类型 → -32602 ─────────────────────────────────────────────────


class TestInvalidTypes:
    async def test_non_numeric_string_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError) as ei:
            await reg.dispatch("test.echo", {"limit": "abc"})
        assert _code(ei.value) == -32602
        assert "limit" in ei.value.message

    async def test_bool_rejected(self, reg) -> None:
        """bool 是 int 子类，必须显式拒绝（limit=True 不是「1 条」）。"""
        for bad in (True, False):
            with pytest.raises(InvalidParamsError):
                await reg.dispatch("test.echo", {"limit": bad})

    async def test_fractional_float_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("test.echo", {"limit": 1.5})

    async def test_list_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("test.echo", {"offset": [1]})

    async def test_offset_string_also_normalized_or_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("test.echo", {"offset": "ten"})


# ── 越界 → -32602 ─────────────────────────────────────────────────────


class TestRangeBoundaries:
    async def test_negative_limit_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError) as ei:
            await reg.dispatch("test.echo", {"limit": -1})
        assert _code(ei.value) == -32602
        assert "out of range" in ei.value.message

    async def test_zero_limit_allowed(self, reg) -> None:
        """limit=0 放行——存量 handler「0=默认/不限」语义（_notes.py 等）。"""
        out = await reg.dispatch("test.echo", {"limit": 0})
        assert out == {"limit": 0}

    async def test_limit_at_max_allowed_above_rejected(self, reg) -> None:
        out = await reg.dispatch("test.echo", {"limit": PAGE_LIMIT_MAX})
        assert out == {"limit": PAGE_LIMIT_MAX}
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("test.echo", {"limit": PAGE_LIMIT_MAX + 1})

    async def test_astronomical_limit_rejected(self, reg) -> None:
        """八轮实测病灶：limit=10**9 不再穿透到 handler 打爆内存/帧。"""
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("test.echo", {"limit": 10**9})

    async def test_negative_offset_rejected_huge_allowed(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("test.echo", {"offset": -5})
        out = await reg.dispatch("test.echo", {"offset": PAGE_OFFSET_MAX})
        assert out == {"offset": PAGE_OFFSET_MAX}


# ── dispatch_stream 同口径 ────────────────────────────────────────────


async def _send(chunk, chunk_type: str = "chunk") -> None:
    return None


class TestStreamParity:
    async def test_stream_normalizes(self, reg) -> None:
        chunks = [c async for c in reg.dispatch_stream("test.echo_stream", {"limit": "30"}, _send)]
        assert chunks == ["done"]
        assert reg._seen["limit"] == 30, "流式通道同样归一回写"

    async def test_stream_rejects_invalid(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            [c async for c in reg.dispatch_stream("test.echo_stream", {"limit": "abc"}, _send)]

    async def test_stream_rejects_out_of_range(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            [c async for c in reg.dispatch_stream("test.echo_stream", {"limit": -1}, _send)]

    async def test_stream_caller_dict_not_mutated(self, reg) -> None:
        original = {"limit": "30"}
        [c async for c in reg.dispatch_stream("test.echo_stream", original, _send)]
        assert original == {"limit": "30"}


# ── 与 JSON Schema 校验协同 ───────────────────────────────────────────


class TestSchemaCoexistence:
    async def test_schema_integer_passes_after_normalization(self, reg) -> None:
        """棘轮在 jsonschema 之前归一 → schema {"type":"integer"} 对 "50"
        不再误杀（此前字符串 "50" 会被 schema 判 -32602 type error）。"""
        reg.register_with_schema(
            "test.schema_echo",
            kwecho,
            schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
        )
        out = await reg.dispatch("test.schema_echo", {"limit": "50"})
        assert out == {"limit": 50}

    async def test_schema_strictness_preserved_for_garbage(self, reg) -> None:
        """非法值在棘轮层即拦，错误消息指向具体参数名。"""
        reg.register_with_schema(
            "test.schema_echo2",
            kwecho,
            schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
        )
        with pytest.raises(InvalidParamsError) as ei:
            await reg.dispatch("test.schema_echo2", {"limit": "abc"})
        assert ei.value.details.get("param") == "limit"

    async def test_validation_order_paging_before_r4_gate(self, reg) -> None:
        """棘轮先于 R4 审批闸门——脏参数不进审批队列（-32602 直接拒）。"""
        reg.set_risk("test.echo", "R4")
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("test.echo", {"limit": "abc"})
