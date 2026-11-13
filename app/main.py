from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker

from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.api.v1 import router as v1_router
from app.infrastructure.database.engine import create_db_engine
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_db_engine(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="Ortelius", version="0.1.0", lifespan=lifespan)
    app.state.session_factory = sessionmaker(engine)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(v1_router)
    return app
