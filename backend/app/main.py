from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.admin_apps import router as admin_apps_router
from app.api.apps import router as apps_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.api.me import router as me_router
from app.api.signup import router as signup_router
from app.config import get_settings
from app.logging_config import configure_logging
from app.privacy_gateway.pipeline import get_pipeline

settings = get_settings()

# LOG_LEVEL was previously read into Settings but nothing configured Python's
# logging module with it, so app.*.info()/warning() calls (the privacy
# pipeline's step-by-step decision log) were silently dropped. This makes the
# setting do what its name says. Colorized outside production: docker logs
# renders the ANSI codes fine, but a production log aggregator would just show
# the raw escape bytes as noise.
configure_logging(settings.log_level, colorize=settings.environment != "production")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Design spec §6: the bundled ORDO OWL and Krankenhausverzeichnis xlsx are parsed
    # once, at startup, into in-memory sets — never per request. The spaCy model is
    # loaded here too, so the first real request does not pay for it.
    if settings.warm_reference_data_on_startup:
        get_pipeline()
    yield


app = FastAPI(
    title="Privacy-First Medical LLM Gateway",
    debug=settings.debug,
    lifespan=lifespan,
)

if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health_router)
app.include_router(me_router)
app.include_router(apps_router)
app.include_router(admin_router)
app.include_router(admin_apps_router)
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(signup_router)
