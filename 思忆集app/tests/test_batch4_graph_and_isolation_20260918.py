# -*- coding: utf-8 -*-
"""批4 治本回归：知识图谱 + 测试隔离（2026-09-18）。

覆盖 5 项（每项至少一条结构性/行为级机器断言）：

* **PD-ISO-b**：离线开关必须真正拦住 LLM 出网。旧病：隔离后端虽然带着离线
  开关启动，但开关只被 headless_main 的 RSS/autonomy 两处消费，``ai_client``
  完全无视 → AUTOSTART 集成测试每次真实联网调 DeepSeek 烧配额。
* **PD-NODEL**：图谱实体删除 RPC 缺失 → 用户/AI 都没有"删掉这个实体"的入口，
  只能靠级联或全图重建。补 ``knowledge.entity.delete`` 并过 R4 审批闸门。
* **PD-5a**：external 导入通道无名称级去重 → 大小写异体（``P-D量子计算入门``
  / ``pd量子计算入门``）各建一个图谱节点，同一实体被拆成两个。
* **PD-5b**：``find_by_name`` 的子串包含折叠把新实体吞并进旧实体
  （``量子`` 被折叠进 ``量子计算``，真实体永久丢失）。
* **PD-5c**：地名正则量词贪婪吞动词前缀（``李四说要去上海市`` → 入库
  ``四说要去上海市``）。

隔离约定（硬约束 7）：本文件**不写出完整的 ``SIYI_`` 变量名字面量** ——
``scripts/gen_inventory.py`` 是文本级扫描（含注释/docstring），写出来会让
"权威环境变量清单"多出不存在的开关。一律经 ``core.isolation`` 的常量引用。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from core import isolation
from knowledge.graph_extractor import KnowledgeGraphExtractor
from knowledge.graph_store import KnowledgeGraphStore

APP_ROOT = Path(__file__).resolve().parents[1]

# ── 合成 key：刻意避开 SIYI_ 命名空间（gen_inventory 文本级扫描会入账）──
_FAKE_BASE = "https://api.deepseek.com/v1"
_FAKE_KEY = "FAKE_KEY_NOT_REAL"


# ══════════════════════════════════════════════════════════════════
# PD-ISO-b —— 离线开关是"与数据目录隔离同级"的基元，且真的拦住出网
# ══════════════════════════════════════════════════════════════════


def _boom_connect(*_args, **_kwargs):  # noqa: ANN002, ANN003
    raise AssertionError("离线模式下不得建立任何真实网络连接（socket.connect 被调用）")


def test_pd_iso_b_offline_blocks_ai_client_network(monkeypatch):
    """离线开关打开时，AI 客户端必须在**传输层**拒绝出网（不是靠上层自觉）。

    结构性断言：连 ``socket.socket.connect`` 都被换成"一调即炸"，若客户端
    仍尝试联网，断言消息会直接指出"网络真的被碰了"。
    """
    monkeypatch.setenv(isolation.OFFLINE_ENV_KEY, "1")
    assert isolation.is_offline_mode() is True, "离线基元未识别显式离线声明"

    from ai.client.base import OfflineRequestBlocked, OpenAICompatibleClient

    monkeypatch.setattr(socket.socket, "connect", _boom_connect)

    client = OpenAICompatibleClient(api_base=_FAKE_BASE, api_key=_FAKE_KEY, model="FAKE_MODEL")
    with pytest.raises(OfflineRequestBlocked):
        client.chat_completion([{"role": "user", "content": "hi"}])
    # 流式通道同源（同一个传输层闸门），也必须被拦
    with pytest.raises(OfflineRequestBlocked):
        list(client.chat_completion_stream([{"role": "user", "content": "hi"}]))


async def test_pd_iso_b_offline_blocks_async_ai_client(monkeypatch):
    """异步通道（astream/achat）同样被拦——闸门装在传输层，两条腿都覆盖。"""
    monkeypatch.setenv(isolation.OFFLINE_ENV_KEY, "1")
    from ai.client.base import OfflineRequestBlocked, OpenAICompatibleClient

    monkeypatch.setattr(socket.socket, "connect", _boom_connect)
    client = OpenAICompatibleClient(api_base=_FAKE_BASE, api_key=_FAKE_KEY, model="FAKE_MODEL")

    with pytest.raises(OfflineRequestBlocked):
        await client.achat_completion([{"role": "user", "content": "hi"}])
    with pytest.raises(OfflineRequestBlocked):
        async for _chunk in client.achat_completion_stream([{"role": "user", "content": "hi"}]):
            pass  # pragma: no cover — 离线时永远拿不到 chunk


def test_pd_iso_b_online_path_unaffected(monkeypatch):
    """反向护栏：未声明离线时**不得**误伤——正常请求照发（防"一刀切禁网"回归）。"""
    monkeypatch.delenv(isolation.OFFLINE_ENV_KEY, raising=False)
    assert isolation.is_offline_mode() is False

    from ai.client.base import OpenAICompatibleClient

    calls: list[str] = []

    def _fake_post(self, url, **kwargs):  # noqa: ANN001, ARG001
        calls.append(str(url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
            request=httpx.Request("POST", str(url)),
        )

    monkeypatch.setattr(httpx.Client, "post", _fake_post)
    client = OpenAICompatibleClient(api_base=_FAKE_BASE, api_key=_FAKE_KEY, model="FAKE_MODEL")
    out = client.chat_completion([{"role": "user", "content": "hi"}])
    assert out.get("content") == "ok"
    assert len(calls) == 1


def test_pd_iso_b_kernel_ai_respects_offline(monkeypatch):
    """``kernel_generate`` 必须尊重离线基元：内核与 fallback 双双不得被调用。

    内核（agent_gateway）会拉起成熟 agent 进程，自身也会出网；只拦 ai_client
    等于留了第二条烧配额的腿。
    """
    monkeypatch.setenv(isolation.OFFLINE_ENV_KEY, "1")
    from services.kernel_ai import kernel_generate

    calls: list[str] = []

    class _Gateway:
        name = "FAKE_GATEWAY"

        def submit(self, prompt, timeout_s=180):  # noqa: ANN001, ARG002
            calls.append("kernel")
            return SimpleNamespace(ok=True, result="不应被调用")

    def _fallback():
        calls.append("fallback")
        return "不应被调用"

    text = kernel_generate(_Gateway(), "你好", fallback=_fallback)
    assert text == "", "离线模式必须空手而归"
    assert calls == [], f"离线模式仍调用了 AI 通道: {calls}"


def test_pd_iso_b_autostart_child_env_declares_offline():
    """链路闭合：AUTOSTART 拉起的临时后端子进程环境必须携带离线声明。

    没有这条，``ai_client`` 侧的闸门就是"装了但没人开"——AUTOSTART 依旧烧配额。
    断言走**常量引用**（而非字面量）：既证明子进程环境确实带上了开关，也证明
    conftest 与 core.isolation 是同一真源（写一个、读另一个正是本缺陷的成因）。
    """
    import conftest as _conftest

    assert _conftest.OFFLINE_ENV_KEY == isolation.OFFLINE_ENV_KEY, "conftest 与离线基元已分叉"
    src = inspect.getsource(_conftest._maybe_autostart_backend)
    assert "OFFLINE_ENV_KEY" in src, "AUTOSTART 子进程环境未声明离线开关 → 隔离后端仍会真实联网"


# ══════════════════════════════════════════════════════════════════
# PD-NODEL —— 图谱实体删除 RPC 存在且过 R4 审批闸门
# ══════════════════════════════════════════════════════════════════


class _RegistryContext:
    """最小 AppContext：register_all 注册期只需要 base_dir + settings_repository。"""

    def __init__(self, tmp_dir: str) -> None:
        self.local_data_store = SimpleNamespace(base_dir=tmp_dir)
        self.settings_repository = MagicMock()
        self.knowledge_graph_store = None


@pytest.fixture
def full_registry(tmp_path):
    from backend.handlers import register_all
    from backend.method_registry import MethodRegistry

    reg = MethodRegistry()
    asyncio.run(register_all(reg, _RegistryContext(str(tmp_path)), ws_port=18765))
    return reg


def test_pd_nodel_entity_delete_registered_and_r4(full_registry):
    """注册表真源：删除实体 RPC 存在，且落在 R4 集中清单（走 sandbox 审批）。"""
    assert full_registry.has_method("knowledge.entity.delete"), "图谱实体删除 RPC 仍缺失"
    assert full_registry.is_r4("knowledge.entity.delete"), "删除实体未过 R4 闸门（可绕 sandbox）"


@pytest.mark.asyncio
async def test_pd_nodel_unapproved_delete_pending_not_executed(full_registry):
    """攻击链：未审批调用必须返回 pending 且**不执行**（不可逆删除不得裸奔）。"""

    class _FakeSandbox:
        def __init__(self) -> None:
            self.added: list[str] = []

        def add(self, action, args, preview, kind=None):  # noqa: ANN001, ARG002
            self.added.append(action)
            return f"pid-{len(self.added)}"

    fake = _FakeSandbox()
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        res = await full_registry.dispatch("knowledge.entity.delete", {"entity_id": "e1"})

    assert res.get("requires_approval") is True
    assert res.get("pending") is True
    assert fake.added == ["knowledge.entity.delete"], "未审批删除未进入 sandbox 审批队列"


def test_pd_nodel_handler_deletes_entity_and_saves(tmp_path):
    """行为级：审批后 handler 真删实体 + 级联边 + 落盘（重开 store 不复现）。"""
    from backend.handlers._inbox_knowledge import register_inbox_knowledge
    from backend.method_registry import MethodRegistry

    kg_path = tmp_path / "kg.json"
    kg = KnowledgeGraphStore(path=kg_path)
    kg.add_entity("e1", "甲", "concept")
    kg.add_entity("e2", "乙", "concept")
    kg.add_relation("e1", "e2", "related_to")
    kg.save()

    reg = MethodRegistry()
    ctx = SimpleNamespace(knowledge_graph_store=kg, local_data_store=SimpleNamespace(base_dir=str(tmp_path)))
    register_inbox_knowledge(reg, ctx)

    res = asyncio.run(reg.dispatch("knowledge.entity.delete", {"entity_id": "e1"}))
    assert res.get("success") is True and res.get("deleted") is True, f"删除回执异常: {res}"
    assert kg.get_entity("e1") is None, "实体未删除"
    assert kg.get_relations("e2") == [], "触及被删实体的边未级联清理"

    reloaded = KnowledgeGraphStore(path=kg_path)
    reloaded.load()
    assert reloaded.get_entity("e1") is None, "删除未落盘（重开 store 复活）"

    # 不存在 / 空参：如实 no-op，不得谎报成功
    missing = asyncio.run(reg.dispatch("knowledge.entity.delete", {"entity_id": "nope"}))
    assert missing.get("success") is False and missing.get("deleted") is False
    empty = asyncio.run(reg.dispatch("knowledge.entity.delete", {}))
    assert empty.get("success") is False


# ══════════════════════════════════════════════════════════════════
# PD-5a —— external 导入按规范化名称去重
# ══════════════════════════════════════════════════════════════════

_CLIPPING = (
    "测试笔记·{title} (张三)\r\n- Location: 10-12 | Added on 2026年9月17日 上午10:00:00\r\n\r\n量子计算利用叠加态进行并行计算。\r\n==========\r\n"
)


def test_pd_5a_import_dedupe_by_normalized_name(tmp_path):
    """大小写异体的同一篇笔记只应入一次库、只建一个图谱节点。

    旧病：kindle 的 external_id 由标题派生（``标题_位置``），大小写一变就是
    新 id → ``_dedupe_existing`` 放行 → 新文章 + 新 ``external_content_*`` 节点。

    勘察纠偏（2026-09-18）：探针 ``test_kg_probe_pd_20260917.py`` 的 C 段自称
    变体"只剩大小写差异"，实测其 ``.replace`` 同时**删掉了连字符**
    （``P-D`` → ``pd``）。本测试用**纯大小写**变体（``P-D`` → ``p-d``），与
    "大小写异体"的原始描述一致；连字符折叠属另一类语义变更，见下一个测试的
    边界说明。
    """
    from backend.handlers._external_content import _import_kindle_file

    articles_path = str(tmp_path / "saved_articles.json")
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")

    f1 = tmp_path / "a.txt"
    f1.write_text(_CLIPPING.format(title="P-D量子计算入门"), encoding="utf-8")
    r1 = _import_kindle_file(str(f1), articles_path, kg)
    assert r1.get("imported") == 1, f"首轮应导入 1 条: {r1}"

    f2 = tmp_path / "b.txt"
    f2.write_text(_CLIPPING.format(title="p-d量子计算入门"), encoding="utf-8")
    r2 = _import_kindle_file(str(f2), articles_path, kg)
    assert r2.get("imported") == 0, f"大小写异体应被名称级去重拦下: {r2}"
    assert r2.get("skipped") == 1

    nodes = [e for e in kg.get_all_entities() if str(e["id"]).startswith("external_content_")]
    assert len(nodes) == 1, f"同一实体被拆成多个图谱节点: {[n['name'] for n in nodes]}"

    saved = json.loads(Path(articles_path).read_text(encoding="utf-8"))
    assert len(saved["articles"]) == 1, "articles 层同样不得出现大小写异体重复"


def test_pd_5a_fullwidth_variant_deduped(tmp_path):
    """全角/半角异体同属"表示差异"，必须并成一条（NFKC 折叠）。"""
    from backend.handlers._external_content import _import_kindle_file

    articles_path = str(tmp_path / "saved_articles.json")
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")

    f1 = tmp_path / "a.txt"
    f1.write_text(_CLIPPING.format(title="DeepSeek 入门"), encoding="utf-8")
    f2 = tmp_path / "b.txt"
    f2.write_text(_CLIPPING.format(title="ＤｅｅｐＳｅｅｋ 入门"), encoding="utf-8")
    assert _import_kindle_file(str(f1), articles_path, kg).get("imported") == 1
    r2 = _import_kindle_file(str(f2), articles_path, kg)
    assert r2.get("imported") == 0 and r2.get("skipped") == 1, f"全角异体未去重: {r2}"


def test_pd_5a_separator_variants_are_intentionally_not_folded(tmp_path):
    """**边界说明（有意为之，非缺陷固化）**：分隔符差异不折叠。

    ``P-D量子计算入门`` 与 ``pd量子计算入门`` 只差大小写与连字符，人看是同一本；
    但"删掉连字符"是**语义**变更而非**表示**变更——``A-B`` 与 ``AB`` 可能是两个
    不同实体，一律折叠会制造"两个真实体被并成一个"的新缺陷（比重复节点更难发
    现：它静默丢信息）。故本批只折叠表示层差异（大小写 / 全角半角 / 空白），
    把分隔符差异留给未来的"别名（aliases）"机制显式表达。
    """
    from backend.handlers._external_content import _import_kindle_file

    articles_path = str(tmp_path / "saved_articles.json")
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")

    f1 = tmp_path / "a.txt"
    f1.write_text(_CLIPPING.format(title="P-D量子计算入门"), encoding="utf-8")
    f2 = tmp_path / "b.txt"
    f2.write_text(_CLIPPING.format(title="pd量子计算入门"), encoding="utf-8")
    assert _import_kindle_file(str(f1), articles_path, kg).get("imported") == 1
    assert _import_kindle_file(str(f2), articles_path, kg).get("imported") == 1


def test_pd_5a_dedupe_does_not_merge_distinct_titles(tmp_path):
    """反向护栏：规范化去重只能吃"同一名称的异体"，不得吞并真正不同的条目。"""
    from backend.handlers._external_content import _import_kindle_file

    articles_path = str(tmp_path / "saved_articles.json")
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")

    f1 = tmp_path / "a.txt"
    f1.write_text(_CLIPPING.format(title="P-D量子计算入门"), encoding="utf-8")
    f2 = tmp_path / "b.txt"
    f2.write_text(_CLIPPING.format(title="完全不同的另一本书"), encoding="utf-8")
    assert _import_kindle_file(str(f1), articles_path, kg).get("imported") == 1
    assert _import_kindle_file(str(f2), articles_path, kg).get("imported") == 1
    nodes = [e for e in kg.get_all_entities() if str(e["id"]).startswith("external_content_")]
    assert len(nodes) == 2


# ══════════════════════════════════════════════════════════════════
# PD-5b —— find_by_name 精确/规范化优先，子串折叠降级为显式选项
# ══════════════════════════════════════════════════════════════════


def test_pd_5b_exact_match_does_not_fold_substring(tmp_path):
    """默认语义 = 身份解析：精确/规范化命中，绝不把短名折叠进长名。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e1", "量子计算", "concept")

    assert store.find_by_name("量子") == [], "子串折叠仍在默认路径上吞并候选"
    assert [e["id"] for e in store.find_by_name("量子计算")] == ["e1"]

    store.add_entity("e2", "DeepSeek", "concept")
    assert [e["id"] for e in store.find_by_name("  deepseek  ")] == ["e2"], "规范化（去空白+大小写）命中失败"
    assert [e["id"] for e in store.find_by_name("ＤｅｅｐＳｅｅｋ")] == ["e2"], "全角异体未规范化"


def test_pd_5b_substring_folding_is_explicit_opt_in(tmp_path):
    """折叠能力**保留**（检索类调用方仍需要），但必须显式开启。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e1", "量子计算", "concept")
    folded = store.find_by_name("量子", allow_substring=True)
    assert [e["id"] for e in folded] == ["e1"]


def test_pd_5b_extractor_keeps_new_entity(tmp_path):
    """端到端：已有「量子计算」时再提取「量子」，新实体必须真建出来。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _FakeLLM(
        [
            json.dumps({"entities": [{"name": "量子计算", "type": "concept"}], "relations": []}, ensure_ascii=False),
            json.dumps({"entities": [{"name": "量子", "type": "concept"}], "relations": []}, ensure_ascii=False),
        ]
    )
    ex = KnowledgeGraphExtractor(store, ai_client=llm)
    ex.extract_from_text("量子计算", use_llm=True)
    ex.extract_from_text("量子", use_llm=True)
    names = sorted(e["name"] for e in store.get_all_entities())
    assert names == ["量子", "量子计算"], f"新实体被折叠吞并: {names}"


class _FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)

    def complete_text(self, messages, temperature=0.2, max_tokens=None, **kwargs):  # noqa: ANN001, ARG002
        return self._replies.pop(0) if self._replies else '{"entities": [], "relations": []}'


# ══════════════════════════════════════════════════════════════════
# PD-5c —— 地名正则不得吞并动词前缀
# ══════════════════════════════════════════════════════════════════


def test_pd_5c_location_pattern_does_not_swallow_verb_prefix(tmp_path):
    """实测反例：「李四说要去上海市」不得产出「四说要去上海市」。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    ex = KnowledgeGraphExtractor(store, ai_client=None)
    ex.extract_from_text("李四说要去上海市出差", use_llm=False)
    names = sorted(e["name"] for e in store.get_all_entities())
    assert "上海市" in names, f"正经地名反而没提出来: {names}"
    assert not any(n.endswith("市") and len(n) > 3 for n in names), f"地名仍被吞动词前缀: {names}"


def test_pd_5c_plain_locations_still_extracted(tmp_path):
    """反向护栏：收紧左边界不得把正常地名一起收掉。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    ex = KnowledgeGraphExtractor(store, ai_client=None)
    ex.extract_from_text("我在北京市上班，下周去上海市出差", use_llm=False)
    names = sorted(e["name"] for e in store.get_all_entities())
    assert "北京市" in names and "上海市" in names, f"正常地名漏提: {names}"
