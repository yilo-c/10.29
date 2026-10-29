"""
测试对话上下文管理
"""

import pytest
from ..context import ConversationContext


def test_create_context():
    """测试创建上下文"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    assert ctx.user_id == "user123"
    assert ctx.session_id == "session456"
    assert len(ctx.history) == 0
    assert ctx.working_directory is None


def test_add_message():
    """测试添加消息"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    ctx.add_message("user", "Hello")
    ctx.add_message("assistant", "Hi there!")
    
    assert len(ctx.history) == 2
    assert ctx.history[0]["role"] == "user"
    assert ctx.history[0]["content"] == "Hello"
    assert ctx.history[1]["role"] == "assistant"


def test_message_limit():
    """测试消息数量限制"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    # 添加 25 条消息
    for i in range(25):
        ctx.add_message("user", f"Message {i}")
    
    # 应该只保留最近 20 条
    assert len(ctx.history) == 20
    assert ctx.history[0]["content"] == "Message 5"
    assert ctx.history[-1]["content"] == "Message 24"


def test_update_working_context():
    """测试更新工作上下文"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    ctx.update_working_context(
        cwd="/home/user/project",
        files=["file1.py", "file2.py"],
        project_type="python"
    )
    
    assert ctx.working_directory == "/home/user/project"
    assert len(ctx.recent_files) == 2
    assert ctx.project_type == "python"


def test_preferences():
    """测试用户偏好"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    ctx.set_preference("theme", "dark")
    ctx.set_preference("language", "python")
    
    assert ctx.get_preference("theme") == "dark"
    assert ctx.get_preference("language") == "python"
    assert ctx.get_preference("nonexistent", "default") == "default"


def test_variables():
    """测试临时变量"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    ctx.set_variable("last_search_result", ["file1.py", "file2.py"])
    ctx.set_variable("task_id", "task123")
    
    assert ctx.get_variable("last_search_result") == ["file1.py", "file2.py"]
    assert ctx.get_variable("task_id") == "task123"
    
    ctx.clear_variables()
    assert len(ctx.variables) == 0


def test_context_summary():
    """测试上下文摘要生成"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    ctx.update_working_context(
        cwd="/home/user/project",
        files=["file1.py"],
        project_type="python"
    )
    ctx.add_message("user", "Search for TODOs")
    ctx.set_preference("code_style", "pep8")
    
    summary = ctx.get_context_summary()
    
    assert "工作目录: /home/user/project" in summary
    assert "项目类型: python" in summary
    assert "最近访问: file1.py" in summary
    assert "用户偏好: code_style=pep8" in summary
    assert "Search for TODOs" in summary


def test_serialization():
    """测试序列化和反序列化"""
    ctx = ConversationContext(user_id="user123", session_id="session456")
    
    ctx.add_message("user", "Hello")
    ctx.update_working_context(cwd="/home/user")
    ctx.set_preference("theme", "dark")
    
    # 序列化
    data = ctx.to_dict()
    
    # 反序列化
    ctx2 = ConversationContext.from_dict(data)
    
    assert ctx2.user_id == ctx.user_id
    assert ctx2.session_id == ctx.session_id
    assert len(ctx2.history) == len(ctx.history)
    assert ctx2.working_directory == ctx.working_directory
    assert ctx2.preferences == ctx.preferences


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
