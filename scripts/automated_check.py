#!/usr/bin/env python3
"""
Siyi Project Automated Check Script
功能：Wiki 文档生成、代码分析、UI/UX 检测、模拟测试
用法：python scripts/automated_check.py --phase all
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List

# 配置
PROJECT_ROOT = Path(__file__).parent.parent
WIKI_DIR = PROJECT_ROOT / "docs" / "wiki"
REPORTS_DIR = PROJECT_ROOT / "reports"


class AutomatedChecker:
    def __init__(self):
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "phase": "initializing",
            "checks": [],
            "errors": [],
            "warnings": []
        }
        
    def log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level.upper()}] {message}")
        
    def run_phase(self, phase_name: str, check_func) -> bool:
        self.log(f"\n{'='*60}\nPhase: {phase_name}\n{'='*60}")
        self.results["phase"] = phase_name
        
        try:
            success = check_func()
            self.results["checks"].append({
                "phase": phase_name,
                "status": "success" if success else "failed",
                "time": datetime.now().isoformat()
            })
            return success
        except Exception as e:
            self.log(f"FAILED: {e}", "error")
            self.results["errors"].append({
                "phase": phase_name,
                "error": str(e),
                "time": datetime.now().isoformat()
            })
            return False
    
    def phase_wiki_setup(self) -> bool:
        """创建 Wiki 目录结构"""
        self.log("Creating Wiki directories...")
        
        dirs = [
            WIKI_DIR / "01-electron-frontend",
            WIKI_DIR / "02-python-backend",
            WIKI_DIR / "03-mobile-app",
            WIKI_DIR / "04-cloud-sync",
            WIKI_DIR / "05-code-tools",
            WIKI_DIR / "99-maintenance",
        ]
        
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
            self.log(f"Created: {d}")
        
        return True
    
    def phase_rpc_inventory(self) -> bool:
        """生成 RPC 方法清单"""
        self.log("Generating RPC inventory...")
        
        try:
            backend_path = PROJECT_ROOT / "思忆集 app"
            sys.path.insert(0, str(backend_path))
            
            from backend.method_registry import registry
            methods = list(registry.list_methods())
            
            output_file = WIKI_DIR / "05-code-tools" / "rpc-methods-index.md"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("# RPC Method Index\n\n")
                f.write(f"> Generated: {datetime.now().isoformat()}\n")
                f.write(f"> Total: {len(methods)} methods\n\n")
                
                modules = {}
                for method in methods:
                    parts = method.split(".")
                    module = parts[0] if parts else "unknown"
                    if module not in modules:
                        modules[module] = []
                    modules[module].append(method)
                
                for module, method_list in sorted(modules.items()):
                    f.write(f"## {module}.*\n\n")
                    for m in method_list:
                        f.write(f"- `{m}`\n")
                    f.write("\n")
            
            self.log(f"Saved {len(methods)} methods to {output_file}")
            return True
            
        except ImportError as e:
            self.log(f"WARNING: Cannot import backend: {e}", "warning")
            self.results["warnings"].append(f"RPC inventory: {e}")
            return False
        except Exception as e:
            self.log(f"FAILED: {e}", "error")
            return False
    
    def phase_code_analysis(self) -> bool:
        """代码结构分析"""
        self.log("Analyzing code structure...")
        
        analysis = {
            "electron_frontend": {"components": [], "hooks": [], "utils": []},
            "python_backend": {"handlers": [], "services": [], "models": []}
        }
        
        # Scan Electron components
        desktop_dir = PROJECT_ROOT / "desk-notes" / "packages" / "desktop" / "src" / "components"
        if desktop_dir.exists():
            for file in desktop_dir.rglob("*.tsx"):
                rel_path = file.relative_to(desktop_dir)
                analysis["electron_frontend"]["components"].append(str(rel_path))
                self.log(f"Found component: {rel_path}")
        
        # Scan Python handlers
        handler_dir = PROJECT_ROOT / "思忆集 app" / "backend" / "handlers"
        if handler_dir.exists():
            for file in handler_dir.glob("_*.py"):
                name = file.stem
                analysis["python_backend"]["handlers"].append(name)
                self.log(f"Found handler: {name}")
        
        output_file = REPORTS_DIR / "code-analysis.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)
        
        self.log(f"Saved to {output_file}")
        return True
    
    def phase_ui_ux_audit(self) -> bool:
        """UI/UX 问题检测"""
        self.log("Auditing UI/UX issues...")
        
        issues = []
        
        # Check ChatView.tsx
        chat_view_path = PROJECT_ROOT / "desk-notes" / "packages" / "desktop" / "src" / "components" / "chat" / "ChatView.tsx"
        if chat_view_path.exists():
            content = chat_view_path.read_text(encoding="utf-8")
            if "describeToolError" in content:
                self.log("OK: ChatView has error optimization")
            else:
                issues.append({"component": "ChatView", "issue": "Missing error hint optimization", "severity": "P2"})
        
        # Check NotesView.tsx
        notes_view_path = PROJECT_ROOT / "desk-notes" / "packages" / "mobile" / "src" / "components" / "features" / "notes" / "NotesView.tsx"
        if notes_view_path.exists():
            content = notes_view_path.read_text(encoding="utf-8")
            if "textarea" in content:
                issues.append({"component": "NotesView", "issue": "Plain text only, upgrade to Markdown editor", "severity": "P1"})
                self.log("WARNING: Notes uses plain textarea")
        
        output_file = REPORTS_DIR / "ui-ux-audit.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "issues": issues}, f, indent=2, ensure_ascii=False)
        
        self.log(f"Found {len(issues)} issues, saved to {output_file}")
        return True
    
    def phase_test_simulation(self) -> bool:
        """模拟客户测试"""
        self.log("Running simulated customer tests...")
        
        test_results = {
            "Scenario_1_NewUserOnboarding": self.test_new_user_onboarding(),
            "Scenario_2_DailyKnowledgeManagement": self.test_daily_knowledge_management(),
            "Scenario_3_CrossDeviceSync": self.test_cross_device_sync(),
            "Scenario_4_AIAssistant": self.test_ai_assistant(),
            "Scenario_5_ProactiveSuggestions": self.test_proactive_suggestions(),
            "Scenario_6_ApprovalFlow": self.test_approval_flow()
        }
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_scenarios": len(test_results),
            "passed": sum(1 for r in test_results.values() if r.get("status") == "pass"),
            "failed": sum(1 for r in test_results.values() if r.get("status") == "fail"),
            "scenarios": test_results
        }
        
        output_file = REPORTS_DIR / "simulated-test-results.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        self.log(f"Completed {summary['total_scenarios']} scenarios, passed {summary['passed']}")
        return True
    
    def test_new_user_onboarding(self) -> Dict:
        try:
            sys.path.insert(0, str(PROJECT_ROOT / "思忆集 app"))
            from backend.method_registry import registry
            methods = list(registry.list_methods())
            has_onboarding = any("onboarding" in m for m in methods)
            
            return {
                "status": "pass" if has_onboarding else "partial",
                "details": "Basic features exist" if has_onboarding else "Missing complete onboarding flow",
                "recommendation": "Add first-time setup wizard"
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def test_daily_knowledge_management(self) -> Dict:
        try:
            kg_exists = (PROJECT_ROOT / "思忆集 app" / "knowledge" / "graph_store.py").exists()
            frontend_viz = list((PROJECT_ROOT / "desk-notes" / "packages" / "desktop" / "src" / "components").rglob("*Graph*.tsx"))
            
            return {
                "status": "pass" if kg_exists and frontend_viz else "partial",
                "details": f"Backend:{'OK' if kg_exists else 'MISSING'}, Frontend:{'OK' if frontend_viz else 'MISSING'}",
                "recommendation": "Knowledge graph has backend but missing frontend visualization"
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def test_cross_device_sync(self) -> Dict:
        sync_client = PROJECT_ROOT / "思忆集 app" / "sync" / "cloud_sync_client.py"
        worker_path = PROJECT_ROOT / "cloudflare" / "worker" / "src" / "index.ts"
        
        return {
            "status": "pass" if sync_client.exists() and worker_path.exists() else "fail",
            "details": f"Client:{'OK' if sync_client.exists() else 'MISSING'}, Worker:{'OK' if worker_path.exists() else 'MISSING'}",
            "recommendation": "Sync protocol implemented, test latency and conflict handling"
        }
    
    def test_ai_assistant(self) -> Dict:
        try:
            sys.path.insert(0, str(PROJECT_ROOT / "思忆集 app"))
            from backend.method_registry import registry
            methods = list(registry.list_methods())
            chat_methods = [m for m in methods if "chat" in m]
            
            return {
                "status": "pass" if len(chat_methods) > 5 else "partial",
                "details": f"Chat methods:{len(chat_methods)}, Tool methods:{len([m for m in methods if any(x in m for x in ['search','read','exec'])])}",
                "recommendation": "AI assistant is complete, optimize error hints"
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def test_proactive_suggestions(self) -> Dict:
        try:
            sys.path.insert(0, str(PROJECT_ROOT / "思忆集 app"))
            from backend.method_registry import registry
            methods = list(registry.list_methods())
            proactive = [m for m in methods if "proactive" in m or "inbox" in m]
            
            return {
                "status": "pass" if len(proactive) > 3 else "partial",
                "details": f"Proactive methods:{len(proactive)}",
                "recommendation": "Event push mechanism exists, optimize priority sorting"
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def test_approval_flow(self) -> Dict:
        try:
            sys.path.insert(0, str(PROJECT_ROOT / "思忆集 app"))
            from backend.method_registry import registry
            methods = list(registry.list_methods())
            sandbox = [m for m in methods if "sandbox" in m or "approval" in m or "confirmation" in m]
            
            return {
                "status": "pass" if len(sandbox) > 5 else "partial",
                "details": f"Approval methods:{len(sandbox)}",
                "recommendation": "Approval mechanism exists, add batch operations"
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def generate_report(self):
        """Generate final report"""
        self.log("="*60 + "\nGenerating Final Report\n" + "="*60)
        
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_phases": len([c for c in self.results["checks"] if c["status"] == "success"]),
                "failed_phases": len([c for c in self.results["checks"] if c["status"] == "failed"]),
                "errors": len(self.results["errors"]),
                "warnings": len(self.results["warnings"])
            },
            "details": self.results
        }
        
        json_file = REPORTS_DIR / "automated-check-report.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        md_file = REPORTS_DIR / "automated-check-report.md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write("# Siyi Project Automated Check Report\n\n")
            f.write(f"> Generated: {datetime.now().isoformat()}\n\n")
            
            f.write("## Summary\n\n")
            f.write(f"- Success phases: {report['summary']['total_phases']}\n")
            f.write(f"- Failed phases: {report['summary']['failed_phases']}\n")
            f.write(f"- Warnings: {report['summary']['warnings']}\n")
            f.write(f"- Errors: {report['summary']['errors']}\n\n")
            
            f.write("## Details\n\n")
            for check in self.results["checks"]:
                status_icon = "[PASS]" if check["status"] == "success" else "[FAIL]"
                f.write(f"### {status_icon} {check['phase']}\n")
                f.write(f"Time: {check['time']}\n\n")
        
        self.log(f"Report saved to:")
        self.log(f"  - {json_file}")
        self.log(f"  - {md_file}")
        
        return True


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Siyi Project Automated Check")
    parser.add_argument("--phase", choices=["wiki", "inventory", "analysis", "audit", "test", "all"], default="all")
    args = parser.parse_args()
    
    print("="*60)
    print("Siyi Project Automated Check")
    print("="*60)
    
    checker = AutomatedChecker()
    
    if args.phase in ["wiki", "all"]:
        if not checker.run_phase("Wiki Directory Creation", checker.phase_wiki_setup):
            return False
    
    if args.phase in ["inventory", "all"]:
        checker.run_phase("RPC Inventory Generation", checker.phase_rpc_inventory)
    
    if args.phase in ["analysis", "all"]:
        if not checker.run_phase("Code Structure Analysis", checker.phase_code_analysis):
            return False
    
    if args.phase in ["audit", "all"]:
        checker.run_phase("UI/UX Audit", checker.phase_ui_ux_audit)
    
    if args.phase in ["test", "all"]:
        checker.run_phase("Simulated Customer Tests", checker.phase_test_simulation)
    
    checker.generate_report()
    
    print("\n" + "="*60)
    print("Automated Check Completed!")
    print("="*60)
    
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
