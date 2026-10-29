#!/usr/bin/env python
"""工具迁移脚本示例。

将现有的 scripts/manifest.json 迁移到工具注册系统。
"""

import json
import sys
from pathlib import Path
import requests

# 配置
API_BASE_URL = "http://localhost:8000"
TOKEN_FILE = Path(__file__).parent / ".admin_token"
MANIFEST_FILE = Path(__file__).parent / "scripts" / "manifest.json"


def load_token() -> str:
    """加载管理员 token。"""
    if not TOKEN_FILE.exists():
        print("❌ Token 文件不存在，请先初始化系统")
        print(f"   期望文件: {TOKEN_FILE}")
        sys.exit(1)
    
    return TOKEN_FILE.read_text().strip()


def load_manifest() -> dict:
    """加载现有的脚本清单。"""
    if not MANIFEST_FILE.exists():
        print(f"⚠️  清单文件不存在: {MANIFEST_FILE}")
        return {}
    
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        return json.load(f)


def migrate_script_to_tool(script_id: str, script_config: dict, token: str) -> bool:
    """将单个脚本迁移为工具。
    
    Args:
        script_id: 脚本 ID
        script_config: 脚本配置
        token: API token
        
    Returns:
        是否成功
    """
    # 从脚本配置推断工具配置
    # 这是一个简化示例，实际需要根据你的 manifest 结构调整
    
    tool_spec = {
        "id": script_id,
        "name": script_config.get("name", script_id.replace("_", " ").title()),
        "description": script_config.get("description", ""),
        "risk_level": script_config.get("risk_level", "exec_low"),
        "executor": "docker",
        "command": parse_command(script_config.get("cmd", "")),
        "cwd": script_config.get("cwd"),
        "timeout_sec": script_config.get("timeout", 120),
        "allowed_paths": script_config.get("allowed_paths", []),
        "args_schema": script_config.get("args_schema", {}),
        "is_enabled": True
    }
    
    # 调用 API 注册工具
    response = requests.post(
        f"{API_BASE_URL}/tools",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json=tool_spec
    )
    
    if response.status_code in (200, 201):
        print(f"✅ 迁移成功: {script_id}")
        return True
    else:
        print(f"❌ 迁移失败: {script_id}")
        print(f"   状态码: {response.status_code}")
        print(f"   响应: {response.text}")
        return False


def parse_command(cmd_string: str) -> list[str]:
    """解析命令字符串为命令数组。
    
    简化实现，实际应使用 shlex.split
    """
    if not cmd_string:
        return ["echo", "No command specified"]
    
    # 简单分割（不处理引号等）
    return cmd_string.split()


def main():
    """主函数。"""
    print("""
╔══════════════════════════════════════════════════════════╗
║  工具迁移脚本                                              ║
║  Scripts Manifest → Tools Registry                       ║
╚══════════════════════════════════════════════════════════╝
""")
    
    # 加载 token
    token = load_token()
    print(f"✅ Token 已加载")
    
    # 加载清单
    manifest = load_manifest()
    
    if not manifest:
        print("\n⚠️  没有找到需要迁移的脚本")
        print("\n这是一个示例脚本，请根据你的 manifest.json 结构进行调整")
        return
    
    print(f"\n📋 发现 {len(manifest)} 个脚本需要迁移")
    
    # 迁移每个脚本
    success_count = 0
    fail_count = 0
    
    for script_id, script_config in manifest.items():
        print(f"\n▶️  迁移: {script_id}")
        
        if migrate_script_to_tool(script_id, script_config, token):
            success_count += 1
        else:
            fail_count += 1
    
    # 显示结果
    print(f"\n{'='*60}")
    print(f"✅ 成功: {success_count}")
    print(f"❌ 失败: {fail_count}")
    print(f"{'='*60}")
    
    if fail_count == 0:
        print("\n🎉 所有脚本迁移成功！")
        print("\n下一步：")
        print("1. 验证工具列表: curl http://localhost:8000/tools -H 'Authorization: Bearer YOUR_TOKEN'")
        print("2. 测试执行工具")
        print("3. 逐步废弃旧的 manifest.json")
    else:
        print("\n⚠️  部分脚本迁移失败，请检查错误信息")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 已取消")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
