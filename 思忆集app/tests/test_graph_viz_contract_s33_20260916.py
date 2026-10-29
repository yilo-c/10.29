"""S3-3 契约测试改判：knowledge.graph_for_viz 未注册属预期（防复活锁）。

沿革（2026-09-24 E2E 扫尾改判）：
- 2026-09-16 提交 d6b4e0b（S3-3 先行）：修 get_all_entities() 返回 list[dict] 而 viz
  handler 的 _build 按 dict 用（→ 任何有实体的图谱必抛 AttributeError，被外层 except 吞成
  degraded 空图 = 图谱可视化恒空根因）的一行错位，并登记"非 degraded"行为契约测试×2
  （直取 reg.handlers["knowledge.graph_for_viz"] 校验真节点/真边）。当时该 RPC 仍注册，用例绿。
- 波次①-c 提交 58e5fa8：@deprecated 僵尸 RPC 批量下线（三重确认：deprecated ∩
  UNUSED_BASELINE ∩ 前端零调用），显式删除
  registry.register_with_schema("knowledge.graph_for_viz", ...) 一行，注释标注
  「僵尸接口，前端未调用」。knowledge_graph_for_viz 实现体仅存为 register_inbox_knowledge
  内不可外部触达的闭包（无存活入口可重指向）→ 原样取 reg.handlers[...] 的本测试族 KeyError 转红。

取证（本次 E2E 扫尾，据以定唯一路线）：
- git show 58e5fa8 -- backend/handlers/_inbox_knowledge.py：register 行被**显式删除**
  （非"从未接线"的死接口，而是"曾接线后经三重确认主动下线"）；
- 全仓 ts/tsx/js/jsx/vue/html + desk-notes grep "graph_for_viz"：0 命中 → 前端零调用坐实；
- scripts/undocumented_methods_baseline.txt:54-58 注明「graph_for_viz 仍属僵尸接口，继续留在
  test_jsonrpc_contract.py 的 UNUSED_METHODS_BASELINE」（波次①-c 后随注册摘除一并从各基线清理，
  故现不在册也不在基线）；
- tests/test_e2e_memory_knowledge_20260924.py::test_e2e_ws_knowledge_graph_for_viz_should_be_available
  同因（未注册 → -32601 / KeyError）以 xfail 登记为查询面缺口，本波只登记不修。

判定：属"当初有意下线/不注册"（僵尸接口三重确认），**非**漏接线的死接口 → **不改源码**
（本次 E2E 未产生任何源码改动）。按同仓库先例 TestAgentZombieRpcRemoved
（test_integration_agent.py）把行为用例改判为「未注册属预期」防复活锁：断言
register_inbox_knowledge 不再注册该方法；若日后有人重新接线，本锁转红强制走评审。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._inbox_knowledge import register_inbox_knowledge


def _registered_methods(tmp_path: Path) -> set[str]:
    """执行 register_inbox_knowledge，返回其注册的全部 RPC 方法名集合。

    注册阶段仅定义 handler 闭包并调用 registry.register*，闭包体不在导入期执行，
    故 context 只需可被闭包捕获、无需真实图谱存储。
    """
    ctx = SimpleNamespace(knowledge_graph_store=None)

    class _Reg:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def register(self, name, fn):
            self.handlers[name] = fn

        def register_with_schema(self, name, fn, schema=None, **kw):
            self.handlers[name] = fn

    reg = _Reg()
    register_inbox_knowledge(reg, ctx)
    return set(reg.handlers)


class TestGraphForVizRegistered:
    """2026-10-15 反转：knowledge.graph_for_viz **必须注册**。

    原防复活锁的注释写着"若确需复活，请先走评审并同步删除本防复活锁"——批次 A
    （c6fb2bc7）已走全族复核（58e5fa8 的三重确认被证伪：该批连测试一并删走，
    造成"无耦合"假象；前端图谱可视化在调 graph_for_viz），注册已恢复，故按其
    指示反转本锁，改为守护"不得再次误下线"。
    """

    def test_graph_for_viz_registered(self, tmp_path):
        registered = _registered_methods(tmp_path)
        assert "knowledge.graph_for_viz" in registered, (
            "knowledge.graph_for_viz 应保持注册（前端图谱可视化依赖；恢复见批次 A c6fb2bc7）。"
        )

    def test_sibling_knowledge_graph_still_registered(self, tmp_path):
        # 对照锁：同为图谱族的 knowledge.graph 仍在册，确认下线是针对 graph_for_viz 的
        # 单点僵尸摘除，而非 register_inbox_knowledge 整体漏接线（防误判为"接线缺失"）。
        registered = _registered_methods(tmp_path)
        assert "knowledge.graph" in registered, (
            f"knowledge.graph 应仍注册（仅 graph_for_viz 下线）；实际注册集: {sorted(registered)}"
        )
