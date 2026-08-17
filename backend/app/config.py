from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
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

    # Backend-chat-slice design doc §2: two separate Keycloak URLs because inside
    # Docker Compose the backend reaches Keycloak via the service name, while the
    # browser (and therefore the token's `iss` claim) uses localhost.
    keycloak_issuer_url: str = "http://localhost:8080/realms/chatgpt-proxy-dev"
    keycloak_jwks_url: str = (
        "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/certs"
    )
    keycloak_audience: str = "chatgpt-proxy-frontend"

    # ADR-0016 / ADR-0022: both providers ship in the MVP.
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    @model_validator(mode="after")
    def _require_openai_key_when_selected(self) -> "Settings":
        # ADR-0020: fail closed on misconfiguration at startup, not at first request.
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai"
            )
        return self

    @property
    def app_database_url(self) -> str:
        url = make_url(self.database_url).set(
            username="app_runtime", password=self.app_runtime_password
        )
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
