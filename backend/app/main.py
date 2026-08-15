from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings
from app.privacy_gateway.pipeline import get_pipeline

settings = get_settings()


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
app.include_router(health_router)
