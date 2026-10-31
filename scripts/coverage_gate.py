"""稳定核心覆盖率门禁。

默认运行一组当前可稳定通过的关键后端测试，并对核心源码目录收集覆盖率。
这样可以在全量测试仍有既存坏例时，先建立可执行的 coverage 基线与门禁。

用法:
    python scripts/coverage_gate.py
    python scripts/coverage_gate.py --threshold 20
    python scripts/coverage_gate.py --full
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# [stdio-utf8-guard] 中文 Windows 控制台(cp1252)下 print 中文会 UnicodeEncodeError 并退出 1，
# 导致 CI quality-gates 中断、后续步骤全部 skipped。详见 scripts/_stdio.py。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = WORKSPACE_ROOT / "思忆集app"
TESTS_DIR = APP_DIR / "tests"
PYTHON = sys.executable

# 覆盖率门禁：实测总覆盖 25.56%（2026-08-26 稳定核心复核，1012 passed）；
# 阈值从 22.0 上调至 25.0（略低于实测，留 ~0.56pt 余量，CI 仍绿；
# 属棘轮机制 T0.1/T4.1，随测试补充逐步逼近 80% P1 目标）。
# 本次抬升得益于补的认证(auth/token)链路（test_handlers_api_keys_rpc）
# 与审批(R4 流)决议链路（test_r4_approval_resolution）行为测试。
DEFAULT_THRESHOLD = 25.0
DEFAULT_XML = APP_DIR / "coverage.xml"

CORE_TEST_FILES = [
    "tests/test_cloud_sync_client.py",
    "tests/test_sync_bridge.py",
    "tests/test_sync_handlers_integration.py",
    "tests/test_event_bridge.py",
    "tests/test_ws_server.py",
    "tests/test_system_health.py",
    "tests/test_tailscale_status.py",
    "tests/test_memory_manager.py",
    "tests/test_llm_cache.py",
    "tests/test_circuit_breaker.py",
    "tests/test_rate_limiter.py",
    "tests/test_credential_store.py",
    "tests/test_web_fetch.py",
    # P2-23 扩列：在原 stable-core 基础上补 ai/app_runtime/backend 关键测试，
    # 使阈值 18% 在 CI 上可稳定达到（不引入已知 collection 坏例）
    "tests/test_fallback_client.py",
    "tests/test_judge_aware_client.py",
    "tests/test_p0_10_app_context_builder.py",
    "tests/test_p0_11_execution_router.py",
    "tests/test_provider_routing.py",
    "tests/test_ai_resilience.py",
    "tests/test_cost_tracker.py",
    "tests/test_llm_provider_topology.py",
    # 2026-08-07: 之前被 KNOWN_COLLECTION_BROKEN 忽略的 3 个测试已修复，纳入 stable-core
    "tests/test_handlers_transfer.py",
    # 2026-08-07: CronService 任务执行状态通知测试（S11 补齐）
    "tests/test_cron_notification.py",
    # 2026-08-23 (T4.1 棘轮推进): 阶段 5/6/7 新增行为测试纳入 stable-core，
    # 提升对 backend/handlers + services 的真实覆盖（此前实测 20.91% < 阈值 21 转红）
    "tests/test_handlers_confirmations.py",
    "tests/test_handlers_inbox_web.py",
    "tests/test_handlers_cascade_cleanup.py",
    "tests/test_monthly_letter.py",
    "tests/test_notes_related_memories.py",
    "tests/test_ai_usage_monthly_report.py",
    # T4.1: narrative handler 行为测试（叙事 RPC + xiaowen.quote）
    "tests/test_handlers_narrative.py",
    # T4.1(2026-08-23) 棘轮推进：纳入已验证稳定的测试，扩大 stable-core 覆盖面
    # （全部经本会话 215 passed 回归确认，覆盖 assistant 编排/级联清理/同步索引/审计压缩）
    "tests/test_action_guard.py",
    "tests/test_agent_gateway.py",
    "tests/test_append_to_list.py",
    "tests/test_assistant_autonomy.py",
    "tests/test_assistant_capability_router.py",
    "tests/test_assistant_judge.py",
    "tests/test_assistant_intent_router.py",
    "tests/test_assistant_privacy_classifier.py",
    "tests/test_assistant_execution.py",
    "tests/test_assistant_planner.py",
    "tests/test_assistant_runtime_guard.py",
    "tests/test_audit_trail_compaction.py",
    "tests/test_cloud_sync_reindex.py",
    # T4.1(2026-08-23) 第二批：本会话新增的行为测试（备份校验/行为归档/dedup 纯逻辑）
    "tests/test_info_collector_dedup.py",
    "tests/test_backup_restore_checksum.py",
    "tests/test_behavior_archive_scheduler.py",
    # T4.1(2026-08-23) 第三批：数据层测试（SQLite/local storage/usage/memory index）
    "tests/test_local_storage.py",
    "tests/test_p1_6_sqlite_store.py",
    "tests/test_usage_tracker.py",
    "tests/test_assistant_memory_index.py",
    "tests/test_storage_rollback.py",
    # T4.2/T4.3(2026-08-23)：并发契约 + Chroma 性能基线
    "tests/test_capability_router_concurrency.py",
    "tests/test_memory_chroma_baseline.py",
    # TASK-V2-03(2026-08-24) 棘轮推进：补 services 高行数 0% 模块覆盖，
    # 使阈值 21.0 → 22.0 在 CI 上可稳定达到（与本日新写 test_feedback_service 一道）
    "tests/test_cron_actions.py",
    "tests/test_cron_service.py",
    "tests/test_feedback_service.py",
    "tests/test_note_templates_service.py",
    # P0-6(2026-08-26): 删除类 handler 过 R4 sandbox 审批闸门的攻击链回归测试，
    # 防止「删除类=u0 审批」设计契约回退（未经审批的删除 dispatch 必须 pending 且不执行）。
    "tests/test_p0_6_delete_r4_gating.py",
    # 覆盖率棘轮 22→25(2026-08-26): 认证(auth/token) + 审批(R4 流) 链路行为测试，
    # 抬升 backend/handlers/_api_keys.py 与 backend/handlers/_sandbox.py 真实覆盖。
    "tests/test_handlers_api_keys_rpc.py",
    "tests/test_r4_approval_resolution.py",
]

KNOWN_COLLECTION_BROKEN = [
    # 列表保留为空，以备未来需要再次忽略测试时使用。
    # 2026-08-07: 3 个之前被忽略的坏测试已全部修复并通过：
    #   test_assistant_task_ui.py / test_handlers_transfer.py / test_smoke_quality_gate.py
    # 2026-08-23 (T4.1): 两个绑定已归档 PyQt6 `ui` 模块的死测试曾在 collection 阶段
    #   `ModuleNotFoundError: No module named 'ui'`。自 2026-08-26（批次5）起，
    #   以下三个死测试文件已直接删除（import 归档 ui.* / PyQt6，无维护价值）：
    #   - tests/test_smoke_quality_gate.py
    #   - tests/test_assistant_task_ui.py
    #   - tests/test_ui_import_smoke.py
    # 故此处不再需要 --ignore；保留空列表。
]

COVERAGE_TARGETS = [
    "ai",
    "app_runtime",
    "backend",
    "core",
    "services",
    "sync",
]


def _parse_coverage_from_xml(xml_path: Path) -> float | None:
    """从 coverage.xml 解析总覆盖率百分比（全量口径报告用，T6.3）。"""
    try:
        import xml.etree.ElementTree as ET

        if not xml_path.exists():
            return None
        root = ET.parse(xml_path).getroot()
        line_rate = float(root.attrib.get("line-rate", ""))
        # line-rate 是 0..1 比例；为与 --cov 的百分比对齐乘 100
        return line_rate * 100.0
    except Exception:
        return None


def _build_pytest_command(*, threshold: float, xml_output: Path, full: bool) -> list[str]:
    cmd = [PYTHON, "-m", "pytest", "-q"]
    # T4.1(2026-08-23): basetemp 固定到系统临时目录的唯一子目录（带时间戳），
    # 规避仓库内 .pytest-tmp-root 权限损坏 + 沙箱 safe-delete 对批量 tmp 清理的拦截
    # （此前门禁被静默架空：coverage.xml 永远停在旧值）。
    import tempfile as _tf
    import time as _time

    cmd.extend(["--basetemp", str(Path(_tf.gettempdir()) / f"siyi_cg_{int(_time.time())}")])
    if full:
        cmd.append(str(TESTS_DIR))
        for broken in KNOWN_COLLECTION_BROKEN:
            cmd.extend(["--ignore", broken])
    else:
        cmd.extend(CORE_TEST_FILES)

    for target in COVERAGE_TARGETS:
        cmd.append(f"--cov={target}")
    cmd.extend(
        [
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=xml:{xml_output}",
            f"--cov-fail-under={threshold}",
        ]
    )
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集稳定核心覆盖率门禁")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="最低覆盖率阈值，默认 17")
    parser.add_argument("--xml", default=str(DEFAULT_XML), help="coverage xml 输出路径")
    parser.add_argument("--full", action="store_true", help="运行尽可能完整的 tests 目录，但默认忽略已知收集坏例")
    parser.add_argument("--report-full", action="store_true", help="T6.3: 额外跑一次全量覆盖率并打印『全量口径』数字（不参与门禁，仅报告）")
    args = parser.parse_args()

    xml_output = Path(args.xml).expanduser().resolve()
    xml_output.parent.mkdir(parents=True, exist_ok=True)

    cmd = _build_pytest_command(
        threshold=args.threshold,
        xml_output=xml_output,
        full=args.full,
    )

    # T4.1(2026-08-23): coverage 数据文件放到系统 temp，规避仓库内 .coverage
    # 删除被沙箱/回收站防护拦截导致 pytest-cov erase 失败（门禁崩的又一根因）。
    import os as _os
    import tempfile as _tf

    _env = dict(_os.environ)
    _env["COVERAGE_FILE"] = str(Path(_tf.gettempdir()) / "siyi_coverage_gate_data")

    mode = "full-minus-broken" if args.full else "stable-core"
    print(f"[coverage-gate] mode={mode}")
    print(f"[coverage-gate] threshold={args.threshold:.1f}")
    print(f"[coverage-gate] xml={xml_output}")
    print(f"[coverage-gate] command: {' '.join(cmd)}")

    completed = subprocess.run(cmd, cwd=str(APP_DIR), check=False, env=_env)

    # T6.3: 全量口径报告（不阻塞门禁）。跑全部 tests 收集覆盖，仅打印数字，
    # 返回码不受全量结果影响 —— 与子集棘轮（--threshold）口径分开呈现。
    if args.report_full:
        full_xml = xml_output.parent / "coverage_full.xml"
        full_cmd = _build_pytest_command(threshold=0.0, xml_output=full_xml, full=True)
        print("[coverage-gate][report-full] running full-suite coverage (report only, non-blocking)...")
        _full = subprocess.run(full_cmd, cwd=str(APP_DIR), check=False, env=_env)
        full_pct = _parse_coverage_from_xml(full_xml)
        if full_pct is not None:
            print(f"[coverage-gate][report-full] FULL-SUITE coverage = {full_pct:.2f}% (report-only, NOT gated)")
        else:
            print("[coverage-gate][report-full] could not parse full coverage xml")

    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
