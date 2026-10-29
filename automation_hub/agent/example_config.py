"""
Agent 示例配置

演示如何集成和使用 Agent 模块
"""

import asyncio
import os
from pathlib import Path

# ==================== LLM 客户端配置 ====================

class MockLLMClient:
    """模拟 LLM 客户端（开发阶段）"""
    
    async def chat_completion(self, messages, **kwargs):
        """模拟聊天补全"""
        import json
        
        # 根据用户输入返回简单的模拟响应
        user_message = messages[-1]["content"]
        
        if "搜索" in user_message or "TODO" in user_message:
            return type('Response', (), {
                'choices': [
                    type('Choice', (), {
                        'message': type('Message', (), {
                            'content': json.dumps({
                                "task_type": "code_search",
                                "steps": [
                                    {
                                        "step_id": "step_1",
                                        "tool_id": "code_search",
                                        "tool_name": "代码搜索",
                                        "args": {"pattern": "TODO"},
                                        "reason": "搜索代码中的 TODO 注释",
                                        "depends_on": [],
                                        "retry_on_fail": False,
                                        "timeout_seconds": 30,
                                        "on_fail": "stop"
                                    }
                                ],
                                "estimated_duration": 30
                            }, ensure_ascii=False)
                        })()
                    })()
                ]
            })()
        
        # 默认响应
        return type('Response', (), {
            'choices': [
                type('Choice', (), {
                    'message': type('Message', (), {
                        'content': json.dumps({
                            "task_type": "custom",
                            "steps": [
                                {
                                    "step_id": "step_1",
                                    "tool_id": "example_tool",
                                    "tool_name": "示例工具",
                                    "args": {},
                                    "reason": "执行用户请求",
                                    "depends_on": [],
                                    "retry_on_fail": False,
                                    "timeout_seconds": 60,
                                    "on_fail": "stop"
                                }
                            ],
                            "estimated_duration": 60
                        }, ensure_ascii=False)
                    })()
                })()
            ]
        })()


# 使用真实 OpenAI 兼容客户端（推荐）
# 需要设置环境变量：
#   OPENAI_API_KEY=your_key
#   OPENAI_BASE_URL=https://api.openai.com/v1  (可选)
#   OPENAI_MODEL=gpt-4o-mini                   (可选)
#
# from agent.llm_client import OpenAICompatibleClient
# llm_client = OpenAICompatibleClient()


# ==================== API 客户端配置 ====================

class MockAPIClient:
    """模拟 API 客户端（开发阶段）"""
    
    async def create_run(self, tool_id, args, user_id):
        """创建任务"""
        import uuid
        return {
            "run_id": str(uuid.uuid4()),
            "status": "queued",
            "tool_id": tool_id
        }
    
    async def get_run_status(self, run_id):
        """获取任务状态"""
        return {
            "run_id": run_id,
            "status": "succeeded",
            "output": f"模拟执行结果: {run_id}"
        }


# 使用真实 API 客户端（取消注释）
# import httpx
# 
# class APIClient:
#     def __init__(self, base_url: str, api_token: str):
#         self.base_url = base_url
#         self.headers = {"Authorization": f"Bearer {api_token}"}
#     
#     async def create_run(self, tool_id, args, user_id):
#         async with httpx.AsyncClient() as client:
#             response = await client.post(
#                 f"{self.base_url}/runs",
#                 json={"tool_id": tool_id, "args": args},
#                 headers=self.headers
#             )
#             return response.json()
#     
#     async def get_run_status(self, run_id):
#         async with httpx.AsyncClient() as client:
#             response = await client.get(
#                 f"{self.base_url}/runs/{run_id}",
#                 headers=self.headers
#             )
#             return response.json()


# ==================== 审批处理器配置 ====================

class MockApprovalHandler:
    """模拟审批处理器"""
    
    async def wait_for_approval(self, approval_id, timeout=3600):
        """等待审批"""
        # 模拟自动批准
        await asyncio.sleep(0.1)
        return True


# ==================== 使用示例 ====================

async def example_usage():
    """Agent 使用示例"""
    from automation_hub.agent import AgentPlanner, AgentExecutor, ConversationContext
    
    # 配置
    db_path = "data/automation_hub.sqlite3"
    # llm_client = OpenAICompatibleClient()
    llm_client = MockLLMClient()
    api_client = MockAPIClient()
    approval_handler = MockApprovalHandler()
    
    # 初始化
    planner = AgentPlanner(llm_client, db_path)
    executor = AgentExecutor(api_client, approval_handler)
    
    # 创建对话上下文
    context = ConversationContext(user_id="user123", session_id="session456")
    context.update_working_context(
        cwd="/home/user/project",
        project_type="python"
    )
    
    # 用户查询
    user_query = "搜索所有 TODO 注释"
    
    # 生成计划
    print("🤖 生成执行计划...")
    plan = await planner.plan(user_query, context)
    
    print(f"📋 计划ID: {plan.plan_id}")
    print(f"📝 任务类型: {plan.task_type}")
    print(f"🔢 步骤数: {len(plan.steps)}")
    print(f"⏱️  预计时长: {plan.estimated_duration}秒")
    
    for i, step in enumerate(plan.steps, 1):
        print(f"\n步骤 {i}:")
        print(f"  工具: {step.tool_name} ({step.tool_id})")
        print(f"  原因: {step.reason}")
        print(f"  参数: {step.args}")
    
    # 执行计划
    print("\n\n🚀 执行计划...")
    result = await executor.execute_plan(plan, user_id="user123")
    
    print(f"\n✅ 执行状态: {result.status}")
    print(f"⏱️  总耗时: {result.total_duration:.2f}秒")
    print(f"\n📊 执行摘要:")
    print(result.summary)
    
    # 添加到对话历史
    context.add_message("user", user_query)
    context.add_message("assistant", result.summary)


if __name__ == "__main__":
    # 运行示例
    asyncio.run(example_usage())
