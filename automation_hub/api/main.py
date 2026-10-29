import logging
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from .artifacts.service import start_artifact_cleaner
from .approvals.service import start_approval_expirer
from .config import settings
from . import db
from .log_config import setup_logging

logger = logging.getLogger("automation_hub.api")

# 内置路由
from .routes import health, meta, scripts, tasks, runs
from .routes.tool_runs import router as tool_runs_router
from .routes.metrics import router as metrics_router
from .routes.agent import router as agent_router
from .routes.agent_nodes import router as agent_nodes_router
from .routes.execution_authorizations import router as execution_authorizations_router
from .routes.personal_news import router as personal_news_router
from .routes.personal_notes import router as personal_notes_router
from .routes.personal_tasks import router as personal_tasks_router
from .routes.personal_translations import router as personal_translations_router
from .routes.personal_wordbooks import router as personal_wordbooks_router
from .routes.personal_words import router as personal_words_router
from .routes.intel import router as intel_router
from .routes.sync import router as sync_router
from .routes.mobile import router as mobile_router
from .routes.mobile_ws import router as mobile_ws_router
from .routes.mcp import router as mcp_router
from .routes.web_search import router as web_search_router
from .routes.plugins import router as plugins_router
from .routes.knowledge_base import router as knowledge_base_router
from .routes.cron import router as cron_router
from .routes.workflows import router as workflows_router
from .routes.evolution import router as evolution_router
from .proposals.router import router as proposals_router
from .routes.sessions import router as sessions_router

# Static files for mobile PWA + web SPA
MOBILE_APP_DIR = Path(__file__).resolve().parent.parent / "mobile_app"
_has_mobile_static = MOBILE_APP_DIR.exists()

WEB_DIST_DIR = Path(__file__).resolve().parent.parent.parent / "思忆集app" / "web_dist"
_has_web_dist = WEB_DIST_DIR.exists()

# 领域路由
from .auth.router import router as auth_router
from .security.routes import router as security_router
from .tools.router import router as tools_router
from .approvals.router import router as approvals_router
from .audit.router import router as audit_router
from .repos.router import router as repos_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 初始化结构化 JSON 日志
    setup_logging()
    logger.info("Structured JSON logging initialized", extra={"component": "logging"})

    # 启动后台服务
    artifact_cleaner_stop = Event()
    approval_expirer_stop = Event()

    logger.info("Starting background services", extra={"component": "startup", "services": ["artifact_cleaner", "approval_expirer"]})
    start_artifact_cleaner(artifact_cleaner_stop)
    start_approval_expirer(approval_expirer_stop)

    yield

    # 停止后台服务
    logger.info("Shutting down background services", extra={"component": "shutdown"})
    artifact_cleaner_stop.set()
    approval_expirer_stop.set()


app = FastAPI(
    title="Automation Hub API",
    description="思忆集自动化底座后端服务",
    version="0.1.0",
    lifespan=lifespan
)

# 注册中间件（CORS 在最外层，确保 preflight 不被内部中间件拦截）
from .middleware.logging import request_logging_middleware
app.middleware("http")(request_logging_middleware)

from .middleware.scope import scope_enforcement_middleware
app.middleware("http")(scope_enforcement_middleware)

# CORS 配置 — 从环境变量读取允许的来源
# 留空时默认允许 localhost 常见端口；设为 "*" 时强制关闭 credentials（浏览器规范禁止 * + credentials）
_raw_origins = settings.CORS_ORIGINS.strip()
if _raw_origins == "*":
    _cors_origins = ["*"]
    _cors_credentials = False  # * 与 credentials 互斥
elif _raw_origins:
    _cors_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
    _cors_credentials = True
else:
    _cors_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ]
    _cors_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router)
app.include_router(meta.router)
app.include_router(scripts.router)
app.include_router(tasks.router)
app.include_router(runs.router, prefix="/runs")
app.include_router(tool_runs_router)
app.include_router(metrics_router)

# auth/tools/approvals
app.include_router(auth_router)
app.include_router(security_router)
app.include_router(tools_router)
app.include_router(approvals_router)
app.include_router(audit_router)
app.include_router(proposals_router)
app.include_router(repos_router)
app.include_router(agent_router)
app.include_router(agent_nodes_router)
app.include_router(execution_authorizations_router)
app.include_router(personal_news_router)
app.include_router(personal_notes_router)
app.include_router(personal_tasks_router)
app.include_router(personal_translations_router)
app.include_router(personal_wordbooks_router)
app.include_router(personal_words_router)
app.include_router(intel_router)
app.include_router(sync_router)
app.include_router(mcp_router)
app.include_router(web_search_router)
app.include_router(plugins_router)
app.include_router(knowledge_base_router)
app.include_router(cron_router)
app.include_router(workflows_router)
app.include_router(evolution_router)

# Cross-device session continuation
app.include_router(sessions_router)

# Mobile companion API + PWA static files
app.include_router(mobile_router)
app.include_router(mobile_ws_router)

if _has_mobile_static:
    app.mount("/mobile/static", StaticFiles(directory=str(MOBILE_APP_DIR)), name="mobile_static")

    @app.get("/mobile/{full_path:path}")
    async def serve_mobile_app(full_path: str):
        """SPA catch-all: serve index.html for all unmatched /mobile/* routes."""
        # FastAPI matches specific routes (pairing/, capture, dashboard, static) first.
        # This catch-all only fires for unknown paths → serve SPA shell for client-side routing.
        if full_path.startswith("static/"):
            return Response(status_code=404)
        return FileResponse(MOBILE_APP_DIR / "index.html")


# Web SPA — main desk-notes UI
if _has_web_dist:
    app.mount("/app/assets", StaticFiles(directory=str(WEB_DIST_DIR / "assets")), name="web_spa_assets")

    @app.get("/app/{full_path:path}")
    async def serve_web_spa(full_path: str):
        """SPA catch-all: serve index.html for all /app/* routes (client-side routing)."""
        if full_path.startswith("assets/"):
            return Response(status_code=404)
        return FileResponse(WEB_DIST_DIR / "index.html")

    @app.get("/app")
    async def serve_web_spa_root():
        return FileResponse(WEB_DIST_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)