"""
工具注册脚本

批量注册常用工具到数据库
"""

import sqlite3
import json
from datetime import datetime
import uuid


DB_PATH = "data/automation_hub.sqlite3"


def get_db():
    """获取数据库连接"""
    return sqlite3.connect(DB_PATH)


def register_tool(conn, tool_data):
    """注册单个工具"""
    cursor = conn.cursor()
    
    # 检查是否已存在
    cursor.execute("SELECT id FROM tools WHERE id = ?", (tool_data["id"],))
    exists = cursor.fetchone()
    
    if exists:
        print(f"⚠️  工具已存在，跳过: {tool_data['name']}")
        return
    
    # 插入工具
    cursor.execute("""
        INSERT INTO tools 
        (id, name, description, risk_level, executor, command_json, 
         args_schema_json, allowed_paths_json, timeout_seconds, enabled, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tool_data["id"],
        tool_data["name"],
        tool_data.get("description", ""),
        tool_data["risk_level"],
        tool_data.get("executor", "host"),
        json.dumps(tool_data["command"]),
        json.dumps(tool_data.get("args_schema", {})),
        json.dumps(tool_data.get("allowed_paths", [])),
        tool_data.get("timeout_seconds", 60),
        tool_data.get("enabled", 1),
        datetime.utcnow().isoformat()
    ))
    
    # 创建版本记录
    cursor.execute("""
        INSERT INTO tool_versions (tool_id, version, command_json, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        tool_data["id"],
        tool_data.get("version", "1.0.0"),
        json.dumps(tool_data["command"]),
        datetime.utcnow().isoformat()
    ))
    
    conn.commit()
    print(f"✅ 已注册工具: {tool_data['name']}")


# ==================== 工具定义 ====================

# 1. 代码搜索工具（ripgrep）
CODE_SEARCH_TOOL = {
    "id": "code_search",
    "name": "代码搜索",
    "description": "使用ripgrep在代码仓库中搜索文本模式，支持正则表达式",
    "risk_level": "read",
    "executor": "host",
    "command": ["rg", "--json", "--ignore-case"],
    "args_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "搜索模式（支持正则表达式）"
            },
            "path": {
                "type": "string",
                "description": "搜索路径",
                "default": "."
            },
            "file_type": {
                "type": "string",
                "description": "文件类型（如: py, js, ts）"
            }
        },
        "required": ["pattern"]
    },
    "timeout_seconds": 30
}

# 2. 文件列表工具
LIST_FILES_TOOL = {
    "id": "list_files",
    "name": "文件列表",
    "description": "列出目录下的文件",
    "risk_level": "read",
    "executor": "host",
    "command": ["find"],
    "args_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径",
                "default": "."
            },
            "pattern": {
                "type": "string",
                "description": "文件名模式（如: *.py）"
            },
            "max_depth": {
                "type": "integer",
                "description": "最大目录深度",
                "default": 5
            }
        }
    },
    "timeout_seconds": 10
}

# 3. Git状态工具
GIT_STATUS_TOOL = {
    "id": "git_status",
    "name": "Git状态",
    "description": "查看Git仓库的文件变更状态",
    "risk_level": "read",
    "executor": "host",
    "command": ["git", "status", "--porcelain"],
    "args_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "仓库路径",
                "default": "."
            }
        }
    },
    "timeout_seconds": 5
}

# 4. Git差异工具
GIT_DIFF_TOOL = {
    "id": "git_diff",
    "name": "Git差异",
    "description": "查看Git文件变更详情",
    "risk_level": "read",
    "executor": "host",
    "command": ["git", "diff"],
    "args_schema": {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "文件路径（可选）"
            },
            "cached": {
                "type": "boolean",
                "description": "查看暂存区差异",
                "default": False
            }
        }
    },
    "timeout_seconds": 10
}

# 5. Git日志工具
GIT_LOG_TOOL = {
    "id": "git_log",
    "name": "Git日志",
    "description": "查看Git提交历史",
    "risk_level": "read",
    "executor": "host",
    "command": ["git", "log", "--oneline"],
    "args_schema": {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "显示数量",
                "default": 10
            },
            "author": {
                "type": "string",
                "description": "作者筛选"
            }
        }
    },
    "timeout_seconds": 5
}

# 6. Python测试工具
PYTEST_TOOL = {
    "id": "run_pytest",
    "name": "运行Python测试",
    "description": "使用pytest运行Python测试",
    "risk_level": "exec_low",
    "executor": "host",
    "command": ["pytest", "-v"],
    "args_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "测试文件或目录路径",
                "default": "tests/"
            },
            "markers": {
                "type": "string",
                "description": "pytest markers (如: -m unit)"
            }
        }
    },
    "timeout_seconds": 300
}

# 7. Python Lint工具
RUFF_CHECK_TOOL = {
    "id": "lint_python",
    "name": "Python代码检查",
    "description": "使用ruff检查Python代码质量",
    "risk_level": "read",
    "executor": "host",
    "command": ["ruff", "check"],
    "args_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "检查路径",
                "default": "."
            },
            "fix": {
                "type": "boolean",
                "description": "自动修复",
                "default": False
            }
        }
    },
    "timeout_seconds": 60
}

# 8. Python格式化工具
RUFF_FORMAT_TOOL = {
    "id": "format_python",
    "name": "Python代码格式化",
    "description": "使用ruff格式化Python代码",
    "risk_level": "write",
    "executor": "host",
    "command": ["ruff", "format"],
    "args_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "格式化路径",
                "default": "."
            }
        },
        "required": ["path"]
    },
    "timeout_seconds": 60
}

# 9. 文件读取工具
READ_FILE_TOOL = {
    "id": "read_file",
    "name": "读取文件",
    "description": "读取文件内容",
    "risk_level": "read",
    "executor": "host",
    "command": ["cat"],
    "args_schema": {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "文件路径"
            },
            "lines": {
                "type": "string",
                "description": "行范围（如: 1-10）"
            }
        },
        "required": ["file"]
    },
    "timeout_seconds": 5
}

# 10. 目录大小统计
DU_TOOL = {
    "id": "disk_usage",
    "name": "目录大小",
    "description": "统计目录占用空间",
    "risk_level": "read",
    "executor": "host",
    "command": ["du", "-sh"],
    "args_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径",
                "default": "."
            }
        }
    },
    "timeout_seconds": 30
}


# 所有工具列表
ALL_TOOLS = [
    CODE_SEARCH_TOOL,
    LIST_FILES_TOOL,
    GIT_STATUS_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    PYTEST_TOOL,
    RUFF_CHECK_TOOL,
    RUFF_FORMAT_TOOL,
    READ_FILE_TOOL,
    DU_TOOL
]


def main():
    """主函数"""
    print("=" * 60)
    print("Automation Hub - 工具注册脚本")
    print("=" * 60)
    
    conn = get_db()
    
    print(f"\n将注册 {len(ALL_TOOLS)} 个工具到数据库...")
    print()
    
    for tool in ALL_TOOLS:
        register_tool(conn, tool)
    
    conn.close()
    
    print()
    print("=" * 60)
    print("✅ 工具注册完成！")
    print()
    print("使用以下命令查看已注册的工具:")
    print("  python automation-hub/cli.py tools list")
    print()
    print("执行工具示例:")
    print("  python automation-hub/cli.py run code_search --args '{\"pattern\": \"TODO\"}'")
    print("=" * 60)


if __name__ == "__main__":
    main()
