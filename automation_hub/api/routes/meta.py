# -*- coding: utf-8 -*-
"""元信息路由"""

from fastapi import APIRouter
from datetime import datetime
from ..config import settings

router = APIRouter(tags=["meta"])


@router.get("/meta")
async def get_meta():
    """获取服务元信息"""
    return {
        "service_name": "automation-hub",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT
    }


@router.get("/version")
async def get_version():
    return {"version": settings.VERSION}


@router.get("/meta/info")
async def get_meta_info():
    """获取系统元信息"""
    return {
        "project": "Automation Hub",
        "version": "1.0.0",
        "description": "一个面向学习者的入门级 DevOps 实践项目",
        "built_with": ["FastAPI", "Redis", "RQ", "Docker", "SQLite"],
        "timestamp": datetime.now().isoformat()
    }
