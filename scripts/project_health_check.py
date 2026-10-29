#!/usr/bin/env python3
"""项目健康检查 — 文档 vs 代码对齐检测、集成状态扫描、差距报告。

用法:
    python scripts/project_health_check.py              # 默认模式
    python scripts/project_health_check.py --json       # JSON 输出（供小问消费）
    python scripts/project_health_check.py --analyze    # 输出 Claude 决策分析
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
APP_DIR = REPO_ROOT / "思忆集app"
BACKEND_DIR = REPO_ROOT / "automation_hub"  # 注意下划线（auth：连字符目录不存在，会导致后端能力误报缺失）

# ── 已知的功能模块清单（文档中的名称 → 代码中的入口） ──────────
# 每项代表一个"文档承诺了、应该检查是否已实现"的能力
KNOWN_CAPABILITIES: list[dict[str, Any]] = [
    # (模块名, 文档路径, 桌面端代码模式, 后端API路径模式/False表示应有但缺失, 是否需要后端)
    {"name": "MCP 工具管理", "doc": "README.md", "desktop": "mcp", "backend": "mcp",
     "note": "桌面端已实现，2026-05-03 已建后端路由 /mcp/*"},
    {"name": "知识库/RAG", "doc": "knowledge-base-rag-design.md", "desktop": "knowledge_base", "backend": "knowledge",
     "note": "2026-05-03 已建后端路由 /knowledge-base/*"},
    {"name": "Web Search", "doc": "README.md", "desktop": "web_search_service", "backend": "web_search",
     "note": "2026-05-03 已建后端路由 /web-search/*"},
    {"name": "Cron定时任务", "doc": "README.md", "desktop": "cron_service", "backend": "cron",
     "note": "2026-05-03 已建后端路由 /cron/*"},
    {"name": "插件系统", "doc": "plugin-sdk-spec.md", "desktop": "plugin_registry", "backend": "plugins",
     "note": "2026-05-03 已建后端路由 /plugins/*"},
    {"name": "进化系统", "doc": "README.md", "desktop": "evolution_", "backend": "evolution",
     "note": "2026-05-03 已建后端路由 /evolution/*"},
    {"name": "工作流引擎", "doc": "README.md", "desktop": "workflow_", "backend": "workflows",
     "note": "2026-05-03 已建后端路由 /workflows/*"},
    {"name": "浏览器桥接", "doc": "browser-extension-floating-assistant-status.md",
     "desktop": "browser_assistant_bridge", "backend": None, "note": "桌面端本地 HTTP 服务，不需要后端"},
    {"name": "跨设备同步", "doc": "cross-device-sync-spec.md", "desktop": "sync_services", "backend": "sync",
     "note": "已完成: 桌面↔后端↔Android"},
    {"name": "移动端API", "doc": "android-client-technical-design.md", "desktop": None, "backend": "mobile",
     "note": "已完成: /mobile/* + /sync/*"},
    {"name": "桌宠控制", "doc": "README.md", "desktop": "desktop_pet_controller", "backend": None,
     "note": "桌面本地 subprocess，不需要后端"},
    {"name": "Agent接口", "doc": "assistant-execution-framework.md", "desktop": "assistant_", "backend": "agent",
     "note": "已完成: /agent/ask, /agent/plan"},
    {"name": "审批系统", "doc": "THREE_LAWS.md", "desktop": None, "backend": "approvals",
     "note": "已完成: 后端 execution_authorizations.py 含审批流程"},
    {"name": "审计系统", "doc": "THREE_LAWS.md", "desktop": None, "backend": "audit",
     "note": "已完成: 后端 metrics.py 含审计日志"},
    {"name": "提案系统", "doc": "THREE_LAWS.md", "desktop": "proposal", "backend": "proposals",
     "note": "已完成: 桌面端 _proposal.py 含 LocalImprovementProposalRepository，后端待补 /proposals 路由"},
    {"name": "个人数据同步", "doc": "README.md", "desktop": "sync_services", "backend": "personal",
     "note": "已完成: notes/tasks/news/translations/wordbooks"},
    {"name": "Web UI 桥接", "doc": "README.md", "desktop": "web_bridge_service", "backend": None,
     "note": "QWebEngineView 已加载 web_dist/index.html，QWebChannel 桥接已配置"},
]


def find_py_files(dirpath: Path, pattern: str | None = None) -> list[Path]:
    """递归查找 Python 文件。"""
    if not dirpath.exists():
        return []
    result = []
    for f in dirpath.rglob("*.py"):
        # 跳过缓存、测试、虚拟环境
        rel = f.relative_to(dirpath)
        if any(part.startswith("__pycache__") or part == ".venv3.11" or part.startswith(".") for part in rel.parts):
            continue
        if pattern and pattern not in f.name:
            continue
        result.append(f)
    return result


def check_capability_in_code(dirpath: Path, pattern: str) -> bool:
    """检查某个能力是否在代码目录中有文件或引用。"""
    if not dirpath.exists():
        return False
    # 先找文件名匹配
    for f in dirpath.rglob("*.py"):
        rel = f.name
        if any(part.startswith(".") or part == "__pycache__" for part in f.relative_to(dirpath).parts):
            continue
        if pattern in rel:
            return True
    # 再搜内容引用
    try:
        for f in dirpath.rglob("*.py"):
            if any(part.startswith(".") or part == "__pycache__" for part in f.relative_to(dirpath).parts):
                continue
            content = f.read_text(encoding="utf-8", errors="ignore")
            if pattern in content:
                return True
    except Exception:
        pass
    return False


def check_doc_file(doc_path: str) -> bool:
    """检查文档文件是否存在。"""
    path = DOCS_DIR / doc_path
    if path.exists():
        return True
    # 也可能在根目录或 automation-hub/ 下
    if (REPO_ROOT / doc_path).exists():
        return True
    if (BACKEND_DIR / doc_path).exists():
        return True
    return False


def check_backend_route(backend_pattern: str | bool | None) -> bool | None:
    """检查后端路由文件中是否包含预期的 API 路径模式。

    返回:
        True  = 路由存在
        False = 路由不存在（应有但缺失）
        None  = 不要求后端（如桌宠、浏览器桥接等桌面本地功能）
    """
    if backend_pattern is None:
        return None  # 不要求后端
    if backend_pattern is False:
        return False  # 强制标记为"应有但缺失"
    routes_dir = BACKEND_DIR / "api" / "routes"
    if not routes_dir.exists():
        # 可能路由直接写在 api/ 下
        api_dir = BACKEND_DIR / "api"
        for f in api_dir.rglob("*.py"):
            content = f.read_text(encoding="utf-8", errors="ignore")
            if backend_pattern.replace(".", "/") in content or backend_pattern in content:
                return True
        return False
    for f in routes_dir.rglob("*.py"):
        content = f.read_text(encoding="utf-8", errors="ignore")
        if backend_pattern.replace(".", "/") in content or backend_pattern in content:
            return True
    return False


def scan_capability_alignment() -> list[dict[str, Any]]:
    """扫描功能模块在文档/代码/后端三处的对齐状态。"""
    results = []
    for cap in KNOWN_CAPABILITIES:
        doc_ok = check_doc_file(cap["doc"])
        desktop_ok = check_capability_in_code(APP_DIR, cap["desktop"]) if cap["desktop"] else None
        backend_ok = check_backend_route(cap["backend"])

        gaps = []
        if not doc_ok:
            gaps.append("文档缺失")
        if cap["desktop"] and desktop_ok is False:
            gaps.append("桌面端代码未找到")
        if backend_ok is False:
            gaps.append("应有后端API但缺失")
        if cap["desktop"] and not desktop_ok and cap["backend"] and backend_ok is True:
            gaps.append("仅后端有实现，桌面端无入口")

        if not gaps:
            status = "OK"
        elif any("缺失" in g for g in gaps):
            status = "MISSING"
        elif any("仅桌面" in g for g in gaps):
            status = "DESKTOP_ONLY"
        else:
            status = "PARTIAL"

        results.append({
            "name": cap["name"],
            "status": status,
            "doc": "有" if doc_ok else "无",
            "desktop": "有" if desktop_ok else ("无" if desktop_ok is False else "不适用"),
            "backend": "有" if backend_ok else ("应有但无" if backend_ok is False else "不适用"),
            "gaps": gaps,
            "note": cap.get("note", ""),
        })
    return results


def scan_doc_issues() -> list[dict[str, Any]]:
    """扫描文档体系中的已知问题。"""
    issues = []

    # 1. 检查 document-map.md 覆盖是否完整
    map_file = DOCS_DIR / "document-map.md"
    if map_file.exists():
        content = map_file.read_text(encoding="utf-8", errors="ignore")
        referenced = set(re.findall(r'\]\(([^)]+\.md)\)', content))
        # 只统计 docs/ 下的引用
        referenced = {r for r in referenced if r.startswith("docs/") or "/" not in r}
        referenced = {r for r in referenced if not r.startswith("http")}

        # docs/ 根目录所有 .md
        actual_files = set()
        for f in DOCS_DIR.glob("*.md"):
            if f.name != "document-map.md":
                actual_files.add(f.name)

        not_mapped = actual_files - referenced
        if not_mapped:
            issues.append({
                "type": "文档地图遗漏",
                "detail": f"document-map.md 未收录 {len(not_mapped)} 份文档: {', '.join(sorted(not_mapped)[:10])}" + ("..." if len(not_mapped) > 10 else ""),
                "severity": "中",
            })

    # 2. 检查 architecture-overview.md 位置
    arch_file = DOCS_DIR / "architecture-overview.md"
    if arch_file.exists():
        content = arch_file.read_text(encoding="utf-8", errors="ignore")
        if "不再更新" in content or "已归档" in content:
            issues.append({
                "type": "文档位置错误",
                "detail": "architecture-overview.md 声明已归档但仍位于 docs/ 根目录，应移入 docs/archive/",
                "severity": "中",
            })

    # 3. 检查 workspace-status 重复
    ws_root = DOCS_DIR / "workspace-status-2026-04-30.md"
    ws_archive = DOCS_DIR / "archive" / "workspace-status-2026-04-30.md"
    if ws_root.exists() and ws_archive.exists():
        issues.append({
            "type": "文档重复",
            "detail": "workspace-status-2026-04-30.md 同时存在于 docs/ 和 docs/archive/",
            "severity": "低",
        })

    # 4. 检查 WORKSPACE_SETUP.md 引用
    setup_file = REPO_ROOT.parent / "WORKSPACE_SETUP.md"
    if setup_file.exists():
        content = setup_file.read_text(encoding="utf-8", errors="ignore")
        if "思忆集test1.py" in content:
            issues.append({
                "type": "入口引用错误",
                "detail": "WORKSPACE_SETUP.md 引用 思忆集test1.py (备份文件)，应为 main.py",
                "severity": "高",
            })

    # 5. 报告数量
    reports_dir = DOCS_DIR / "reports"
    if reports_dir.exists():
        count = len(list(reports_dir.glob("*.md")))
        if count > 10:
            issues.append({
                "type": "报告膨胀",
                "detail": f"docs/reports/ 有 {count} 份自动生成报告，建议保留最近5份",
                "severity": "低",
            })

    return issues


def scan_web_ui_status() -> dict[str, Any]:
    """检查 Web UI 的集成状态。"""
    web_ui_dir = REPO_ROOT / "web-ui"
    web_dist_dir = APP_DIR / "web_dist"
    bridge_py = APP_DIR / "web_bridge_service.py"

    status = {
        "project_exists": web_ui_dir.exists(),
        "dist_exists": web_dist_dir.exists(),
        "has_bridge_py": bridge_py.exists(),
    }

    issues = []
    if status["project_exists"] and not status["dist_exists"]:
        issues.append("web-ui 存在但从未构建过 (web_dist/ 不存在)")
    if status["has_bridge_py"]:
        content = bridge_py.read_text(encoding="utf-8", errors="ignore")
        if "setHtml" in content and "Web Bridge Ready" in content:
            issues.append("QWebEngineView 加载的是占位 HTML，不是 web-ui 应用")
    status["issues"] = issues
    return status


def scan_nav_icons() -> list[dict[str, Any]]:
    """检查导航栏图标映射是否完整。"""
    nav_file = APP_DIR / "nav_bar.py"
    if not nav_file.exists():
        return []
    content = nav_file.read_text(encoding="utf-8", errors="ignore")
    nav_items_file = APP_DIR / "ui_sidebar_nav.py"
    nav_items_content = nav_items_file.read_text(encoding="utf-8", errors="ignore") if nav_items_file.exists() else ""

    # 从 ui_sidebar_nav.py 提取所有 nav item id
    ids_in_nav = set(re.findall(r'NavItem\("(\w+)"', nav_items_content))
    # 从 nav_bar.py 提取所有 icon key
    icons = set(re.findall(r'"(\w+)":\s*"([^"]+)",?', content))
    # 只保留几何符号样式的图标（非中文/英文标签）
    icons = {k for k, v in icons if len(v) == 1 and ord(v) > 0x2000}

    missing = ids_in_nav - icons
    if missing:
        return [{"type": "导航图标缺失", "detail": f"nav_bar.py 缺少图标: {', '.join(sorted(missing))}", "severity": "低"}]
    return []


def scan_bridge_stubs() -> list[dict[str, Any]]:
    """检查 QWebChannel bridge 是否使用 stub 数据。"""
    bridge_file = APP_DIR / "desktop_web_bridge.py"
    if not bridge_file.exists():
        return []
    content = bridge_file.read_text(encoding="utf-8", errors="ignore")
    # 统计 stub 类
    stub_classes = re.findall(r'class (\w+Service).*:\s*\n\s+def __init__', content)
    real_services = []
    for s in stub_classes:
        class_body_start = content.find(f"class {s}")
        class_body = content[class_body_start:class_body_start + 2000]
        if "stub" in class_body.lower() or "mock" in class_body.lower() or "fake" in class_body.lower():
            real_services.append(f"{s}(stub)")
        else:
            real_services.append(f"{s}(real?)")

    if real_services:
        stubs = [s for s in real_services if "stub" in s]
        if stubs:
            return [{
                "type": "Bridge Service 使用 Stub",
                "detail": f"{len(stubs)}/{len(real_services)} 个 bridge service 返回假数据: {', '.join(stubs)}",
                "severity": "高",
            }]
    return []


def generate_report(output_json: bool = False) -> dict[str, Any]:
    """生成完整健康检查报告。"""
    report = {
        "timestamp": datetime.now().isoformat(),
        "repo_root": str(REPO_ROOT),
        "summary": {},
        "capabilities": [],
        "doc_issues": [],
        "nav_issues": [],
        "web_ui": {},
        "bridge_issues": [],
    }

    # ── 扫描 ──
    caps = scan_capability_alignment()
    doc_issues = scan_doc_issues()
    nav_issues = scan_nav_icons()
    web_ui = scan_web_ui_status()
    bridge_issues = scan_bridge_stubs()

    report["capabilities"] = caps
    report["doc_issues"] = doc_issues
    report["nav_issues"] = nav_issues
    report["web_ui"] = web_ui
    report["bridge_issues"] = bridge_issues

    # ── 汇总 ──
    all_issues = []
    all_issues.extend(doc_issues)
    all_issues.extend(nav_issues)
    all_issues.extend(bridge_issues)
    all_issues.extend({"type": "Web UI", "detail": i, "severity": "高"} for i in web_ui.get("issues", []))

    by_severity = defaultdict(list)
    for issue in all_issues:
        by_severity[issue.get("severity", "低")].append(issue)

    report["summary"] = {
        "total_capabilities": len(caps),
        "fully_aligned": sum(1 for c in caps if c["status"] == "OK"),
        "partial": sum(1 for c in caps if c["status"] == "PARTIAL"),
        "missing": sum(1 for c in caps if c["status"] == "MISSING"),
        "backend_missing_count": sum(1 for c in caps if c["backend"] == "应有但无"),
        "backend_missing_list": [c["name"] for c in caps if c["backend"] == "应有但无"],
        "doc_issues_count": len(doc_issues),
        "high_severity_count": len(by_severity.get("高", [])),
        "medium_severity_count": len(by_severity.get("中", [])),
    }

    return report


def generate_analysis(report: dict[str, Any]) -> list[dict[str, Any]]:
    """为每个问题生成可供 Claude 决策的修复分析。

    每条分析包含：
    - issue: 问题描述
    - severity: 严重程度
    - fix_approach: 修复思路
    - files_to_read: 需要先读哪些文件才能动手
    - complexity: simple/moderate/complex
    - needs_decision: 是否需要用户确认
    - subprocess: 是否建议开子进程处理
    - prompt_hint: 如果是子进程任务，prompt 应该包含什么
    """
    analysis = []

    # ── 后端 API 缺失（8项） ──
    for cap in report["capabilities"]:
        if cap["backend"] == "应有但无":
            desktop_pattern = None
            for kc in KNOWN_CAPABILITIES:
                if kc["name"] == cap["name"]:
                    desktop_pattern = kc.get("desktop")
                    break

            desktop_files = []
            if desktop_pattern:
                for f in APP_DIR.rglob("*.py"):
                    if any(p.startswith(".") or p == "__pycache__" for p in f.relative_to(APP_DIR).parts):
                        continue
                    if desktop_pattern in f.name or desktop_pattern in f.read_text(encoding="utf-8", errors="ignore"):
                        desktop_files.append(str(f.relative_to(REPO_ROOT)))

            analysis.append({
                "type": "backend_api_missing",
                "capability": cap["name"],
                "severity": "高",
                "fix_approach": f"在 automation-hub 中为 {cap['name']} 创建 FastAPI 路由，参考桌面端已有实现",
                "files_to_read": desktop_files[:5],
                "complexity": "complex" if cap["name"] in ("工作流引擎", "知识库/RAG") else "moderate",
                "needs_decision": True,
                "subprocess": True,
                "prompt_hint": f"为 {cap['name']} 创建后端 API。参考桌面端 {desktop_pattern} 相关代码，在 automation-hub/api/routes/ 下新建路由文件，遵循现有路由风格（参考 /agent, /sync 等已有路由）。返回新建的文件列表。",
            })

    # ── 文档问题 ──
    for issue in report["doc_issues"]:
        if issue["type"] == "文档地图遗漏":
            analysis.append({
                "type": "doc_map_update",
                "severity": "中",
                "fix_approach": "扫描 docs/ 下所有 .md 文件，补充到 document-map.md 对应分类下",
                "files_to_read": ["docs/document-map.md"],
                "complexity": "simple",
                "needs_decision": False,
                "subprocess": True,
                "prompt_hint": f"更新 docs/document-map.md：添加以下缺失文档的引用 - {issue['detail']}",
            })
        elif issue["type"] == "文档位置错误":
            analysis.append({
                "type": "file_move",
                "severity": "中",
                "fix_approach": "将 architecture-overview.md 移入 docs/archive/",
                "files_to_read": [],
                "complexity": "simple",
                "needs_decision": False,
                "subprocess": False,
                "prompt_hint": "git mv docs/architecture-overview.md docs/archive/",
            })
        elif issue["type"] == "文档重复":
            analysis.append({
                "type": "file_cleanup",
                "severity": "低",
                "fix_approach": "删除 docs/workspace-status-2026-04-30.md（保留 archive 中副本）",
                "files_to_read": [],
                "complexity": "simple",
                "needs_decision": False,
                "subprocess": False,
                "prompt_hint": "删除 docs/ 根目录的重复文件，保留 archive 版本",
            })
        elif issue["type"] == "入口引用错误":
            analysis.append({
                "type": "config_fix",
                "severity": "高",
                "fix_approach": "将 WORKSPACE_SETUP.md 中的思忆集test1.py 替换为 main.py",
                "files_to_read": [str(REPO_ROOT.parent / "WORKSPACE_SETUP.md")],
                "complexity": "simple",
                "needs_decision": False,
                "subprocess": False,
                "prompt_hint": "",
            })
        elif issue["type"] == "报告膨胀":
            analysis.append({
                "type": "cleanup",
                "severity": "低",
                "fix_approach": "清理 docs/reports/ 旧报告，保留最近5份",
                "files_to_read": [],
                "complexity": "simple",
                "needs_decision": True,
                "subprocess": True,
                "prompt_hint": "清理 docs/reports/ 目录，只保留最近5份，其余移入 docs/archive/reports/",
            })

    # ── 导航图标 ──
    for issue in report["nav_issues"]:
        if issue["type"] == "导航图标缺失":
            analysis.append({
                "type": "nav_icon_fix",
                "severity": "低",
                "fix_approach": "为 nav_bar.py 补充缺失的导航图标映射",
                "files_to_read": ["思忆集app/nav_bar.py", "思忆集app/ui_sidebar_nav.py"],
                "complexity": "simple",
                "needs_decision": False,
                "subprocess": False,
                "prompt_hint": "",
            })

    # ── Web UI ──
    if not report["web_ui"].get("dist_exists", False):
        analysis.append({
            "type": "web_ui_build",
            "severity": "高",
            "fix_approach": "在 web-ui/ 运行 npm run build（vite 输出到 思忆集app/web_dist/）",
            "files_to_read": ["web-ui/package.json", "思忆集app/web_bridge_service.py"],
            "complexity": "moderate",
            "needs_decision": True,
            "subprocess": True,
            "prompt_hint": "在 web-ui/ 运行 npm run build（vite 配置 outDir 为 ../思忆集app/web_dist）",
        })

    # ── Bridge Stubs ──
    for issue in report["bridge_issues"]:
        if issue["type"] == "Bridge Service 使用 Stub":
            analysis.append({
                "type": "bridge_stub_fix",
                "severity": "高",
                "fix_approach": "逐一将 stub service 替换为真实实现（通过 AutomationHubClient 调用后端 API）",
                "files_to_read": ["思忆集app/desktop_web_bridge.py", "思忆集app/automation_hub_client.py"],
                "complexity": "complex",
                "needs_decision": True,
                "subprocess": True,
                "prompt_hint": "将 desktop_web_bridge.py 的 stub service 替换为通过 AutomationHubClient 调用后端 API 的真实实现",
            })

    return analysis


# ══════════════════════════════════════════════════════════════
#  小问集成接口
# ══════════════════════════════════════════════════════════════

def format_for_xiaowen(report: dict[str, Any]) -> str:
    """格式化为小问可消费的简短摘要（方案B的基础）。"""
    s = report["summary"]
    lines = [
        f"📊 项目健康检查 ({report['timestamp'][:10]})",
        f"",
        f"功能对齐: {s['fully_aligned']}✅ / {s['partial']}⚠️ / {s['missing']}❌  (共{s['total_capabilities']}项)",
        f"缺少后端API: {s['backend_missing_count']}项",
    ]
    if s["backend_missing_list"]:
        lines.append(f"  → {'、'.join(s['backend_missing_list'])}")
    lines.append(f"文档问题: {s['doc_issues_count']}项 (高危{s['high_severity_count']}项)")
    lines.append(f"")
    lines.append("🛠 建议优先级:")
    if s["high_severity_count"] > 0:
        lines.append(f"  P0: 处理 {s['high_severity_count']} 个高风险问题")
    if s["backend_missing_count"] > 0:
        lines.append(f"  P1: 为 {s['backend_missing_count']} 项能力添加后端API")
    if s.get("doc_issues_count", 0) > 0:
        lines.append(f"  P2: 更新文档地图，清理过时文档")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Windows GBK 终端兼容
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    output_json = "--json" in sys.argv
    analyze_mode = "--analyze" in sys.argv
    report = generate_report(output_json=output_json)

    if output_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif analyze_mode:
        s = report["summary"]
        analysis = generate_analysis(report)
        sep = "=" * 60

        print(sep)
        print("  思忆集 — Claude 决策分析")
        print(f"  {report['timestamp']}")
        print(sep)

        print(f"\n共 {len(analysis)} 个待处理问题\n")

        # 按优先级分组
        by_complexity = {"simple": [], "moderate": [], "complex": []}
        for a in analysis:
            by_complexity.setdefault(a["complexity"], []).append(a)

        for label, comp in [("P0 - 简单（可直接在当前会话修复）", "simple"),
                            ("P1 - 中等（可当前会话或开子进程）", "moderate"),
                            ("P2 - 复杂（建议开 Claude 子进程）", "complex")]:
            items = by_complexity.get(comp, [])
            if not items:
                continue
            print(f"\n  >> {label} ({len(items)}项)")
            for a in items:
                flag = " 🚧需决策" if a.get("needs_decision") else ""
                sfx = " [subprocess]" if a.get("subprocess") else " [direct]"
                print(f"\n    [{a['type']}]{sfx}{flag}")
                print(f"    问题: {a.get('capability', a.get('fix_approach', ''))}")
                print(f"    方案: {a['fix_approach']}")
                if a.get("files_to_read"):
                    print(f"    先读: {', '.join(a['files_to_read'][:3])}")
                if a.get("prompt_hint") and a.get("subprocess"):
                    print(f"    prompt: {a['prompt_hint'][:120]}...")

        print(f"\n{sep}")
        print("统计: 简单={} / 中等={} / 复杂={}".format(
            len(by_complexity.get("simple", [])),
            len(by_complexity.get("moderate", [])),
            len(by_complexity.get("complex", [])),
        ))
        print("可直接修复={} / 需子进程={}".format(
            sum(1 for a in analysis if not a["subprocess"]),
            sum(1 for a in analysis if a["subprocess"]),
        ))
        print(sep)
    else:
        s = report["summary"]
        sep = "=" * 60
        print(sep)
        print("  思忆集项目健康检查报告")
        print(f"  {report['timestamp']}")
        print(sep)
        print(f"\n[功能对齐概览] ({s['total_capabilities']}项)")
        print(f"  [完整] {s['fully_aligned']}")
        print(f"  [部分] {s['partial']}")
        print(f"  [缺失] {s['missing']}")
        print()
        for cap in report["capabilities"]:
            status_ch = {"OK": "OK", "MISSING": "MIS", "PARTIAL": "PRT", "DESKTOP_ONLY": "DOK"}.get(cap["status"], "???")
            print(f"  [{status_ch}] {cap['name']}  (doc={cap['doc']}, desktop={cap['desktop']}, backend={cap['backend']})")
            if cap["gaps"]:
                print(f"         gaps: {'; '.join(cap['gaps'])}")
        print(f"\n[缺少后端API] ({s['backend_missing_count']}项):")
        for name in s["backend_missing_list"]:
            print(f"  - {name}")
        print(f"\n[文档问题] {s['doc_issues_count']}项 (高危{s['high_severity_count']}, 中危{s['medium_severity_count']})")
        for issue in report["doc_issues"]:
            print(f"  [{issue['severity']}] {issue['detail']}")
        if report["nav_issues"]:
            for issue in report["nav_issues"]:
                print(f"  [{issue['severity']}] {issue['detail']}")
        if report["web_ui"]["issues"]:
            print(f"\n[Web UI 问题]:")
            for i in report["web_ui"]["issues"]:
                print(f"  - {i}")
        if report["bridge_issues"]:
            print(f"\n[Bridge 问题]:")
            for issue in report["bridge_issues"]:
                print(f"  [{issue['severity']}] {issue['detail']}")

        print(f"\n{sep}")
        print("建议:")
        if s["high_severity_count"] > 0:
            print(f"  P0: 处理 {s['high_severity_count']} 个高风险问题")
        if s["backend_missing_count"] > 0:
            print(f"  P1: 为缺少后端API的能力建路由 (参考桌面端现有实现)")
        if s.get("doc_issues_count", 0) > 0:
            print(f"  P2: 更新 document-map.md 补齐遗漏文档")
        if (REPO_ROOT / "docs" / "architecture-overview.md").exists():
            print(f"  P3: 将 architecture-overview.md 移入 archive/")
        print(sep)

        # 同时输出小问可消费格式
        print(f"\n--- 小问摘要 ---\n")
        print(format_for_xiaowen(report))
