"""
Agent 执行器

执行规划好的任务步骤，处理审批、重试、错误等
"""

import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime
import inspect

from .models import (
    ExecutionPlan,
    ExecutionResult,
    StepResult,
    StepStatus,
    PlanStep
)

logger = logging.getLogger(__name__)


class AgentExecutor:
    """执行器 - 执行规划好的步骤"""
    
    def __init__(self, api_client, approval_handler=None):
        """
        初始化执行器
        
        Args:
            api_client: API 客户端（用于调用工具）
            approval_handler: 审批处理器（可选）
        """
        self.api = api_client
        self.approval = approval_handler
    
    async def execute_plan(
        self,
        plan: ExecutionPlan,
        user_id: str
    ) -> ExecutionResult:
        """
        执行计划
        
        特性:
        - 串行/并行执行（根据依赖关系）
        - 处理审批流程
        - 自动重试
        - 失败回滚
        
        Args:
            plan: 执行计划
            user_id: 用户ID
            
        Returns:
            ExecutionResult: 执行结果
        """
        logger.info(f"开始执行计划: {plan.plan_id}, 步骤数: {len(plan.steps)}")
        start_time = datetime.utcnow()
        
        step_results: Dict[str, StepResult] = {}
        completed_steps: List[tuple[PlanStep, StepResult]] = []
        
        try:
            # 按顺序执行步骤（简化版，完整版需实现 DAG 并行执行）
            for step in plan.steps:
                logger.info(f"执行步骤: {step.step_id} - {step.tool_name}")
                
                # 检查依赖是否满足
                if not self._dependencies_satisfied(step, step_results):
                    logger.warning(f"步骤 {step.step_id} 依赖未满足，跳过")
                    step_results[step.step_id] = StepResult(
                        step_id=step.step_id,
                        status=StepStatus.SKIPPED,
                        error="依赖步骤未完成"
                    )
                    continue
                
                # 执行步骤
                result = await self._execute_step(step, user_id)
                step_results[step.step_id] = result
                if result.status == StepStatus.COMPLETED:
                    completed_steps.append((step, result))
                
                # 失败处理
                if result.status == StepStatus.FAILED:
                    logger.error(f"步骤失败: {step.step_id}")
                    
                    if step.retry_on_fail:
                        logger.info(f"重试步骤: {step.step_id}")
                        await asyncio.sleep(2)  # 延迟2秒
                        result = await self._execute_step(step, user_id)
                        step_results[step.step_id] = result
                    
                    if result.status == StepStatus.FAILED:
                        if step.on_fail == "stop":
                            logger.info("失败策略为 stop，终止执行")
                            break
                        elif step.on_fail == "rollback":
                            logger.info("失败策略为 rollback，执行回滚")
                            await self._rollback(completed_steps, user_id=user_id)
                            break
                        # continue: 继续执行下一步
            
            # 生成总结
            end_time = datetime.utcnow()
            total_duration = (end_time - start_time).total_seconds()
            
            summary = self._generate_summary(plan, list(step_results.values()))
            overall_status = self._get_overall_status(step_results)
            
            result = ExecutionResult(
                plan_id=plan.plan_id,
                status=overall_status,
                step_results=list(step_results.values()),
                summary=summary,
                completed_at=end_time.isoformat(),
                total_duration=total_duration
            )
            
            logger.info(
                f"计划执行完成: {plan.plan_id}, "
                f"状态: {overall_status}, "
                f"耗时: {total_duration:.2f}s"
            )
            
            return result
        
        except Exception as e:
            logger.exception(f"计划执行异常: {plan.plan_id}")
            return ExecutionResult(
                plan_id=plan.plan_id,
                status="failed",
                step_results=list(step_results.values()),
                summary=f"执行异常: {str(e)}",
                completed_at=datetime.utcnow().isoformat(),
                total_duration=(datetime.utcnow() - start_time).total_seconds()
            )
    
    async def _execute_step(
        self,
        step: PlanStep,
        user_id: str
    ) -> StepResult:
        """
        执行单个步骤
        
        Args:
            step: 步骤定义
            user_id: 用户ID
            
        Returns:
            StepResult: 步骤结果
        """
        start_time = datetime.utcnow()
        
        try:
            # 创建任务（调用现有的 runs API）
            logger.debug(f"调用工具: {step.tool_id}, 参数: {step.args}")

            run_response = await self.api.create_run(
                tool_id=step.tool_id,
                args=step.args,
                user_id=user_id,
            )
            
            # 检查是否需要审批
            if run_response.get("status") == "pending_approval":
                logger.info(f"步骤 {step.step_id} 需要审批")
                
                if self.approval:
                    # 等待审批
                    approved = await self.approval.wait_for_approval(
                        run_response["approval_id"],
                        timeout=3600  # 1小时超时
                    )
                    
                    if not approved:
                        return StepResult(
                            step_id=step.step_id,
                            status=StepStatus.BLOCKED,
                            error="审批被拒绝或超时",
                            started_at=start_time.isoformat(),
                            completed_at=datetime.utcnow().isoformat()
                        )
                else:
                    # 无审批处理器，标记为阻塞
                    return StepResult(
                        step_id=step.step_id,
                        status=StepStatus.BLOCKED,
                        error="需要审批：请先批准后再继续",
                        started_at=start_time.isoformat(),
                        completed_at=datetime.utcnow().isoformat(),
                        run_id=run_response.get("run_id"),
                        approval_id=run_response.get("approval_id"),
                    )
            
            # 等待执行完成
            final_status = await self._wait_for_completion(
                run_response["run_id"],
                timeout=step.timeout_seconds
            )
            
            end_time = datetime.utcnow()
            execution_time = (end_time - start_time).total_seconds()
            
            # 构建结果
            if final_status["status"] == "succeeded":
                return StepResult(
                    step_id=step.step_id,
                    status=StepStatus.COMPLETED,
                    output=final_status.get("output", "执行成功"),
                    run_id=run_response["run_id"],
                    execution_time=execution_time,
                    started_at=start_time.isoformat(),
                    completed_at=end_time.isoformat()
                )
            else:
                return StepResult(
                    step_id=step.step_id,
                    status=StepStatus.FAILED,
                    error=final_status.get("error", "执行失败"),
                    run_id=run_response["run_id"],
                    execution_time=execution_time,
                    started_at=start_time.isoformat(),
                    completed_at=end_time.isoformat()
                )
        
        except asyncio.TimeoutError:
            logger.error(f"步骤超时: {step.step_id}")
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                error=f"执行超时（{step.timeout_seconds}秒）",
                started_at=start_time.isoformat(),
                completed_at=datetime.utcnow().isoformat()
            )
        
        except Exception as e:
            logger.exception(f"执行步骤异常: {step.step_id}")
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                error=f"执行异常: {str(e)}",
                started_at=start_time.isoformat(),
                completed_at=datetime.utcnow().isoformat()
            )
    
    async def _wait_for_completion(
        self,
        run_id: str,
        timeout: int
    ) -> Dict:
        """
        轮询等待任务完成
        
        Args:
            run_id: 任务ID
            timeout: 超时时间（秒）
            
        Returns:
            任务最终状态
        """
        elapsed = 0
        interval = 2
        while elapsed < timeout:
            status = await self.api.get_run_status(run_id)
            if status.get("status") in ["succeeded", "failed", "denied", "blocked", "pending_approval"]:
                return status
            await asyncio.sleep(interval)
            elapsed += interval

        raise asyncio.TimeoutError(f"任务执行超时: {run_id}")
    
    def _dependencies_satisfied(
        self,
        step: PlanStep,
        results: Dict[str, StepResult]
    ) -> bool:
        """
        检查依赖是否满足
        
        Args:
            step: 当前步骤
            results: 已执行步骤的结果
            
        Returns:
            bool: 依赖是否满足
        """
        for dep_id in step.depends_on:
            if dep_id not in results:
                logger.warning(f"依赖步骤未执行: {dep_id}")
                return False
            
            if results[dep_id].status != StepStatus.COMPLETED:
                logger.warning(f"依赖步骤未成功: {dep_id}")
                return False
        
        return True
    
    async def _rollback(self, completed_steps: List[tuple[PlanStep, StepResult]], user_id: str) -> None:
        """
        回滚已执行的步骤
        
        Args:
            completed_steps: 已执行成功的步骤及结果
            user_id: 当前用户 ID
        """
        logger.warning("执行回滚操作")

        for step, result in reversed(completed_steps):
            rollback_spec = None
            if isinstance(step.args, dict):
                rollback_spec = step.args.get("_rollback")

            if isinstance(rollback_spec, dict) and rollback_spec.get("tool_id"):
                tool_id = str(rollback_spec.get("tool_id") or "").strip()
                rollback_args = rollback_spec.get("args") if isinstance(rollback_spec.get("args"), dict) else {}
                logger.info("执行补偿步骤: step=%s rollback_tool=%s", step.step_id, tool_id)
                try:
                    run_response = await self.api.create_run(
                        tool_id=tool_id,
                        args=rollback_args,
                        user_id=user_id,
                    )
                    rollback_run_id = run_response.get("run_id")
                    if rollback_run_id:
                        await self._wait_for_completion(
                            rollback_run_id,
                            timeout=max(30, min(step.timeout_seconds, 600)),
                        )
                    continue
                except Exception as exc:
                    logger.exception("补偿步骤执行失败: step=%s error=%s", step.step_id, exc)

            if result.run_id and hasattr(self.api, "cancel_run"):
                logger.info("尝试取消运行中的任务: step=%s run_id=%s", step.step_id, result.run_id)
                try:
                    maybe_coro = self.api.cancel_run(result.run_id)
                    if inspect.isawaitable(maybe_coro):
                        await maybe_coro
                except Exception as exc:
                    logger.warning("取消任务失败: step=%s run_id=%s error=%s", step.step_id, result.run_id, exc)
                continue

            logger.info("步骤无可执行回滚动作，跳过: step=%s", step.step_id)
    
    def _generate_summary(
        self,
        plan: ExecutionPlan,
        results: List[StepResult]
    ) -> str:
        """
        生成执行摘要
        
        Args:
            plan: 执行计划
            results: 步骤结果列表
            
        Returns:
            格式化的摘要文本
        """
        total = len(results)
        completed = sum(1 for r in results if r.status == StepStatus.COMPLETED)
        failed = sum(1 for r in results if r.status == StepStatus.FAILED)
        blocked = sum(1 for r in results if r.status == StepStatus.BLOCKED)
        
        summary_parts = [
            f"任务执行完成。"
        ]
        
        if completed == total:
            summary_parts.append(f"✅ 全部 {total} 个步骤成功完成。")
        else:
            summary_parts.append(f"📊 完成 {completed}/{total} 个步骤")
            if failed > 0:
                summary_parts.append(f"❌ {failed} 个失败")
            if blocked > 0:
                summary_parts.append(f"🔒 {blocked} 个被阻塞")
        
        # 添加关键输出
        key_outputs = []
        for i, result in enumerate(results, 1):
            if result.output and result.status == StepStatus.COMPLETED:
                output_preview = result.output[:100]
                if len(result.output) > 100:
                    output_preview += "..."
                key_outputs.append(f"  {i}. {result.step_id}: {output_preview}")
        
        if key_outputs:
            summary_parts.append("\n关键结果:")
            summary_parts.extend(key_outputs)
        
        # 添加错误信息
        errors = []
        for result in results:
            if result.error:
                errors.append(f"  - {result.step_id}: {result.error}")
        
        if errors:
            summary_parts.append("\n错误信息:")
            summary_parts.extend(errors)
        
        return "\n".join(summary_parts)
    
    def _get_overall_status(self, results: Dict[str, StepResult]) -> str:
        """
        获取整体状态
        
        Args:
            results: 步骤结果字典
            
        Returns:
            str: success|partial|failed
        """
        if not results:
            return "failed"
        
        statuses = [r.status for r in results.values()]
        
        if all(s == StepStatus.COMPLETED for s in statuses):
            return "success"
        elif any(s == StepStatus.FAILED for s in statuses):
            if any(s == StepStatus.COMPLETED for s in statuses):
                return "partial"
            else:
                return "failed"
        elif any(s == StepStatus.BLOCKED for s in statuses):
            return "blocked"
        else:
            return "partial"
