from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from systutor.api.v1.core import router as core_router
from systutor.api.v1.system import router as system_router
from systutor.core.config import Settings, get_settings
from systutor.core.errors import register_exception_handlers
from systutor.core.lifecycle import bootstrap_app_state, lifespan
from systutor.core.logging import configure_logging
from systutor.core.request_context import RequestContextMiddleware
from systutor.kernel.auth.router import router as auth_router


def create_app(settings: Settings | None = None) -> FastAPI:
    effective_settings = settings or get_settings()
    configure_logging(effective_settings.log_level)

    app = FastAPI(
        title=effective_settings.app_name,
        version=effective_settings.version,
        debug=effective_settings.debug,
        lifespan=lifespan,
    )
    bootstrap_app_state(app, effective_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=effective_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)

    api_router = APIRouter()
    api_router.include_router(auth_router)
    api_router.include_router(core_router)
    api_router.include_router(system_router)
    app.include_router(api_router, prefix=effective_settings.api_prefix)
    register_exception_handlers(app)

    import os
    static_dir = os.environ.get("SYSTUTOR_STATIC_DIR", "")
    if static_dir and os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
