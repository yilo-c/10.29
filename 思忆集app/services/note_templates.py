"""笔记模板系统。

提供：
- 预定义笔记模板（会议记录、日记、任务清单等）
- 自定义模板创建和管理
- 模板变量替换
- 模板导入/导出
- 变量自动检测
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.services.note_templates")


@dataclass
class NoteTemplate:
    """笔记模板。"""
    
    id: str
    name: str
    description: str
    content: str
    category: str = "general"  # general, meeting, journal, task, review
    variables: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    is_builtin: bool = False
    
    def render(self, **kwargs: str) -> str:
        """渲染模板，替换变量。
        
        Args:
            **kwargs: 变量名到值的映射
            
        Returns:
            渲染后的内容
        """
        result = self.content
        for var in self.variables:
            placeholder = f"{{{{{var}}}}}"
            value = kwargs.get(var, f"[{var}]")
            result = result.replace(placeholder, value)
        
        # 替换内置变量
        now = datetime.now()
        builtin_vars = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "datetime": now.strftime("%Y-%m-%d %H:%M"),
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
            "year": now.strftime("%Y"),
            "month": now.strftime("%m"),
            "day": now.strftime("%d"),
            "hour": now.strftime("%H"),
            "minute": now.strftime("%M"),
            "timestamp": str(int(now.timestamp())),
            "iso_datetime": now.isoformat(),
        }
        
        for var_name, var_value in builtin_vars.items():
            result = result.replace(f"{{{{{var_name}}}}}", var_value)
        
        return result
    
    @classmethod
    def detect_variables(cls, content: str) -> list[str]:
        """从模板内容中自动检测变量。
        
        Args:
            content: 模板内容
            
        Returns:
            检测到的变量列表
        """
        pattern = r'\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}'
        matches = re.findall(pattern, content)
        
        # 排除内置变量
        builtin_vars = {
            "date", "time", "datetime", "weekday", 
            "year", "month", "day", "hour", "minute",
            "timestamp", "iso_datetime"
        }
        
        # 去重并保持顺序
        seen = set()
        variables = []
        for var in matches:
            if var not in builtin_vars and var not in seen:
                seen.add(var)
                variables.append(var)
        
        return variables


# ── 内置模板 ──

BUILTIN_TEMPLATES = [
    NoteTemplate(
        id="meeting",
        name="会议记录",
        description="记录会议主题、参与者和决策",
        category="meeting",
        content="""# 会议记录

**日期**: {{date}} {{time}}
**主题**: {{topic}}
**参与者**: {{attendees}}

## 议程

1. 

## 讨论要点

- 

## 决策

- 

## 待办事项

- [ ] 
""",
        variables=["topic", "attendees"],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="journal",
        name="日记",
        description="每日心情和反思",
        category="journal",
        content="""# {{date}} {{weekday}}

## 今日心情

**心情**: {{mood}} 

## 今日要点

- 

## 反思与感悟



## 明日计划

- [ ] 
""",
        variables=["mood"],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="task_list",
        name="任务清单",
        description="按优先级组织的任务列表",
        category="task",
        content="""# 任务清单

**日期**: {{date}}

## 馃敶 紧急重要

- [ ] 

## 馃煛 重要不紧急

- [ ] 

## 馃數 紧急不重要

- [ ] 

## ⚪ 不紧急不重要

- [ ] 

## 已完成

- [x] 
""",
        variables=[],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="weekly_review",
        name="周回顾",
        description="每周工作回顾和计划",
        category="review",
        content="""# 周回顾

**周期**: {{date}}

## 本周完成

### 工作


### 学习


## 本周收获

- 

## 本周不足

- 

## 下周计划

- [ ] 
""",
        variables=[],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="idea",
        name="想法记录",
        description="快速记录灵感和想法",
        category="general",
        content="""# 想法记录

**时间**: {{datetime}}

## 想法

{{idea}}

## 背景



## 下一步

- 
""",
        variables=["idea"],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="bug_report",
        name="Bug 报告",
        description="记录软件缺陷",
        category="task",
        content="""# Bug 报告

**时间**: {{datetime}}
**严重程度**: {{severity}}

## 问题描述



## 复现步骤

1. 
2. 
3. 

## 期望行为



## 实际行为



## 环境信息

- 系统: 
- 版本: 

## 截图/日志

""",
        variables=["severity"],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="learning_note",
        name="学习笔记",
        description="记录学习内容和心得",
        category="general",
        content="""# 学习笔记

**日期**: {{date}}
**主题**: {{topic}}

## 要点

- 

## 代码示例

```
```

## 疑问

- 

## 参考资料

- 
""",
        variables=["topic"],
        is_builtin=True,
    ),
]


class NoteTemplateManager:
    """笔记模板管理器。"""
    
    def __init__(self, data_dir: Path | None = None):
        self._templates: dict[str, NoteTemplate] = {}
        self._data_dir = data_dir
        self._custom_templates_file = data_dir / "note_templates.json" if data_dir else None
        
        # 加载内置模板
        for template in BUILTIN_TEMPLATES:
            self._templates[template.id] = template
        
        # 加载自定义模板
        self._load_custom_templates()
    
    def _load_custom_templates(self) -> None:
        """从文件加载自定义模板。"""
        if not self._custom_templates_file or not self._custom_templates_file.exists():
            return
        
        try:
            data = json.loads(self._custom_templates_file.read_text(encoding="utf-8"))
            for item in data:
                template = NoteTemplate(
                    id=item["id"],
                    name=item["name"],
                    description=item.get("description", ""),
                    content=item["content"],
                    category=item.get("category", "general"),
                    variables=item.get("variables", []),
                    created_at=item.get("created_at", ""),
                    is_builtin=False,
                )
                self._templates[template.id] = template
            logger.info("Loaded %d custom templates", len(data))
        except Exception as e:
            logger.warning("Failed to load custom templates: %s", e)
    
    def _save_custom_templates(self) -> None:
        """保存自定义模板到文件。"""
        if not self._custom_templates_file:
            return
        
        custom = [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "content": t.content,
                "category": t.category,
                "variables": t.variables,
                "created_at": t.created_at,
            }
            for t in self._templates.values()
            if not t.is_builtin
        ]
        
        try:
            self._custom_templates_file.parent.mkdir(parents=True, exist_ok=True)
            self._custom_templates_file.write_text(
                json.dumps(custom, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save custom templates: %s", e)
    
    def get(self, template_id: str) -> NoteTemplate | None:
        """获取模板。"""
        return self._templates.get(template_id)
    
    def list_all(self, category: str | None = None) -> list[NoteTemplate]:
        """列出所有模板。"""
        templates = list(self._templates.values())
        if category:
            templates = [t for t in templates if t.category == category]
        return sorted(templates, key=lambda t: (not t.is_builtin, t.name))
    
    def list_categories(self) -> list[str]:
        """列出所有分类。"""
        return sorted(set(t.category for t in self._templates.values()))
    
    def create(
        self,
        name: str,
        content: str,
        description: str = "",
        category: str = "general",
        variables: list[str] | None = None,
    ) -> NoteTemplate:
        """创建自定义模板。"""
        import hashlib
        
        # 生成唯一 ID
        id_base = f"custom_{name}_{datetime.now().isoformat()}"
        template_id = hashlib.md5(id_base.encode(), usedforsecurity=False).hexdigest()[:12]
        
        template = NoteTemplate(
            id=template_id,
            name=name,
            description=description,
            content=content,
            category=category,
            variables=variables or [],
            is_builtin=False,
        )
        
        self._templates[template_id] = template
        self._save_custom_templates()
        
        logger.info("Created custom template: %s (%s)", name, template_id)
        return template
    
    def update(
        self,
        template_id: str,
        name: str | None = None,
        content: str | None = None,
        description: str | None = None,
        category: str | None = None,
        variables: list[str] | None = None,
    ) -> NoteTemplate | None:
        """更新自定义模板。"""
        template = self._templates.get(template_id)
        if not template:
            return None
        
        if template.is_builtin:
            logger.warning("Cannot modify builtin template: %s", template_id)
            return None
        
        if name is not None:
            template.name = name
        if content is not None:
            template.content = content
        if description is not None:
            template.description = description
        if category is not None:
            template.category = category
        if variables is not None:
            template.variables = variables
        
        self._save_custom_templates()
        return template
    
    def delete(self, template_id: str) -> bool:
        """删除自定义模板。"""
        template = self._templates.get(template_id)
        if not template:
            return False
        
        if template.is_builtin:
            logger.warning("Cannot delete builtin template: %s", template_id)
            return False
        
        del self._templates[template_id]
        self._save_custom_templates()
        
        logger.info("Deleted custom template: %s", template_id)
        return True
    
    def render(self, template_id: str, **kwargs: str) -> str | None:
        """渲染模板。"""
        template = self._templates.get(template_id)
        if not template:
            return None
        return template.render(**kwargs)
    
    def detect_variables(self, content: str) -> list[str]:
        """从模板内容中自动检测变量。"""
        return NoteTemplate.detect_variables(content)
    
    def export_templates(self, template_ids: list[str] | None = None) -> str:
        """导出模板为 JSON 字符串。
        
        Args:
            template_ids: 要导出的模板 ID 列表，None 表示导出所有自定义模板
            
        Returns:
            JSON 字符串
        """
        if template_ids is None:
            templates = [t for t in self._templates.values() if not t.is_builtin]
        else:
            templates = [self._templates[tid] for tid in template_ids if tid in self._templates]
        
        data = {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "templates": [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "content": t.content,
                    "category": t.category,
                    "variables": t.variables,
                }
                for t in templates
            ],
        }
        
        return json.dumps(data, ensure_ascii=False, indent=2)
    
    def import_templates(self, json_str: str, overwrite: bool = False) -> int:
        """从 JSON 字符串导入模板。
        
        Args:
            json_str: JSON 字符串
            overwrite: 是否覆盖已存在的模板
            
        Returns:
            导入的模板数量
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON: %s", e)
            return 0
        
        templates_data = data.get("templates", [])
        imported = 0
        
        for item in templates_data:
            template_id = item.get("id", "")
            if not template_id:
                continue
            
            if template_id in self._templates and not overwrite:
                template_id = f"{template_id}_imported_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            template = NoteTemplate(
                id=template_id,
                name=item.get("name", "未命名"),
                description=item.get("description", ""),
                content=item.get("content", ""),
                category=item.get("category", "general"),
                variables=item.get("variables", []),
                is_builtin=False,
            )
            
            if not template.variables:
                template.variables = self.detect_variables(template.content)
            
            self._templates[template.id] = template
            imported += 1
        
        if imported > 0:
            self._save_custom_templates()
            logger.info("Imported %d templates", imported)
        
        return imported
    
    def duplicate(self, template_id: str, new_name: str | None = None) -> NoteTemplate | None:
        """复制模板。"""
        source = self._templates.get(template_id)
        if not source:
            return None
        
        import hashlib
        
        id_base = f"duplicate_{source.name}_{datetime.now().isoformat()}"
        new_id = hashlib.md5(id_base.encode(), usedforsecurity=False).hexdigest()[:12]
        
        new_template = NoteTemplate(
            id=new_id,
            name=new_name or f"{source.name}(副本)",
            description=source.description,
            content=source.content,
            category=source.category,
            variables=source.variables.copy(),
            is_builtin=False,
        )
        
        self._templates[new_id] = new_template
        self._save_custom_templates()
        
        logger.info("Duplicated template: %s -> %s", template_id, new_id)
        return new_template
    
    def search(self, query: str) -> list[NoteTemplate]:
        """搜索模板。"""
        query_lower = query.lower()
        results = []
        
        for template in self._templates.values():
            if (query_lower in template.name.lower() or
                query_lower in template.description.lower() or
                query_lower in template.category.lower()):
                results.append(template)
        
        return sorted(results, key=lambda t: (not t.is_builtin, t.name))


# ── 额外实用模板 ──

EXTRA_TEMPLATES = [
    NoteTemplate(
        id="daily_plan",
        name="每日计划",
        description="规划一天的工作和目标",
        category="task",
        content="""# 每日计划

**日期**: {{date}} {{weekday}}

## 馃幆 今日目标

1. 
2. 
3. 

## 馃搵 待办事项

### 上午
- [ ] 
- [ ] 

### 下午
- [ ] 
- [ ] 

### 晚上
- [ ] 

## 馃摑 备注


""",
        variables=[],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="project_readme",
        name="项目 README",
        description="项目文档模板",
        category="general",
        content="""# {{project_name}}

> {{description}}

## 功能特性

- 
- 
- 

## 快速开始

### 安装

```bash
```

### 使用

```bash
```

## 项目结构

```
```

## 开发

### 环境要求

- 

### 安装依赖

```bash
```

### 运行

```bash
```

## 许可证

{{license}}
""",
        variables=["project_name", "description", "license"],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="email_draft",
        name="邮件草稿",
        description="正式邮件模板",
        category="general",
        content="""# 邮件草稿

**收件人**: {{recipient}}
**主题**: {{subject}}
**日期**: {{date}}

---

{{salutation}}，

{{body}}

{{closing}}

{{signature}}
""",
        variables=["recipient", "subject", "salutation", "body", "closing", "signature"],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="interview_notes",
        name="面试记录",
        description="面试评估记录",
        category="meeting",
        content="""# 面试记录

**日期**: {{date}} {{time}}
**候选人**: {{candidate}}
**职位**: {{position}}
**面试官**: {{interviewer}}

## 基本信息

- 工作年限: 
- 技术栈: 
- 期望薪资: 

## 技术评估

### 专业技能

| 技能 | 评分(1-5) | 备注 |
|------|-----------|------|
|      |           |      |

### 项目经验


### 编码能力


## 软技能

- 沟通能力: 
- 团队合作: 
- 学习能力: 

## 综合评价


## 录用建议

- [ ] 强烈推荐
- [ ] 推荐
- [ ] 待定
- [ ] 不推荐

## 备注


""",
        variables=["candidate", "position", "interviewer"],
        is_builtin=True,
    ),
    
    NoteTemplate(
        id="retrospective",
        name="复盘总结",
        description="项目或事件复盘",
        category="review",
        content="""# 复盘总结

**日期**: {{date}}
**主题**: {{topic}}

## 馃搳 目标回顾

### 原定目标

1. 
2. 

### 实际结果

1. 
2. 

## ✅ 做得好的

- 
- 

## ❌ 需要改进的

- 
- 

## 馃挕 经验教训

### 继续保持


### 需要改变


### 开始尝试


## 馃摑 行动计划

- [ ] 
- [ ] 
""",
        variables=["topic"],
        is_builtin=True,
    ),
]

# 将额外模板添加到内置模板列表
BUILTIN_TEMPLATES.extend(EXTRA_TEMPLATES)


# 全局实例
_manager: NoteTemplateManager | None = None


def get_template_manager(data_dir: Path | None = None) -> NoteTemplateManager:
    """获取全局模板管理器。"""
    global _manager
    if _manager is None:
        _manager = NoteTemplateManager(data_dir)
    return _manager