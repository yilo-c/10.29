import sys
sys.path.insert(0, '.')
from automation_hub.api.db.session import get_db_session
from automation_hub.api.models.tools import Tool

# 测试自动提交
with get_db_session() as session:
    tool = Tool(
        id='test-tool-1',
        name='test-tool',
        description='Test tool for session manager',
        risk_level='low',
        executor='shell',
        args_schema_json={},
        command_json={'cmd': 'echo hello'},
        timeout_sec=30,
        allowed_paths_json=[],
        is_enabled=True
    )
    session.add(tool)
