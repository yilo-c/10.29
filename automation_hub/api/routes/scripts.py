"""模块说明：脚本管理接口。"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List
import os
import json

from automation_hub.api.db import (
    create_script,
    get_script_by_name,
    get_scripts,
)
from automation_hub.api.auth.deps import require_scope
from automation_hub.api.schemas import Script, ScriptDetail

router = APIRouter(prefix="/scripts", tags=["scripts"])


@router.get("", response_model=List[Script])
def list_scripts(token=Depends(require_scope("tool:read"))):
    """获取所有脚本"""
    scripts = get_scripts()
    return [Script(name=s['name'], description=s['description'], created_at=s['created_at']) for s in scripts]


@router.get("/{script_name}", response_model=ScriptDetail)
def get_script(script_name: str, token=Depends(require_scope("tool:read"))):
    """获取特定脚本详情"""
    script = get_script_by_name(script_name)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    # 从manifest.json获取脚本参数信息
    manifest_path = os.path.join("scripts", "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)

        script_detail = next((s for s in manifest if s['name'] == script_name), None)
        if script_detail:
            return ScriptDetail(
                name=script['name'],
                description=script['description'],
                parameters=script_detail.get('parameters', []),
                created_at=script['created_at']
            )

    # 如果没有在manifest中找到，返回基本信息
    return ScriptDetail(
        name=script['name'],
        description=script['description'],
        parameters=[],
        created_at=script['created_at']
    )


@router.post("")
def register_script(script: Script, token=Depends(require_scope("tool:write"))):
    """注册新脚本"""
    # 检查脚本文件是否存在
    script_path = os.path.join("scripts", script.name)
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail=f"Script file {script.name} does not exist")

    success = create_script(script.name, script.description)
    if not success:
        raise HTTPException(status_code=400, detail="Script already exists")

    return {"message": "Script registered successfully"}