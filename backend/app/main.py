import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.config import get_settings
from app.privacy_gateway.pipeline import get_pipeline

settings = get_settings()

# LOG_LEVEL was previously read into Settings but nothing configured Python's
# logging module with it, so app.*.info()/warning() calls (the privacy
# pipeline's step-by-step decision log) were silently dropped. This makes the
# setting do what its name says.
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


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
app.include_router(conversations_router)
app.include_router(chat_router)
