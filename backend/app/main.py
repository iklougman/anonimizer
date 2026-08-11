from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(title="Privacy-First Medical LLM Gateway", debug=settings.debug)
app.include_router(health_router)
