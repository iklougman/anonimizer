from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

# Design spec §2: "PATIENT_NUMBER — configurable numeric ID pattern." The default
# covers the label forms German clinical notes actually use. The optional named group
# `value` marks the part of the match that is the identifier itself, so the token
# replaces only the digits and the surrounding label stays readable for the LLM.
DEFAULT_PATIENT_NUMBER_PATTERN = (
    r"(?:Patientennummer|Patienten-Nr\.|Pat\.-Nr\.|Fallnummer|Fallnr\.)"
    r"\s*:?\s*(?P<value>\d{6,10})"
)


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"]
    log_level: str = "INFO"
    debug: bool = False
    cors_allowed_origins: list[str] = Field(default_factory=list)
    database_url: str
    master_key_path: str
    app_runtime_password: str
    patient_number_pattern: str = DEFAULT_PATIENT_NUMBER_PATTERN
    # Parsing the bundled ORDO OWL + Krankenhausverzeichnis xlsx at startup (design
    # spec §6) costs minutes and gigabytes; the test suite injects small in-memory
    # reference sets instead, so it turns the warm-up off.
    warm_reference_data_on_startup: bool = True

    @property
    def app_database_url(self) -> str:
        url = make_url(self.database_url).set(
            username="app_runtime", password=self.app_runtime_password
        )
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
