"""FastAPI entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.business import router as business_api_router
from app.api.factory import router as factory_router
from app.api.production import router as production_api_router
from app.api.publishing import router as publishing_router
from app.api.research import router as research_api_router
from app.api.scripts import router as scripts_router
from app.api.topics import router as topics_router
from app.api.videos import router as videos_router
from app.config import get_settings
from app.core.brand_worker import start_brand_worker
from app.core.database import init_db, session_scope
from app.core.errors import AppError
from app.core.jobs import mark_interrupted
from app.core.logging import configure_logging
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.seed import seed
from app.services.business.cycle import mark_cycles_interrupted
from app.services.production import mark_production_interrupted
from app.web.business import router as business_router
from app.web.production import router as production_router
from app.web.research import router as research_router
from app.web.views import router as web_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    with session_scope() as session:
        changed = mark_interrupted(session)
        interrupted_cycles = mark_cycles_interrupted(session)
        interrupted_jobs = mark_production_interrupted(session)
        seed(session)
        if changed or interrupted_cycles or interrupted_jobs:
            session.commit()
    start_scheduler()
    start_brand_worker()
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
        name="static",
    )
    app.include_router(web_router)
    app.include_router(business_router)
    app.include_router(production_router)
    app.include_router(research_router)
    app.include_router(topics_router)
    app.include_router(scripts_router)
    app.include_router(videos_router)
    app.include_router(publishing_router)
    app.include_router(research_api_router)
    app.include_router(business_api_router)
    app.include_router(factory_router)
    app.include_router(production_api_router)

    @app.get("/health")
    def health() -> dict:
        current = get_settings()
        return {
            "status": "ok",
            "tts_provider": current.tts_provider,
            "openai": current.openai_enabled(),
            "elevenlabs": current.elevenlabs_enabled(),
        }

    @app.exception_handler(AppError)
    async def handle_app_error(_request, exc: AppError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    return app


app = create_app()
