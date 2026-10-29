"""
输出格式化器

支持多种输出格式：Table, JSON, YAML
"""

import json
import yaml
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax
from rich import box


class OutputFormatter:
    """输出格式化器"""
    
    def __init__(self, format: str = "table", color: bool = True):
        """
        初始化格式化器
        
        Args:
            format: 输出格式 (table, json, yaml)
            color: 是否使用颜色
        """
        self.format = format.lower()
        self.color = color
        self.console = Console(color_system="auto" if color else None)
    
    def format_list(
        self,
        data: List[Dict[str, Any]],
        columns: Optional[List[str]] = None,
        title: Optional[str] = None
    ) -> str:
        """
        格式化列表数据
        
        Args:
            data: 数据列表
            columns: 列名列表，如果为None则使用第一行的键
            title: 表格标题
            
        Returns:
            格式化后的字符串
        """
        if not data:
            return "No data"
        
        if self.format == "json":
            return json.dumps(data, indent=2, ensure_ascii=False)
        
        elif self.format == "yaml":
            return yaml.dump(data, default_flow_style=False, allow_unicode=True)
        
        else:  # table
            if columns is None:
                columns = list(data[0].keys())
            
            table = Table(title=title, box=box.ROUNDED, show_lines=False)
            
            for col in columns:
                table.add_column(col, style="cyan")
            
            for row in data:
                table.add_row(*[str(row.get(col, "")) for col in columns])
            
            # 使用console渲染到字符串
            from io import StringIO
            string_io = StringIO()
            temp_console = Console(file=string_io, color_system="auto" if self.color else None)
            temp_console.print(table)
            return string_io.getvalue()
    
    def format_dict(self, data: Dict[str, Any], title: Optional[str] = None) -> str:
        """
        格式化字典数据
        
        Args:
            data: 字典数据
            title: 标题
            
        Returns:
            格式化后的字符串
        """
        if self.format == "json":
            return json.dumps(data, indent=2, ensure_ascii=False)
        
        elif self.format == "yaml":
            return yaml.dump(data, default_flow_style=False, allow_unicode=True)
        
        else:  # table
            table = Table(title=title, box=box.ROUNDED, show_header=False)
            table.add_column("Key", style="cyan")
            table.add_column("Value", style="white")
            
            for key, value in data.items():
                table.add_row(str(key), str(value))
            
            from io import StringIO
            string_io = StringIO()
            temp_console = Console(file=string_io, color_system="auto" if self.color else None)
            temp_console.print(table)
            return string_io.getvalue()
    
    def format_code(
        self,
        code: str,
        language: str = "python",
        title: Optional[str] = None
    ) -> str:
        """
        格式化代码（带语法高亮）
        
        Args:
            code: 代码内容
            language: 编程语言
            title: 标题
            
        Returns:
            格式化后的字符串
        """
        if not self.color or self.format != "table":
            return code
        
        syntax = Syntax(code, language, theme="monokai", line_numbers=True)
        
        from io import StringIO
        string_io = StringIO()
        temp_console = Console(file=string_io)
        
        if title:
            temp_console.print(f"\n[bold cyan]{title}[/bold cyan]")
        
        temp_console.print(syntax)
        return string_io.getvalue()
    
    def print(self, content: str):
        """打印内容"""
        print(content)
    
    def export_to_file(
        self,
        data: Any,
        filepath: str,
        format: Optional[str] = None
    ):
        """
        导出数据到文件
        
        Args:
            data: 数据
            filepath: 文件路径
            format: 输出格式，如果为None则根据文件扩展名判断
        """
        if format is None:
            if filepath.endswith('.json'):
                format = 'json'
            elif filepath.endswith('.yaml') or filepath.endswith('.yml'):
                format = 'yaml'
            else:
                format = self.format
        
        with open(filepath, 'w', encoding='utf-8') as f:
            if format == 'json':
                json.dump(data, f, indent=2, ensure_ascii=False)
            elif format == 'yaml':
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            else:
                f.write(str(data))


class ResultFormatter:
    """执行结果格式化器"""
    
    @staticmethod
    def format_run_result(result: Dict[str, Any], formatter: OutputFormatter) -> str:
        """
        格式化执行结果
        
        Args:
            result: 执行结果
            formatter: 输出格式化器
            
        Returns:
            格式化后的字符串
        """
        output = []
        
        # 状态
        if result.get("success"):
            output.append("✅ 执行成功")
        else:
            output.append("❌ 执行失败")
        
        # 基本信息
        if "run_id" in result:
            output.append(f"\n任务ID: {result['run_id']}")
        
        if "status" in result:
            output.append(f"状态: {result['status']}")
        
        # 输出内容
        if result.get("stdout"):
            output.append("\n[标准输出]")
            
            # 尝试检测语言并高亮
            stdout = result["stdout"]
            if formatter.color and formatter.format == "table":
                # 简单检测：如果包含Python关键字，使用Python高亮
                if any(kw in stdout for kw in ["def ", "class ", "import ", "from "]):
                    output.append(formatter.format_code(stdout, "python"))
                # 如果是JSON
                elif stdout.strip().startswith('{') or stdout.strip().startswith('['):
                    try:
                        parsed = json.loads(stdout)
                        output.append(formatter.format_code(
                            json.dumps(parsed, indent=2),
                            "json"
                        ))
                    except Exception:
                        output.append(stdout)
                else:
                    output.append(stdout)
            else:
                output.append(stdout)
        
        # 错误输出
        if result.get("stderr"):
            output.append("\n[标准错误]")
            output.append(result["stderr"])
        
        # 退出码
        if "exit_code" in result:
            output.append(f"\n退出码: {result['exit_code']}")
        
        return "\n".join(output)


# 便捷函数

def create_formatter(format: str = "table", color: bool = True) -> OutputFormatter:
    """创建格式化器"""
    return OutputFormatter(format=format, color=color)


def format_as_table(data: List[Dict], title: Optional[str] = None) -> str:
    """格式化为表格"""
    return OutputFormatter(format="table").format_list(data, title=title)


def format_as_json(data: Any) -> str:
    """格式化为JSON"""
    return json.dumps(data, indent=2, ensure_ascii=False)


def format_as_yaml(data: Any) -> str:
    """格式化为YAML"""
    return yaml.dump(data, default_flow_style=False, allow_unicode=True)
