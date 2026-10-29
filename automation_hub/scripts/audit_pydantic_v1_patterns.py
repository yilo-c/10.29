#!/usr/bin/env python3
"""
Pydantic v1 → v2 审计脚本（破冰卡）

用途：
- 扫描 automation-hub 项目中所有使用 Pydantic v1 的模式
- 生成迁移清单，标记需要修改的文件和代码行
- 为后续的 v1→v2 升级提供基础数据

运行方式：
cd automation-hub
python scripts/audit_pydantic_v1_patterns.py

输出：
- audit_report.md：详细的审计报告
- migration_todo.txt：待办事项清单
"""

import os
import re
import ast
import argparse
from pathlib import Path
from typing import List, Dict, Tuple, Set

# Pydantic v1 特有的模式（这些在 v2 中已废弃或改变）
PYDANTIC_V1_PATTERNS = {
    "config_class": r"class Config:\s*",
    "orm_mode": r"orm_mode\s*=\s*True",
    "allow_mutation": r"allow_mutation\s*=",
    "fields_with_underscore": r"Field\([^)]*alias\s*=\s*\"_[^)]*\)",
    "validate_assignment": r"validate_assignment\s*=\s*True",
    "extra_forbid": r"extra\s*=\s*Extra\.forbid",
    "root_validator_pre": r"@root_validator\(.*?pre=True",
    "root_validator_no_pre": r"@root_validator$",
    "validator_wildcard": r"@validator\(\s*\"\*\"",
    "get_field_value": r"values\.get\(",
    "schema_extra": r"schema_extra\s*=",
    "copy_on_model_validation": r"copy_on_model_validation\s*=",
}

def scan_python_files(root_dir: str) -> List[str]:
    """扫描目录下所有Python文件"""
    python_files = []
    for root, _, files in os.walk(root_dir):
        # 跳过一些目录
        if any(skip in root for skip in ['.venv', '__pycache__', '.pytest_cache', 'tests']):
            continue
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    return python_files

def check_v1_patterns(file_path: str) -> Dict[str, List[int]]:
    """检查文件中的v1模式"""
    patterns_found = {}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line_num, line in enumerate(lines, 1):
            for pattern_name, pattern in PYDANTIC_V1_PATTERNS.items():
                if re.search(pattern, line):
                    if pattern_name not in patterns_found:
                        patterns_found[pattern_name] = []
                    patterns_found[pattern_name].append(line_num)
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        
    return patterns_found

def generate_audit_report(root_dir: str, output_dir: str = "."):
    """生成审计报告"""
    python_files = scan_python_files(root_dir)
    all_findings = {}
    
    print(f"Scanning {len(python_files)} Python files...")
    
    for file_path in python_files:
        rel_path = os.path.relpath(file_path, root_dir)
        findings = check_v1_patterns(file_path)
        if findings:
            all_findings[rel_path] = findings
    
    # 生成详细报告
    report_path = os.path.join(output_dir, "audit_report.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# Pydantic v1 → v2 迁移审计报告\n\n")
        f.write(f"扫描目录: `{root_dir}`\n\n")
        f.write(f"发现 {len(all_findings)} 个文件包含 Pydantic v1 模式\n\n")
        
        if all_findings:
            f.write("## 详细发现\n\n")
            for file_path, findings in sorted(all_findings.items()):
                f.write(f"### `{file_path}`\n\n")
                for pattern, lines in findings.items():
                    f.write(f"- **{pattern}**: 行 {', '.join(map(str, lines))}\n")
                f.write("\n")
        else:
            f.write("未发现 Pydantic v1 特有模式。\n\n")
    
    # 生成待办清单
    todo_path = os.path.join(output_dir, "migration_todo.txt")
    with open(todo_path, 'w', encoding='utf-8') as f:
        if all_findings:
            f.write("# Pydantic v1 → v2 迁移待办清单\n\n")
            for file_path in sorted(all_findings.keys()):
                f.write(f"- [ ] {file_path}\n")
        else:
            f.write("# 无需迁移\n\n")
    
    print(f"审计报告已生成: {report_path}")
    print(f"待办清单已生成: {todo_path}")
    
    return all_findings

def main():
    parser = argparse.ArgumentParser(description="Audit Pydantic v1 patterns")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--output", default=".", help="输出目录")
    args = parser.parse_args()
    
    generate_audit_report(args.root, args.output)

if __name__ == "__main__":
    main()