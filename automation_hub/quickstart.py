#!/usr/bin/env python
"""快速启动脚本。

初始化数据库并输出当前版本的推荐启动与引导步骤。
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent


def run_command(cmd: list[str], description: str) -> tuple[int, str]:
    """运行命令并返回结果。"""
    print(f"\n{'='*60}")
    print(f"▶️  {description}")
    print(f"{'='*60}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Success")
        if result.stdout:
            print(result.stdout)
    else:
        print("❌ Failed")
        if result.stderr:
            print(result.stderr)
    
    return result.returncode, result.stdout


def main():
    """主函数。"""
    print("""
╔══════════════════════════════════════════════════════════╗
║  Automation Hub - 快速启动                                ║
║  AI 工具助手底座                                           ║
╚══════════════════════════════════════════════════════════╝
""")
    
    # 步骤 1: 数据库迁移
    print("\n📦 步骤 1/3: 执行数据库初始化")
    code, output = run_command(
        [sys.executable, "api/db/migrate.py"],
        "Initializing database schema"
    )
    
    if code != 0:
        print("\n❌ 数据库迁移失败，请检查错误信息")
        sys.exit(1)
    
    # 步骤 2: 检查是否已初始化
    print("\n🔍 步骤 2/3: 检查系统引导状态")
    
    # 简单检查：查看是否有 token 文件
    token_file = PROJECT_ROOT / ".admin_token"
    
    if token_file.exists():
        print("✅ 系统已初始化")
        token = token_file.read_text().strip()
        print(f"\n管理员 Token: {token}")
    else:
        print("⚠️  系统尚未初始化")
        print("\n请手动执行初始化：")
        print("""
1. 启动 API 服务：
   uvicorn api.main:app --reload

2. 在另一个终端执行 bootstrap：
   curl -X POST http://localhost:8000/auth/bootstrap \\
     -H "Content-Type: application/json" \\
     -d '{
       "admin_name": "Admin",
       "device_name": "Development",
             "device_platform": "windows"
     }'

3. 保存返回的 token 到 .admin_token 文件
""")
    
    # 步骤 3: 显示下一步操作
    print("\n📋 步骤 3/3: 下一步操作")
    print("""
✨ 数据库已就绪！

🚀 启动服务：

# 启动 API 服务
cd automation_hub
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 启动 Worker（另一个终端）
cd automation_hub
python -m worker.worker

📚 快速测试：

# 创建测试工具
curl -X POST http://localhost:8000/tools \\
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "id": "hello_world",
    "name": "Hello World",
    "description": "测试工具",
    "risk_level": "read",
    "executor": "host",
    "command": ["echo", "Hello from Automation Hub!"],
    "args_schema": {},
    "timeout_sec": 10
  }'

# 执行工具
curl -X POST http://localhost:8000/tool-runs \
  -H "Authorization: Bearer YOUR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "tool_id": "hello_world",
    "args": {}
  }'

📖 更多信息：
- README: automation_hub/README.md
- 部署检查清单: automation_hub/DEPLOYMENT_CHECKLIST.md
- 工具规范: automation_hub/docs/tool-spec.md
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 已取消")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)
