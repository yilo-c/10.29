"""P1 集成测试：ReAct Catalog ↔ 执行体一致性自动验证

确保 REACT_TOOL_CATALOG 中每个 action_id 都能被**唯一执行体**接住。

C-1 执行器收敛 步骤 3 语义重指向：本测试原来靠
``inspect.getsource(make_react_executor)`` 搜 ``"action.id"`` 字面量来证明
「catalog 在册的工具有处理分支」——那是 B 套自写 if/elif 实现时代的探针。
执行体收敛后 B 套只剩分派（字面量必然消失），断言改为直接查 A 套
``router._handlers`` 的覆盖度；同时新增一条反向结构钉：B 套不得重新长出
动作实现阶梯（防第二执行体回潮）。
"""
import inspect
import re


def test_catalog_and_executor_consistency(tmp_path):
    """每个 catalog action_id 都在唯一执行体（A 套路由静态表）里可达。"""
    from assistant.capability_router import AssistantCapabilityRouter
    from data.local_storage import LocalDataStore
    from services.react_tool_catalog import REACT_TOOL_CATALOG

    catalog_ids = {e["action_id"] for e in REACT_TOOL_CATALOG}
    router = AssistantCapabilityRouter(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
    )
    handled = set(router._handlers)

    missing = sorted(catalog_ids - handled)
    assert not missing, f"以下 action_id 在 catalog 中定义但 A 套路由无 handler: {missing}"


def test_react_executor_has_no_inline_action_ladder():
    """防回潮钉：B 套只剩分派，不得重新长出动作 if/elif 实现阶梯。

    例外只有一个：``shell.run`` 的两阶段确认转发（A 套 shell_domain.shell_run 是
    裸执行，下沉给它等于绕过确认闭环），故允许它出现在适配层里。
    """
    from services.react_executor import make_react_executor

    source = inspect.getsource(make_react_executor)
    assert not re.search(r"elif\s+action_id\s*==", source), "B 套不得再写动作分支阶梯"
    for aid in ("file.read", "file.write", "notes.list", "tasks.create", "web.search", "app.launch"):
        assert f'"{aid}"' not in source, f"B 套内残留 {aid} 的实现/硬编码分派"


def test_react_executor_dispatches_through_capability_router():
    """结构钉：分派必须经 router.dispatch_sync（不绕到 _handlers 私有表）。"""
    from services.react_executor import make_react_executor

    source = inspect.getsource(make_react_executor)
    assert "dispatch_sync" in source
    assert "_handlers" not in source, "适配层不得直接摸 A 套私有 handler 表"


def test_safe_ids_subset_of_catalog():
    """SAFE_TOOL_IDS_BASE 应尽量与 catalog 一致，记录差异但不阻断"""
    from services.react_tool_catalog import REACT_TOOL_CATALOG, SAFE_TOOL_IDS_BASE

    catalog_ids = {e["action_id"] for e in REACT_TOOL_CATALOG}
    extra = SAFE_TOOL_IDS_BASE - catalog_ids

    # 已知的差异：这些工具在 safe list 中但未在 catalog 中详细定义
    # 原因：notes.sync/notes.organize 是旧版工具，browser.automate 是高级工具
    known_extra = {"notes.sync", "notes.organize", "browser.automate"}
    unexpected = extra - known_extra

    assert not unexpected, f"SAFE_TOOL_IDS_BASE 中有未预期的 catalog 不存在 action_id: {unexpected}"
    # 已知差异记录到日志（不阻断）
    if extra - known_extra:
        print(f"INFO: 已知差异（safe 但不在 catalog）: {extra}")


def test_openai_tool_conversion():
    """catalog 转 OpenAI tools 格式应正确"""
    from services.react_tool_catalog import (
        REACT_TOOL_CATALOG,
        react_catalog_to_openai_tools,
    )

    tools = react_catalog_to_openai_tools(REACT_TOOL_CATALOG)
    assert len(tools) == len(REACT_TOOL_CATALOG)

    for t in tools:
        assert t["type"] == "function"
        assert "function" in t
        func = t["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func


def test_catalog_has_core_crud_tools():
    """catalog 应包含核心 CRUD 工具"""
    from services.react_tool_catalog import REACT_TOOL_CATALOG

    ids = {e["action_id"] for e in REACT_TOOL_CATALOG}
    required = {
        "notes.list", "notes.create", "notes.get", "notes.update", "notes.delete",
        "tasks.list", "tasks.create", "tasks.get", "tasks.update", "tasks.delete", "tasks.toggle",
    }
    missing = required - ids
    assert not missing, f"catalog 缺少核心 CRUD 工具: {missing}"


def test_catalog_descriptions_not_empty():
    """每个工具应有非空描述"""
    from services.react_tool_catalog import REACT_TOOL_CATALOG

    for entry in REACT_TOOL_CATALOG:
        desc = entry.get("description", "").strip()
        assert desc, f"action_id={entry['action_id']} 描述为空"


def test_catalog_payload_schema_exists():
    """每个工具应有 payload_schema"""
    from services.react_tool_catalog import REACT_TOOL_CATALOG

    for entry in REACT_TOOL_CATALOG:
        schema = entry.get("payload_schema")
        assert schema is not None, f"action_id={entry['action_id']} 无 payload_schema"
        assert isinstance(schema, dict), f"action_id={entry['action_id']} payload_schema 不是 dict"
