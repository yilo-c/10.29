from __future__ import annotations

# 注意：本仓库存在双 sys.path 导致的重复模块树（api.* 与 automation_hub.api.*）。
# api/routes/agent.py 自身混用两套导入：settings 绑定 automation_hub.api.config.settings，
# AgentPlanner/ExecutionPlan 来自裸 agent 树；而 app 挂载的是裸 api.routes.agent 模块。
# 因此 monkeypatch 必须分别打在对应树的模块上才能生效。
from agent.models import ExecutionPlan, ExecutionResult, TaskType
from automation_hub.api.config import settings
from api.routes import agent as agent_routes
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client


def test_agent_repo_search_context_injects_search_results_into_session(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "AGENT_ENABLED", True)

    client = make_client()
    headers = bootstrap_headers(client)

    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text("agent needle one\nagent needle two\n", encoding="utf-8")

    repo_response = client.post(
        "/repos",
        headers=headers,
        json={"name": "agent-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    context_response = client.post(
        "/agent/context/repo-search",
        headers=headers,
        json={"repo_id": repo_id, "query": "needle", "limit": 5},
    )
    assert context_response.status_code == 200, context_response.text
    payload = context_response.json()
    assert payload["repo_id"] == repo_id
    assert payload["query"] == "needle"
    assert payload["engine"] in {"rg", "python"}
    assert payload["total"] == 2
    assert payload["injected_files"] == ["notes.txt", "notes.txt"]
    assert payload["items"] == [
        {"path": "notes.txt", "line": 1, "text": "agent needle one"},
        {"path": "notes.txt", "line": 2, "text": "agent needle two"},
    ]


def test_agent_repo_search_context_requires_repo_read_scope(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "AGENT_ENABLED", True)

    client = make_client()
    admin_headers = bootstrap_headers(client)

    device_response = client.post(
        "/auth/devices",
        headers=admin_headers,
        json={"name": "agent-only device", "platform": "windows"},
    )
    assert device_response.status_code == 200, device_response.text
    device_id = device_response.json()["id"]

    token_response = client.post(
        "/auth/tokens",
        headers=admin_headers,
        json={"device_id": device_id, "template": "agent"},
    )
    assert token_response.status_code == 200, token_response.text
    agent_headers = {"Authorization": f"Bearer {token_response.json()['token']}"}

    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text("needle\n", encoding="utf-8")

    repo_response = client.post(
        "/repos",
        headers=admin_headers,
        json={"name": "agent-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    context_response = client.post(
        "/agent/context/repo-search",
        headers=agent_headers,
        json={"repo_id": repo_id, "query": "needle", "limit": 5},
    )
    assert context_response.status_code == 403, context_response.text
    assert "missing scope: repo:read" in context_response.text


def test_agent_plan_auto_repo_context_injects_search_before_planning(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "AGENT_ENABLED", True)
    monkeypatch.setattr(agent_routes, "_get_llm_client", lambda: object())

    client = make_client()
    headers = bootstrap_headers(client)

    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text("agent needle one\n", encoding="utf-8")

    repo_response = client.post(
        "/repos",
        headers=headers,
        json={"name": "agent-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    monkeypatch.setattr(agent_routes, "_get_llm_client", lambda: object())

    async def fake_plan(self, user_query, context):
        last_search = context.get_variable("repo_search_last")
        assert last_search is not None
        assert last_search["repo_id"] == repo_id
        assert last_search["query"] == "needle"
        assert last_search["items"] == [{"path": "notes.txt", "line": 1, "text": "agent needle one"}]
        assert context.recent_files == ["notes.txt"]
        return ExecutionPlan(
            plan_id="plan-auto-repo",
            user_query=user_query,
            task_type=TaskType.CODE_SEARCH,
            steps=[],
            estimated_duration=1,
            created_at="2026-03-10T00:00:00Z",
        )

    monkeypatch.setattr(agent_routes.AgentPlanner, "plan", fake_plan)

    response = client.post(
        "/agent/plan",
        headers=headers,
        json={
            "query": "查一下相关实现",
            "repo_context": {"repo_id": repo_id, "query": "needle", "limit": 5},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plan_id"] == "plan-auto-repo"
    assert payload["repo_context"] == {
        "repo_id": repo_id,
        "query": "needle",
        "engine": payload["repo_context"]["engine"],
        "total": 1,
        "injected_files": ["notes.txt"],
        "items": [{"path": "notes.txt", "line": 1, "text": "agent needle one"}],
    }
    assert payload["repo_context"]["engine"] in {"rg", "python"}


def test_agent_ask_auto_repo_context_injects_search_before_execution(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "AGENT_ENABLED", True)
    monkeypatch.setattr(agent_routes, "_get_llm_client", lambda: object())

    client = make_client()
    headers = bootstrap_headers(client)

    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text("agent needle one\n", encoding="utf-8")

    repo_response = client.post(
        "/repos",
        headers=headers,
        json={"name": "agent-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    async def fake_plan(self, user_query, context):
        assert context.get_variable("repo_search_last")["repo_id"] == repo_id
        return ExecutionPlan(
            plan_id="ask-auto-repo",
            user_query=user_query,
            task_type=TaskType.CODE_SEARCH,
            steps=[],
            estimated_duration=1,
            created_at="2026-03-10T00:00:00Z",
        )

    async def fake_execute_plan(self, plan, user_id):
        return ExecutionResult(
            plan_id=plan.plan_id,
            status="success",
            step_results=[],
            summary="ok",
            completed_at="2026-03-10T00:00:01Z",
            total_duration=0.1,
        )

    monkeypatch.setattr(agent_routes.AgentPlanner, "plan", fake_plan)
    monkeypatch.setattr(agent_routes.AgentExecutor, "execute_plan", fake_execute_plan)

    response = client.post(
        "/agent/ask",
        headers=headers,
        json={
            "query": "根据仓库上下文回答",
            "repo_context": {"repo_id": repo_id, "query": "needle", "limit": 5},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plan_id"] == "ask-auto-repo"
    assert payload["summary"] == "ok"
    assert payload["repo_context"]["repo_id"] == repo_id
    assert payload["repo_context"]["query"] == "needle"


def test_agent_plan_auto_repo_context_requires_repo_read_scope(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "AGENT_ENABLED", True)
    monkeypatch.setattr(agent_routes, "_get_llm_client", lambda: object())

    client = make_client()
    admin_headers = bootstrap_headers(client)

    device_response = client.post(
        "/auth/devices",
        headers=admin_headers,
        json={"name": "agent-only device", "platform": "windows"},
    )
    assert device_response.status_code == 200, device_response.text
    device_id = device_response.json()["id"]

    token_response = client.post(
        "/auth/tokens",
        headers=admin_headers,
        json={"device_id": device_id, "template": "agent"},
    )
    assert token_response.status_code == 200, token_response.text
    agent_headers = {"Authorization": f"Bearer {token_response.json()['token']}"}

    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text("needle\n", encoding="utf-8")

    repo_response = client.post(
        "/repos",
        headers=admin_headers,
        json={"name": "agent-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    response = client.post(
        "/agent/plan",
        headers=agent_headers,
        json={
            "query": "查一下上下文",
            "repo_context": {"repo_id": repo_id, "query": "needle", "limit": 5},
        },
    )
    assert response.status_code == 403, response.text
    assert "missing scope: repo:read" in response.text


def test_agent_execute_plan_and_get_session(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "AGENT_ENABLED", True)
    monkeypatch.setattr(agent_routes, "_get_llm_client", lambda: object())

    client = make_client()
    headers = bootstrap_headers(client)

    async def fake_plan(self, user_query, context):
        return ExecutionPlan(
            plan_id="plan-exec-1",
            user_query=user_query,
            task_type=TaskType.CODE_SEARCH,
            steps=[],
            estimated_duration=2,
            created_at="2026-03-12T00:00:00Z",
        )

    async def fake_execute_plan(self, plan, user_id):
        return ExecutionResult(
            plan_id=plan.plan_id,
            status="success",
            step_results=[],
            summary="executed",
            completed_at="2026-03-12T00:00:01Z",
            total_duration=0.2,
        )

    monkeypatch.setattr(agent_routes.AgentPlanner, "plan", fake_plan)
    monkeypatch.setattr(agent_routes.AgentExecutor, "execute_plan", fake_execute_plan)

    plan_resp = client.post("/agent/plan", headers=headers, json={"query": "run this plan"})
    assert plan_resp.status_code == 200, plan_resp.text
    payload = plan_resp.json()
    assert payload["plan_id"] == "plan-exec-1"

    execute_resp = client.post(f"/agent/execute/{payload['plan_id']}", headers=headers)
    assert execute_resp.status_code == 200, execute_resp.text
    execute_payload = execute_resp.json()
    assert execute_payload["summary"] == "executed"
    assert execute_payload["session_id"]

    session_resp = client.get(f"/agent/sessions/{execute_payload['session_id']}", headers=headers)
    assert session_resp.status_code == 200, session_resp.text
    session_payload = session_resp.json()
    assert session_payload["session_id"] == execute_payload["session_id"]
    assert session_payload["history_count"] >= 1
    assert any(plan_info["plan_id"] == "plan-exec-1" for plan_info in session_payload["plans"])