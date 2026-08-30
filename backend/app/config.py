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
    # DB identity for the internal-only Django ops-admin service (migration
    # 0007 provisions the `app_ops` role with this password) -- read here too
    # since the migration environment imports Settings the same way 0003 does
    # for app_runtime_password.
    app_ops_password: str
    patient_number_pattern: str = DEFAULT_PATIENT_NUMBER_PATTERN

    # Explicit, env-tunable pool sizing rather than SQLAlchemy's accidental
    # defaults (pool_size=5, max_overflow=10). Each uvicorn worker process gets
    # its own engine/pool (app/db/session.py's create_engine() is module-level,
    # re-instantiated per process), so the real ceiling on Postgres connections
    # from this service is `--workers * (db_pool_size + db_max_overflow)` --
    # see docker-compose.yml's postgres `max_connections` override, sized with
    # this in mind.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    # Parsing the bundled ORDO OWL + Krankenhausverzeichnis xlsx at startup (design
    # spec §6) costs minutes and gigabytes; the test suite injects small in-memory
    # reference sets instead, so it turns the warm-up off.
    warm_reference_data_on_startup: bool = True

    # Public, unauthenticated endpoint (signup) abuse guard -- see
    # app/api/rate_limit.py's docstring for the accepted MVP limitation.
    signup_rate_limit_per_hour: int = 5

    # Backend-chat-slice design doc §2: two separate Keycloak URLs because inside
    # Docker Compose the backend reaches Keycloak via the service name, while the
    # browser (and therefore the token's `iss` claim) uses localhost.
    keycloak_issuer_url: str = "http://localhost:8080/realms/chatgpt-proxy-dev"
    keycloak_jwks_url: str = (
        "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/certs"
    )
    keycloak_audience: str = "chatgpt-proxy-frontend"

    # Optional: user provisioning via POST /api/admin/users falls back to
    # "link an existing Keycloak subject" mode when these are unset, so a
    # missing admin client degrades a feature rather than blocking startup.
    keycloak_admin_base_url: str = "http://keycloak:8080"
    keycloak_admin_realm: str = "chatgpt-proxy-dev"
    keycloak_admin_client_id: str | None = None
    keycloak_admin_client_secret: str | None = None

    # ADR-0016 / ADR-0022: both providers ship in the MVP.
    llm_provider: Literal["ollama", "openai", "stub"] = "ollama"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-nano"

    # Debug-only override (ADR-0020): when false, the deanonymize step skips the
    # post-LLM leakage scan and returns the LLM's raw completion (with tokens
    # still resolved) so an operator can inspect what the model actually
    # produced. The outbound sanitize step and assert_no_raw_pii are
    # unaffected -- only the response guard is bypassed. Must remain true in
    # any non-dev environment: a false value returns unverified text that may
    # contain real PII the LLM hallucinated.
    output_guard_enabled: bool = True

    @model_validator(mode="after")
    def _require_openai_key_when_selected(self) -> "Settings":
        # ADR-0020: fail closed on misconfiguration at startup, not at first request.
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai"
            )
        return self

    @model_validator(mode="after")
    def _forbid_stub_provider_in_production(self) -> "Settings":
        # ADR-0020: fail closed at startup, not at first request. The stub
        # provider exists for deterministic/fast e2e and CI runs only -- it
        # must never be reachable by a misconfigured production deployment.
        if self.llm_provider == "stub" and self.environment == "production":
            raise ValueError("LLM_PROVIDER=stub is forbidden when ENVIRONMENT=production")
        return self

    @property
    def app_database_url(self) -> str:
        url = make_url(self.database_url).set(
            username="app_runtime", password=self.app_runtime_password
        )
        return url.render_as_string(hide_password=False)

    @property
    def app_ops_database_url(self) -> str:
        url = make_url(self.database_url).set(
            username="app_ops", password=self.app_ops_password
        )
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
