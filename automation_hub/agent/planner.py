"""
Agent 规划器

将自然语言转换为可执行的工具调用计划
"""

import json
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
import sqlite3
import logging

from .models import ExecutionPlan, PlanStep, TaskType
from .llm_client import extract_content
from .context import ConversationContext

logger = logging.getLogger(__name__)


class AgentPlanner:
    """Agent 规划器 - 将自然语言转换为执行计划"""
    
    def __init__(self, llm_client, db_path: str, model: Optional[str] = None, temperature: float = 0.2):
        """
        初始化规划器
        
        Args:
            llm_client: LLM 客户端（OpenAI API 兼容）
            db_path: 数据库路径
        """
        self.llm = llm_client
        self.db_path = db_path
        self.model = model
        self.temperature = temperature
    
    async def plan(
        self,
        user_query: str,
        context: ConversationContext
    ) -> ExecutionPlan:
        """
        生成执行计划
        
        核心流程:
        1. 获取可用工具列表
        2. 获取对话上下文
        3. 构造 LLM Prompt
        4. 调用 LLM 生成计划
        5. 解析并验证计划
        
        Args:
            user_query: 用户自然语言输入
            context: 对话上下文
            
        Returns:
            ExecutionPlan: 生成的执行计划
        """
        logger.info(f"开始规划任务: {user_query[:100]}")
        
        # 1. 获取已启用的工具
        available_tools = self._get_available_tools()
        logger.debug(f"可用工具数量: {len(available_tools)}")
        
        # 2. 构造 Prompt
        prompt = self._build_planning_prompt(
            user_query=user_query,
            available_tools=available_tools,
            context=context
        )
        
        # 3. 调用 LLM
        try:
            response = await self._call_llm(prompt)
            logger.debug(f"LLM 响应: {response[:200]}...")
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise RuntimeError(f"LLM 调用失败: {e}")
        
        # 4. 解析响应
        try:
            plan_data = json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}\n响应内容: {response}")
            raise ValueError(f"LLM 返回的 JSON 格式错误: {e}")
        
        # 5. 验证并构建计划
        plan = self._validate_and_build_plan(plan_data, user_query)
        
        logger.info(f"计划生成成功: {plan.plan_id}, 步骤数: {len(plan.steps)}")
        return plan
    
    def _build_planning_prompt(
        self,
        user_query: str,
        available_tools: List[Dict[str, Any]],
        context: ConversationContext
    ) -> str:
        """
        构造规划 Prompt
        
        Args:
            user_query: 用户请求
            available_tools: 可用工具列表
            context: 对话上下文
            
        Returns:
            完整的 Prompt 字符串
        """
        # 工具列表描述
        tools_desc = "\n".join([
            f"- **{tool['id']}** ({tool['name']}): {tool.get('description', '无描述')}\n"
            f"  风险级别: {tool['risk_level']}\n"
            f"  参数: {json.dumps(tool.get('args_schema', {}), ensure_ascii=False, indent=4)}"
            for tool in available_tools
        ])
        
        # 上下文摘要
        context_summary = context.get_context_summary()
        
        return f"""你是一个任务规划专家。你的职责是将用户的自然语言请求转换为可执行的步骤序列。

用户请求: {user_query}

当前上下文:
{context_summary}

可用工具列表:
{tools_desc}

请生成执行计划（严格按照以下 JSON 格式）:
{{
  "task_type": "code_search|code_modify|file_operation|git_operation|test_execution|deployment|custom",
  "steps": [
    {{
      "step_id": "step_1",
      "tool_id": "工具ID（必须是上面列表中的）",
      "tool_name": "工具名称",
      "args": {{"参数名": "参数值"}},
      "reason": "为什么需要这一步",
      "depends_on": [],
      "retry_on_fail": false,
      "timeout_seconds": 60,
      "on_fail": "stop"
    }}
  ],
  "estimated_duration": 预计总时间（秒）
}}

规划要求:
1. 步骤要具体、可执行、原子化
2. 只使用提供的工具列表中的工具ID
3. 高风险操作（write/exec_high）需要明确说明原因
4. 合理设置步骤依赖关系（depends_on 是步骤ID数组）
5. 参数必须符合工具的 args_schema
6. step_id 格式为 step_1, step_2, step_3...
7. on_fail 可选值: stop（停止）, continue（继续）, rollback（回滚）

注意: 只返回 JSON，不要包含任何其他文字说明！
"""
    
    async def _call_llm(self, prompt: str) -> str:
        """
        调用 LLM
        
        Args:
            prompt: 提示词
            
        Returns:
            LLM 响应内容
        """
        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": prompt},
        ]

        if not self.llm:
            raise RuntimeError("未配置 LLM 客户端")

        # 兼容不同客户端接口
        if hasattr(self.llm, "chat_completion"):
            response = await self.llm.chat_completion(
                messages=messages,
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            return extract_content(response)

        if hasattr(self.llm, "chat"):
            response = await self.llm.chat(
                messages=messages,
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            return extract_content(response)

        raise RuntimeError("LLM 客户端缺少 chat_completion/chat 方法")
    
    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return """你是一个任务规划专家。你的职责是：
1. 理解用户的自然语言请求
2. 将其拆解为可执行的步骤
3. 为每个步骤选择合适的工具
4. 确保步骤之间的依赖关系正确

重要原则:
- 只使用提供的工具列表中的工具
- 高风险操作必须明确标注
- 步骤要简洁、原子化
- 考虑错误处理和回退方案
- 返回格式必须是有效的 JSON
"""
    
    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """
        从数据库获取可用工具
        
        Returns:
            工具列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT id, name, description, risk_level, args_schema_json, executor, command_json
                FROM tools
                WHERE is_enabled = 1
                ORDER BY name
            """)
            
            tools = []
            for row in cursor.fetchall():
                tool_id, name, description, risk_level, args_schema_json, executor, command_json = row
                if executor not in {"host", "docker"}:
                    continue
                try:
                    parsed_command = json.loads(command_json) if command_json else []
                except Exception:
                    continue
                if not isinstance(parsed_command, list) or not parsed_command:
                    continue
                
                tools.append({
                    "id": tool_id,
                    "name": name,
                    "description": description or f"{name} 工具",
                    "risk_level": risk_level,
                    "args_schema": json.loads(args_schema_json) if args_schema_json else {}
                })
            
            return tools
        
        finally:
            conn.close()
    
    def _validate_and_build_plan(
        self,
        plan_data: Dict[str, Any],
        user_query: str
    ) -> ExecutionPlan:
        """
        验证并构建执行计划
        
        Args:
            plan_data: LLM 返回的计划数据
            user_query: 原始用户请求
            
        Returns:
            ExecutionPlan: 验证后的执行计划
        """
        # 验证必需字段
        if "steps" not in plan_data or not plan_data["steps"]:
            raise ValueError("计划必须包含至少一个步骤")
        
        # 验证任务类型
        task_type_str = plan_data.get("task_type", "custom")
        try:
            task_type = TaskType(task_type_str)
        except ValueError:
            logger.warning(f"未知任务类型: {task_type_str}，使用 custom")
            task_type = TaskType.CUSTOM
        
        # 验证步骤
        steps = []
        for i, step_data in enumerate(plan_data["steps"]):
            # 验证工具是否存在
            tool_id = step_data.get("tool_id")
            if not tool_id:
                raise ValueError(f"步骤 {i+1} 缺少 tool_id")
            
            if not self._tool_exists(tool_id):
                raise ValueError(f"工具不存在或未启用: {tool_id}")
            
            # 构建步骤
            try:
                step = PlanStep(
                    step_id=step_data.get("step_id", f"step_{i+1}"),
                    tool_id=tool_id,
                    tool_name=step_data.get("tool_name", tool_id),
                    args=step_data.get("args", {}),
                    reason=step_data.get("reason", "执行任务"),
                    depends_on=step_data.get("depends_on", []),
                    retry_on_fail=step_data.get("retry_on_fail", False),
                    timeout_seconds=step_data.get("timeout_seconds", 60),
                    on_fail=step_data.get("on_fail", "stop")
                )
                steps.append(step)
            except Exception as e:
                raise ValueError(f"步骤 {i+1} 验证失败: {e}")
        
        # 构建执行计划
        return ExecutionPlan(
            plan_id=str(uuid.uuid4()),
            user_query=user_query,
            task_type=task_type,
            steps=steps,
            estimated_duration=plan_data.get("estimated_duration", sum(s.timeout_seconds for s in steps)),
            created_at=datetime.utcnow().isoformat()
        )
    
    def _tool_exists(self, tool_id: str) -> bool:
        """
        检查工具是否存在且已启用
        
        Args:
            tool_id: 工具ID
            
        Returns:
            bool: 是否存在
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM tools WHERE id = ? AND is_enabled = 1",
                (tool_id,)
            )
            count = cursor.fetchone()[0]
            return count > 0
        
        finally:
            conn.close()
