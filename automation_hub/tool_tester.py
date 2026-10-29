"""
工具测试验证系统

用于测试工具是否正常工作
"""

import subprocess
import json
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import sqlite3


@dataclass
class TestResult:
    """测试结果"""
    tool_id: str
    success: bool
    duration_ms: float
    output: str
    error: Optional[str] = None
    dependency_check: bool = True


class ToolTester:
    """工具测试器"""
    
    def __init__(self, db_path: str):
        """
        初始化测试器
        
        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
    
    def test_tool(
        self,
        tool_id: str,
        test_args: Optional[Dict[str, Any]] = None
    ) -> TestResult:
        """
        测试单个工具
        
        Args:
            tool_id: 工具ID
            test_args: 测试参数，如果为None则使用默认测试参数
            
        Returns:
            TestResult
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取工具信息
        cursor.execute("""
            SELECT id, name, command_json, executor, enabled
            FROM tools
            WHERE id = ?
        """, (tool_id,))
        
        tool = cursor.fetchone()
        conn.close()
        
        if not tool:
            return TestResult(
                tool_id=tool_id,
                success=False,
                duration_ms=0,
                output="",
                error=f"工具不存在: {tool_id}"
            )
        
        tool_id, name, command_json, executor, enabled = tool
        
        if not enabled:
            return TestResult(
                tool_id=tool_id,
                success=False,
                duration_ms=0,
                output="",
                error="工具已禁用"
            )
        
        # 检查依赖
        command_template = json.loads(command_json)
        base_command = command_template[0] if command_template else ""
        
        dependency_ok = self._check_dependency(base_command)
        
        if not dependency_ok:
            return TestResult(
                tool_id=tool_id,
                success=False,
                duration_ms=0,
                output="",
                error=f"依赖未安装: {base_command}",
                dependency_check=False
            )
        
        # 构建测试命令
        if test_args is None:
            test_args = self._get_default_test_args(tool_id)
        
        command = self._build_command(command_template, test_args)
        
        # 执行测试
        start_time = time.time()
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            duration_ms = (time.time() - start_time) * 1000
            
            return TestResult(
                tool_id=tool_id,
                success=result.returncode == 0,
                duration_ms=duration_ms,
                output=result.stdout or result.stderr,
                error=result.stderr if result.returncode != 0 else None
            )
        
        except subprocess.TimeoutExpired:
            duration_ms = (time.time() - start_time) * 1000
            return TestResult(
                tool_id=tool_id,
                success=False,
                duration_ms=duration_ms,
                output="",
                error="测试超时（10秒）"
            )
        
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return TestResult(
                tool_id=tool_id,
                success=False,
                duration_ms=duration_ms,
                output="",
                error=f"执行异常: {str(e)}"
            )
    
    def test_all_tools(self) -> List[TestResult]:
        """
        测试所有工具
        
        Returns:
            测试结果列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM tools WHERE enabled = 1")
        tool_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        results = []
        for tool_id in tool_ids:
            result = self.test_tool(tool_id)
            results.append(result)
        
        return results
    
    def _check_dependency(self, command: str) -> bool:
        """检查命令是否可用"""
        import shutil
        return shutil.which(command) is not None
    
    def _get_default_test_args(self, tool_id: str) -> Dict[str, Any]:
        """获取默认测试参数"""
        # 预定义的测试参数
        default_args = {
            "code_search": {"pattern": "test", "path": "."},
            "list_files": {"path": "."},
            "git_status": {},
            "git_diff": {},
            "git_log": {"count": 1},
            "run_pytest": {"path": "tests/"},
            "lint_python": {"path": "."},
            "format_python": {"path": ".", "check": True},
            "read_file": {"path": "README.md"},
            "disk_usage": {"path": "."}
        }
        
        return default_args.get(tool_id, {})
    
    def _build_command(
        self,
        template: List[str],
        args: Dict[str, Any]
    ) -> List[str]:
        """构建命令"""
        command = []
        
        for part in template:
            if part.startswith("{") and part.endswith("}"):
                param_name = part[1:-1]
                if param_name in args:
                    value = args[param_name]
                    if isinstance(value, bool):
                        continue  # 布尔值作为标志，不添加值
                    command.append(str(value))
            else:
                command.append(part)
        
        # 添加额外参数
        for key, value in args.items():
            placeholder = f"{{{key}}}"
            if placeholder not in template:
                if isinstance(value, bool):
                    if value:
                        command.append(f"--{key}")
                else:
                    command.append(f"--{key}")
                    command.append(str(value))
        
        return command
    
    def print_test_report(self, results: List[TestResult]):
        """打印测试报告"""
        from rich.console import Console
        from rich.table import Table
        from rich import box
        
        console = Console()
        
        # 统计
        total = len(results)
        passed = sum(1 for r in results if r.success)
        failed = total - passed
        dependency_issues = sum(1 for r in results if not r.dependency_check)
        
        console.print("\n" + "="*60)
        console.print("  工具测试报告")
        console.print("="*60)
        
        console.print(f"\n✅ 通过: {passed}/{total}")
        console.print(f"❌ 失败: {failed}/{total}")
        if dependency_issues:
            console.print(f"⚠️  依赖问题: {dependency_issues}")
        
        # 详细结果
        if results:
            console.print("\n详细结果:")
            console.print("-" * 60)
            
            table = Table(box=box.ROUNDED)
            table.add_column("工具ID", style="cyan")
            table.add_column("状态", style="white")
            table.add_column("耗时(ms)", justify="right")
            table.add_column("错误", style="red")
            
            for result in results:
                status_icon = "✅" if result.success else "❌"
                error_msg = result.error or ""
                
                table.add_row(
                    result.tool_id,
                    status_icon,
                    f"{result.duration_ms:.0f}",
                    error_msg[:50]  # 截断长错误消息
                )
            
            console.print(table)
        
        console.print("="*60 + "\n")


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="测试工具")
    parser.add_argument(
        "--tool",
        help="测试特定工具"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="测试所有工具"
    )
    parser.add_argument(
        "--db",
        default="data/automation_hub.sqlite3",
        help="数据库路径"
    )
    
    args = parser.parse_args()
    
    tester = ToolTester(args.db)
    
    if args.all:
        results = tester.test_all_tools()
        tester.print_test_report(results)
    elif args.tool:
        result = tester.test_tool(args.tool)
        tester.print_test_report([result])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
