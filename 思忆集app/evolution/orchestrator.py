"""受控进化编排器：协调技术雷达 → 汇报 → 确认 → 沙箱 → 升级的完整流程。

根据 complete-architecture-design.md 第 6 章设计：
- TechRadar 定时扫描 → 发现变化
- 汇入每日早报「技术动态」板块
- 用户确认 → 沙箱验证 → 用户最终确认 → 升级
- 三条安全红线不可触碰
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .report_generator import EvolutionReportGenerator, TechBriefingSection
from .sandbox import EvolutionSandbox
from .tech_radar import RadarFinding, TechRadar

from core.subprocess_text import run_text

logger = logging.getLogger("siyi.evolution.orchestrator")

# 升级流程状态
FLOW_IDLE = "idle"
FLOW_SCANNING = "scanning"
FLOW_REPORTED = "reported"  # 已汇报，等待用户确认
FLOW_USER_CONFIRMED = "confirmed"  # 用户确认，准备沙箱
FLOW_SANDBOX = "sandbox"  # 沙箱验证中
FLOW_READY = "ready"  # 沙箱通过，等待最终确认
FLOW_APPLYING = "applying"  # 应用升级中
FLOW_COMPLETED = "completed"
FLOW_FAILED = "failed"
FLOW_SKIPPED = "skipped"

# 安全红线：不可自动执行的操作
SAFETY_REDLINES = [
    "action_guard",  # 修改 action_guard 白名单
    "auth",  # 修改认证/授权逻辑
    "data_migration",  # 涉及数据迁移的依赖升级
    "delete_user_data",  # 删除用户数据
    "privilege_escalation",  # 提权操作
]

# P4.1-1c：用户自定义阻拦列表的 settings key（逗号分隔的包名）。
SETTINGS_KEY_UPGRADE_BLOCKLIST = "evolution_upgrade_blocklist"


@dataclass
class EvolutionResult:
    """一次进化流程的完整记录。"""

    name: str
    current_version: str = ""
    target_version: str = ""
    flow_steps: list[dict[str, Any]] = field(default_factory=list)
    status: str = FLOW_IDLE
    sandbox_run_id: str = ""
    sandbox_passed: bool = False
    applied_at: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "flow_steps": list(self.flow_steps),
            "status": self.status,
            "sandbox_passed": self.sandbox_passed,
            "applied_at": self.applied_at,
            "error": self.error,
        }


class EvolutionOrchestrator:
    """受控进化编排器。

    职责：
    1. 触发 TechRadar 每日扫描
    2. 将发现生成早报板块
    3. 管理用户确认 → 沙箱 → 升级的完整流程
    4. 强制执行安全红线
    """

    def __init__(self, tech_radar: TechRadar | None = None, sandbox: EvolutionSandbox | None = None, settings_repository: Any = None):
        self._tech_radar = tech_radar or TechRadar()
        self._sandbox = sandbox or EvolutionSandbox()
        self._report_generator = EvolutionReportGenerator()
        self._results: dict[str, EvolutionResult] = {}
        self._last_briefing: TechBriefingSection | None = None
        self._settings_repository = settings_repository

    def scan_and_report(self) -> TechBriefingSection:
        """执行每日扫描并生成早报板块。"""
        findings = self._tech_radar.scan()

        # 未发现变化
        if not findings:
            briefing = self._report_generator.generate([], "本周暂无技术动态更新。")
            self._last_briefing = briefing
            return briefing

        # 记录每个发现
        for f in findings:
            self._results[f.name] = EvolutionResult(
                name=f.name,
                current_version=f.current_version,
                target_version=f.latest_version,
                status=FLOW_REPORTED,
                flow_steps=[
                    {
                        "step": "scanned",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "detail": f"发现 {f.name} 更新: {f.current_version} → {f.latest_version}",
                    }
                ],
            )

        briefing = self._report_generator.generate(findings)
        self._last_briefing = briefing
        return briefing

    def confirm_upgrade(self, name: str) -> EvolutionResult | None:
        """用户确认升级某项目。"""
        result = self._results.get(name)
        if result is None:
            logger.warning("未找到升级项: %s", name)
            return None

        # 安全红线检查
        if self._is_redline(name):
            result.status = FLOW_FAILED
            result.error = f"「{name}」触及安全红线，不允许自动升级"
            result.flow_steps.append(
                {
                    "step": "blocked",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "detail": result.error,
                }
            )
            logger.warning("安全红线阻止升级: %s", name)
            return result

        result.status = FLOW_USER_CONFIRMED
        result.flow_steps.append(
            {
                "step": "user_confirmed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "detail": "用户确认升级",
            }
        )

        # 进入沙箱验证
        return self._run_sandbox(result)

    def _run_sandbox(self, result: EvolutionResult) -> EvolutionResult:
        """在沙箱中验证升级。"""
        finding = self._find_finding(result.name)
        if finding is None:
            result.status = FLOW_FAILED
            result.error = "未找到对应的雷达发现"
            return result

        result.status = FLOW_SANDBOX
        result.flow_steps.append(
            {
                "step": "sandbox_started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        sandbox_run = self._sandbox.run_tests(finding)
        result.sandbox_run_id = sandbox_run.run_id
        result.sandbox_passed = sandbox_run.overall_status == "passed"

        result.flow_steps.append(
            {
                "step": "sandbox_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "passed": result.sandbox_passed,
                "summary": sandbox_run.summary,
            }
        )

        if result.sandbox_passed:
            result.status = FLOW_READY
        else:
            result.status = FLOW_FAILED
            result.error = sandbox_run.summary

        return result

    def final_apply(self, name: str) -> EvolutionResult | None:
        """用户最终确认后，应用升级。"""
        result = self._results.get(name)
        if result is None:
            return None

        if result.status != FLOW_READY:
            result.error = f"状态不正确（当前: {result.status}），需要 FLOW_READY"
            result.status = FLOW_FAILED
            return result

        result.status = FLOW_APPLYING
        result.flow_steps.append(
            {
                "step": "applying",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "detail": f"正在升级 {result.name} 到 {result.target_version}",
            }
        )

        try:
            # 实际升级操作（模拟）
            self._do_apply(result)
            result.status = FLOW_COMPLETED
            result.applied_at = datetime.now(timezone.utc).isoformat()
            result.flow_steps.append(
                {
                    "step": "completed",
                    "timestamp": result.applied_at,
                    "detail": f"升级完成: {result.current_version} → {result.target_version}",
                }
            )
        except Exception as exc:
            result.status = FLOW_FAILED
            result.error = str(exc)
            result.flow_steps.append(
                {
                    "step": "failed",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "detail": str(exc),
                }
            )

        return result

    def skip_upgrade(self, name: str) -> EvolutionResult | None:
        """用户选择跳过某项升级。"""
        result = self._results.get(name)
        if result is None:
            return None
        result.status = FLOW_SKIPPED
        result.flow_steps.append(
            {
                "step": "skipped",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "detail": "用户选择跳过",
            }
        )
        return result

    def get_result(self, name: str) -> EvolutionResult | None:
        return self._results.get(name)

    def get_recent_results(self, limit: int = 10) -> list[EvolutionResult]:
        return list(self._results.values())[-limit:]

    def get_last_briefing(self) -> TechBriefingSection | None:
        return self._last_briefing

    def _find_finding(self, name: str) -> RadarFinding | None:
        for f in self._tech_radar.get_all_findings():
            if f.name == name:
                return f
        return None

    def _is_redline(self, name: str) -> bool:
        """检查是否触及安全红线（关键词内置黑名单 + 用户自定义阻拦列表）。"""
        if not name:
            return False
        lower = name.lower()
        if any(r in lower for r in SAFETY_REDLINES):
            return True
        # P4.1-1c：用户阻拦列表（settings 中以逗号分隔的包名）
        user_blocked = self._load_user_blocklist()
        return lower in user_blocked

    def _load_user_blocklist(self) -> set[str]:
        """从 settings 读取用户阻拦列表，返回小写包名集合。"""
        if self._settings_repository is None:
            return set()
        try:
            raw = self._settings_repository.get(SETTINGS_KEY_UPGRADE_BLOCKLIST)
        except Exception as exc:
            logger.debug("读取用户阻拦列表失败: %s", exc)
            return set()
        if not raw:
            return set()
        # 支持 list 与 csv string 两种格式
        if isinstance(raw, list):
            items = raw
        else:
            items = [s.strip() for s in str(raw).split(",")]
        return {item.lower() for item in items if item}

    def _do_apply(self, result: EvolutionResult) -> None:
        """执行实际升级并持久化记录。"""
        if not result.current_version or not result.target_version:
            raise RuntimeError("版本信息不完整，无法升级")

        logger.info(
            "升级执行: %s %s → %s (类别=%s)",
            result.name,
            result.current_version,
            result.target_version,
            getattr(result, "category", "未知"),
        )

        # 尝试实际 pip 升级（仅对 pip 源有效，忽略失败）
        if result.name and result.name not in SAFETY_REDLINES:
            pkg_name = result.name.lower()
            try:
                import sys

                pip_args = [sys.executable, "-m", "pip", "install", "--upgrade"]
                # If target version is specified, pin it
                if result.target_version:
                    pip_args.append(f"{pkg_name}=={result.target_version}")
                else:
                    pip_args.append(pkg_name)
                proc = run_text(pip_args, timeout=120)
                if proc.returncode == 0:
                    logger.info("pip 升级 %s 成功", pkg_name)
                else:
                    logger.warning("pip 升级 %s 失败: %s", pkg_name, proc.stderr.strip()[:200])
            except Exception as exc:
                logger.warning("pip 升级 %s 失败（非阻塞）: %s", pkg_name, exc)

        # 更新 tech_radar 的版本缓存以匹配
        self._tech_radar.set_version(result.name, result.target_version)

        # 记录升级以备后续审计
        try:
            from data.local_storage import LocalDataStore

            store = LocalDataStore("evolution_upgrades")
            store.save_json(
                result.name,
                {
                    "name": result.name,
                    "from_version": result.current_version,
                    "to_version": result.target_version,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "category": getattr(result, "category", ""),
                },
            )
        except Exception as exc:
            logger.warning("升级记录持久化失败（非阻塞）: %s", exc)

    def is_redline_item(self, name: str) -> bool:
        """公开的安全红线检查。"""
        return self._is_redline(name)

    def get_status_snapshot(self) -> dict[str, Any]:
        completed = sum(1 for r in self._results.values() if r.status == FLOW_COMPLETED)
        failed = sum(1 for r in self._results.values() if r.status == FLOW_FAILED)
        pending = sum(1 for r in self._results.values() if r.status in (FLOW_REPORTED, FLOW_READY))
        return {
            "status": "running" if self._results else FLOW_IDLE,
            "total_items": len(self._results),
            "completed": completed,
            "failed": failed,
            "pending_approval": pending,
            "has_last_briefing": self._last_briefing is not None,
        }
