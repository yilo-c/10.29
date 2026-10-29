"""AssistantMemoryLayer / PlannedStep 单元测试 '"覆盖助手核心决策涓庤忆链璺€"""



from __future__ import annotations



import sys

from pathlib import Path

from types import SimpleNamespace



APP_ROOT = Path(__file__).resolve().parents[1]

if str(APP_ROOT) not in sys.path:

    sys.path.insert(0, str(APP_ROOT))



from assistant.autonomy import AssistantMemoryLayer, AutonomousAssistantManager, PlannedStep

from assistant.intent_router import IntentClassification

from assistant.models import AgentTask

from assistant.agents.planner import AssistantPlannerLayer

from assistant.task import AssistantTaskEntry

from assistant.task_lifecycle import AgentTaskLifecycle

from data.local_storage import LocalDataStore



# 鈹€鈹€ PlannedStep 鈹€鈹€



def test_planned_step_defaults():

    step = PlannedStep(step_id="s1", action_id="news.lookup", payload={"q": "AI"})

    assert step.risk_level == "low"

    assert step.timeout_seconds == 20

    assert step.retries == 1





def test_planned_step_custom():

    step = PlannedStep(

        step_id="s2",

        action_id="shell.run",

        payload={"command": "echo hello"},

        risk_level="high",

        timeout_seconds=60,

        retries=3,

    )

    assert step.risk_level == "high"

    assert step.retries == 3





def test_agent_task_defaults():

    task = AgentTask(goal="查看今天鐨AI 新闻")



    assert task.task_id

    assert task.goal == "查看今天鐨AI 新闻"

    assert task.source == "assistant.goal"

    assert task.status == "pending"

    assert task.delivery_channel == "chat"

    assert task.created_at is not None

    assert task.steps == []

    assert task.profile_snapshot == {}





# 鈹€鈹€ AssistantMemoryLayer 鈹€鈹€



def test_memory_get_state_empty(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    memory = AssistantMemoryLayer(store)

    state = memory.get_state()

    assert "preferences" in state

    assert "common_sources" in state

    assert isinstance(state["preferences"], dict)





def test_memory_remember_preference(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    memory = AssistantMemoryLayer(store)

    memory.remember_preference("theme", "dark", source="user")

    state = memory.get_state()

    assert state["preferences"]["theme"]["value"] == "dark"

    assert state["preferences"]["theme"]["source"] == "user"

    assert state["updated_at"] is not None





def test_memory_remember_sources_dedup(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    memory = AssistantMemoryLayer(store)

    memory.remember_sources(["BBC", "CNN", "BBC", ""])

    state = memory.get_state()

    assert state["common_sources"] == ["BBC", "CNN"]





def test_memory_remember_sources_limit_50(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    memory = AssistantMemoryLayer(store)

    sources = [f"source_{i}" for i in range(60)]

    memory.remember_sources(sources)

    state = memory.get_state()

    assert len(state["common_sources"]) == 50





def test_memory_builds_user_profile_snapshot(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    memory = AssistantMemoryLayer(store)

    memory.remember_preference("focus_topics", ["AI", "鑷姩鍖"], source="user")

    memory.remember_preference("preferred_delivery", "inbox", source="user")

    memory.remember_preference("quiet_hours", ["22:00-08:00"], source="user")

    memory.remember_sources(["36姘", "BBC"])



    snapshot = memory.get_user_profile_snapshot()



    assert snapshot["focus_topics"] == ["AI", "鑷姩鍖"]

    assert snapshot["preferred_delivery"] == "inbox"

    assert snapshot["quiet_hours"] == ["22:00-08:00"]

    assert snapshot["common_sources"] == ["36姘", "BBC"]





def test_memory_append_decision(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    memory = AssistantMemoryLayer(store)

    step = PlannedStep(step_id="s1", action_id="news.lookup", payload={"q": "AI"})

    memory.append_decision("查看新闻", [step], {"ok": 1})

    decisions = store.load_json("assistant_decisions", [])

    assert len(decisions) == 1

    assert decisions[0]["goal"] == "查看新闻"





def test_memory_decisions_capped_at_300(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    memory = AssistantMemoryLayer(store)

    step = PlannedStep(step_id="s1", action_id="test", payload={})

    for i in range(310):

        memory.append_decision(f"goal_{i}", [step], {"i": i})

    decisions = store.load_json("assistant_decisions", [])

    assert len(decisions) == 300





def test_planner_uses_profile_focus_topics_for_news_query():

    planner = AssistantPlannerLayer(settings_repository=None)



    steps = planner.plan_goal(

        "帮我看今天新闻",

        user_profile={

            "focus_topics": ["AI", "自动化"],

            "common_sources": ["36氪"],

        },

    )



    news_step = next(step for step in steps if step.action_id == "news.lookup")

    assert news_step.payload["query"] == "AI OR 自动化"





def test_autonomous_manager_run_goal_returns_task_and_delivery_and_passes_profile():

    captured = {}

    manager = AutonomousAssistantManager.__new__(AutonomousAssistantManager)

    manager._ensure_planner_ai_client = lambda: None

    manager.data_store = SimpleNamespace(

        update_json=lambda key, default, updater: None,

        load_json=lambda key, default: default,

    )

    manager.memory = SimpleNamespace(

        get_user_profile_snapshot=lambda: {

            "focus_topics": ["AI", "鑷姩鍖"],

            "preferred_delivery": "inbox",

            "common_sources": ["36姘"],

        },

        append_decision=lambda goal, steps, summary: captured.update(

            remembered_goal=goal,

            remembered_steps=steps,

            remembered_summary=summary,

        ),

    )

    manager.user_profile_service = None

    manager.task_lifecycle = AgentTaskLifecycle(

        data_store=manager.data_store,

        memory=manager.memory,

        user_profile_service=manager.user_profile_service,

    )

    manager.planner = SimpleNamespace(

        plan_goal=lambda goal, user_profile=None: captured.update(

            planner_goal=goal,

            planner_profile=user_profile,

        )

        or [PlannedStep(step_id="s1", action_id="news.lookup", payload={"query": "AI OR 鑷姩鍖"})]

    )

    manager.security = SimpleNamespace(authorize_step=lambda step, **kwargs: {"allowed": True})

    manager.executor = SimpleNamespace(

        execute_plan=lambda steps, authorize_callback, task=None, delivery_channel=None, user_profile=None, **kwargs: captured.update(

            execution_steps=steps,

            execution_task=task,

            execution_delivery_channel=delivery_channel,

            execution_user_profile=user_profile,

        )

        or {

            "total": 1,

            "ok": 1,

            "failed": 0,

            "blocked": 0,

            "degraded": 0,

            "outcomes": [

                {

                    "step_id": "s1",

                    "action_id": "news.lookup",

                    "status": "ok",

                    "payload": {"count": 2},

                }

            ],

            "delivery": {

                "channel": "inbox",

                "title": "任务已完鎴",

            },

        }

    )



    result = manager.run_goal("甯垜看今天的 AI 新闻")



    assert result["task"]["goal"] == "甯垜看今天的 AI 新闻"

    assert result["task"]["source"] == "assistant.goal"

    assert result["task"]["status"] == "completed"

    assert result["delivery"]["channel"] == "inbox"

    assert captured["planner_profile"]["focus_topics"] == ["AI", "鑷姩鍖"]

    assert captured["execution_user_profile"]["preferred_delivery"] == "inbox"

    assert captured["execution_task"].goal == "甯垜看今天的 AI 新闻"





def test_autonomous_manager_run_goal_persists_agent_task_with_delivery(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    manager = AutonomousAssistantManager.__new__(AutonomousAssistantManager)

    manager.data_store = store

    manager._ensure_planner_ai_client = lambda: None

    manager.memory = SimpleNamespace(

        get_user_profile_snapshot=lambda: {

            "focus_topics": ["AI"],

            "preferred_delivery": "inbox",

        },

        append_decision=lambda goal, steps, summary: None,

    )

    manager.user_profile_service = None

    manager.task_lifecycle = AgentTaskLifecycle(

        data_store=store,

        memory=manager.memory,

        user_profile_service=manager.user_profile_service,

    )

    manager.planner = SimpleNamespace(

        plan_goal=lambda goal, user_profile=None: [

            PlannedStep(step_id="s1", action_id="news.lookup", payload={"query": "AI"})

        ]

    )

    manager.security = SimpleNamespace(authorize_step=lambda step, **kwargs: {"allowed": True})

    manager.executor = SimpleNamespace(

        execute_plan=lambda steps, authorize_callback, task=None, delivery_channel=None, user_profile=None, **kwargs: {

            "total": 1,

            "ok": 1,

            "failed": 0,

            "blocked": 0,

            "degraded": 0,

            "outcomes": [{"step_id": "s1", "action_id": "news.lookup", "status": "ok", "payload": {"count": 3}}],

            "delivery": {

                "channel": "inbox",

                "title": "任务完成：帮我看 AI 新闻",

                "content": "共有 3 条结鏋溿€",

            },

        }

    )



    result = manager.run_goal("甯垜鐪AI 新闻")

    persisted = store.load_json("assistant_agent_tasks", [])



    assert result["task"]["status"] == "completed"

    assert len(persisted) == 1

    assert persisted[0]["goal"] == "甯垜鐪AI 新闻"

    assert persisted[0]["result"]["delivery"]["channel"] == "inbox"





def test_autonomous_manager_run_steps_approved_persists_chat_session_id(tmp_path):

    store = LocalDataStore(base_dir=str(tmp_path))

    manager = AutonomousAssistantManager.__new__(AutonomousAssistantManager)

    manager.data_store = store

    manager.memory = SimpleNamespace(

        get_user_profile_snapshot=lambda: {"preferred_delivery": "chat"},

        append_decision=lambda goal, steps, summary: None,

    )

    manager.user_profile_service = None

    manager.task_lifecycle = AgentTaskLifecycle(

        data_store=store,

        memory=manager.memory,

        user_profile_service=manager.user_profile_service,

    )

    manager.security = SimpleNamespace(authorize_step=lambda step, **kwargs: {"allowed": True})

    manager.executor = SimpleNamespace(

        execute_plan=lambda steps, authorize_callback, task=None, delivery_channel=None, user_profile=None, **kwargs: {

            "total": 1,

            "ok": 1,

            "failed": 0,

            "blocked": 0,

            "degraded": 0,

            "outcomes": [{"step_id": "s1", "action_id": "news.lookup", "status": "ok", "payload": {"count": 1}}],

            "delivery": {"channel": "chat", "title": "任务完成"},

        }

    )



    steps = [PlannedStep(step_id="s1", action_id="news.lookup", payload={"query": "AI"})]



    result = manager.run_steps_approved(

        steps,

        {"news.lookup"},

        goal="甯垜鐪AI 新闻",

        chat_session_id="chat-session-42",

    )

    persisted = store.load_json("assistant_agent_tasks", [])



    assert result["task"]["chat_session_id"] == "chat-session-42"

    assert len(persisted) == 1

    assert persisted[0]["chat_session_id"] == "chat-session-42"





def test_autonomous_manager_dispatch_user_input_routes_execute_via_run_goal():

    captured = {}

    manager = AutonomousAssistantManager.__new__(AutonomousAssistantManager)

    manager.intent_router = SimpleNamespace(

        classify=lambda text: IntentClassification("execute", 0.9, "keyword_match", "news")

    )

    manager.run_goal = lambda goal, source="assistant.goal", delivery_channel=None, chat_session_id=None, **kwargs: captured.update(

        goal=goal,

        source=source,

        delivery_channel=delivery_channel,

        chat_session_id=chat_session_id,

    ) or {"delivery": {"channel": "inbox"}}



    result = manager.dispatch_user_input("甯垜看今天的 AI 新闻", source="chat_panel", chat_session_id="chat-1")



    assert result["intent"] == "execute"

    assert captured == {

        "goal": "甯垜看今天的 AI 新闻",

        "source": "chat_panel",

        "delivery_channel": None,

        "chat_session_id": "chat-1",

    }





def test_autonomous_manager_dispatch_user_input_returns_automate_classification():

    manager = AutonomousAssistantManager.__new__(AutonomousAssistantManager)

    manager.intent_router = SimpleNamespace(

        classify=lambda text: IntentClassification("automate", 0.95, "keyword_match", "daily_schedule")

    )



    result = manager.dispatch_user_input("每天早上给我鍙AI 绠€鎶")



    assert result["intent"] == "automate"

    assert result["classification"]["intent"] == "automate"

    assert result["classification"]["sub_intent"] == "daily_schedule"





def test_autonomous_manager_dispatch_user_input_falls_back_to_chat_when_router_missing():

    manager = AutonomousAssistantManager.__new__(AutonomousAssistantManager)

    manager.intent_router = None



    result = manager.dispatch_user_input("Transformer 原理鏄粈涔")



    assert result["intent"] == "chat"

    assert result["classification"]["reason"] == "router_unavailable"





def test_build_agent_task_uses_profile_preferred_delivery_when_missing_explicit_channel():

    data_store = SimpleNamespace(

        update_json=lambda key, default, updater: None,

        load_json=lambda key, default: default,

    )

    memory = SimpleNamespace(get_user_profile_snapshot=lambda: {})

    lifecycle = AgentTaskLifecycle(

        data_store=data_store,

        memory=memory,

        user_profile_service=None,

    )



    task = lifecycle.build_agent_task(

        "甯垜鐪AI 新闻",

        source="assistant.goal",

        profile_snapshot={"preferred_delivery": "inbox"},

    )



    assert task.delivery_channel == "inbox"





# 鈹€鈹€ 统一入口涓€鑷存€"鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€



def test_assistant_task_entry_points_produce_consistent_tasks():

    """鎵€有入口（chat/rule/timer/browser）产生同类型 AgentTask，source 鍜delivery 正确。"""
    data_store = SimpleNamespace(

        update_json=lambda key, default, updater: None,

        load_json=lambda key, default: default,

    )

    memory = SimpleNamespace(get_user_profile_snapshot=lambda: {})

    lifecycle = AgentTaskLifecycle(

        data_store=data_store,

        memory=memory,

        user_profile_service=None,

    )

    entry = AssistantTaskEntry(task_lifecycle=lifecycle)

    profile = {"focus_topics": ["AI"], "preferred_delivery": "inbox"}



    chat_task = entry.from_chat("甯垜查新闂", chat_session_id="chat-1", profile_snapshot=profile)

    rule_task = entry.from_rule_center("每日绠€鎶", rule_id="rule-1", profile_snapshot=profile)

    timer_task = entry.from_timer("定时鎽樿", event_name="daily_brief", profile_snapshot=profile)

    browser_task = entry.from_browser("网页鍐呭", url="http://example.com", profile_snapshot=profile)



    # 鎵€有入口都产生 AgentTask

    for t in (chat_task, rule_task, timer_task, browser_task):

        assert isinstance(t, AgentTask)

        assert t.task_id

        assert t.goal

        assert t.status == "pending"

        assert t.profile_snapshot == profile



    # source 各不相同

    assert chat_task.source == "assistant.chat"

    assert rule_task.source == "assistant.rule:rule-1"

    assert timer_task.source == "assistant.schedule:daily_brief"

    assert browser_task.source == "assistant.browser"



    # delivery_channel 按入口区鍒"
    assert chat_task.delivery_channel == "chat"

    assert rule_task.delivery_channel == "inbox"

    assert timer_task.delivery_channel == "inbox"

    assert browser_task.delivery_channel == "inbox"



    # chat_session_id 鍙紶鍏chat 入口

    assert chat_task.chat_session_id == "chat-1"

    assert rule_task.chat_session_id == ""

    assert timer_task.chat_session_id == ""

    assert browser_task.chat_session_id == ""

