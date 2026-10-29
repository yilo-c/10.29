# -*- coding: utf-8 -*-
"""简单的personal_tasks路由功能验证"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# 验证路由模块是否可以导入
try:
    from automation_hub.api.routes import personal_tasks
    print("✅ personal_tasks路由模块导入成功")
    print(f"Router: {personal_tasks.router}")
except ImportError as e:
    print(f"❌ personal_tasks路由模块导入失败: {e}")
    sys.exit(1)

# 验证db函数是否可以导入（从api.db而不是api.db包）
try:
    from automation_hub.api import db
    # 检查函数是否存在
    if hasattr(db, 'list_personal_tasks') and hasattr(db, 'upsert_personal_task') and hasattr(db, 'delete_personal_tasks'):
        print("✅ personal_tasks DB函数存在")
    else:
        print("⚠️ personal_tasks DB函数不存在")
except ImportError as e:
    print(f"❌ personal_tasks DB模块导入失败: {e}")

print("Personal tasks route verification completed! 🎉")