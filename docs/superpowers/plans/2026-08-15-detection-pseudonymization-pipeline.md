# Detection & Pseudonymization Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn raw German clinical text into pseudonymized text safe to send to an LLM, and reverse that transformation on the way back — a three-layer fixed-precedence detector stack, a deterministic risk scorer, `TokenVault`-backed pseudonymization, and an output guard, orchestrated by `privacy_gateway/pipeline.py`'s `sanitize()`/`deanonymize()`.

**Architecture:** Three detector layers produce `Span(start, end, entity_type, confidence, source_layer)` objects in fixed precedence — regex (German direct identifiers, confidence 1.0), Presidio with the `de_core_news_lg` spaCy model (PERSON/LOCATION/ORGANIZATION/DATE_TIME), and custom recognizers that *refine* (not rescan) layer 2's PERSON/ORGANIZATION spans into DOCTOR/PATIENT/PERSON and HOSPITAL. A deterministic `RiskScorer` classifies every span as direct identifier / quasi-identifier / medical content and fails closed (raises) on the two-or-more-quasi-identifiers-plus-rare-disease combination and on any sub-0.4-confidence span. A `Pseudonymizer` replaces every tokenize-eligible span in descending offset order via `TokenVault.create_mapping`. The return path re-runs the detector stack on raw LLM output as a leakage check, then resolves only tokens `TokenVault.resolve_tokens` authorizes for this `(tenant_id, conversation_id)`. Reference data (German ORDO OWL, Destatis Krankenhausverzeichnis xlsx) is fetched by a build-time script outside `privacy_gateway/` and parsed once at startup into in-memory sets.

**Tech Stack:** `presidio-analyzer`, `spacy` + `de_core_news_lg`, `rdflib`, `openpyxl`, existing SQLAlchemy 2.0 / `TokenVault` / `tenant_scoped_session` layer, pytest against a real Postgres 16.

## Global Constraints

- **Entity type names are exact and closed.** Layer 1 (regex) emits `INSURANCE_NUMBER`, `PATIENT_NUMBER`, `PHONE`, `EMAIL`, `DATE`, `AGE`. Layer 2 (Presidio+spaCy) emits `PERSON`, `LOCATION`, `DATE_TIME`, `ORGANIZATION`. Layer 3 (custom recognizers) emits `HOSPITAL`, `DOCTOR`, `PATIENT`, `PERSON`. No other entity type name appears anywhere in this plan's code.
- **Fixed precedence, not confidence-based** (design spec §2): "Each layer only processes text not already claimed by a higher-precedence layer." Overlap resolution is by layer order — regex > Presidio > custom — never by comparing confidence scores.
- **Layer 3 refines, does not rescan** (design spec §2): custom recognizers "Operate only on spans already tagged `PERSON`/`ORG` by layer 2." They never scan raw text for new spans.
- **Regex confidence is always 1.0** (design spec §2). Presidio's own per-entity confidence score is preserved verbatim.
- **Confidence threshold is exactly 0.4, fail-closed** (design spec §3): "any span with `confidence < 0.4` (Presidio's own common default) blocks the whole message the same way — fail-closed, not partial." Implemented as the module constant `CONFIDENCE_THRESHOLD = 0.4`, deliberately *not* configurable via `Settings` — a fail-closed threshold that can be relaxed by an environment variable is not fail-closed.
- **HIGH escalation is rejection, not partial sanitization** (design spec §3): "the whole message is rejected — `sanitize()` raises rather than returning partially-sanitized text."
- **HIGH escalation rule** (design spec §3): "If **two or more** distinct quasi-identifier categories co-occur in the same message **and** a rare-disease mention is present (checked against the ORDO-derived name list)."
- **Output guard fail-closed rules** (design spec §5): a detected span in LLM output that "*isn't* inside a `[TYPE_XXXXX]`-shaped substring is raw-looking PII ... Fail-closed: raise, do not return partial output"; and "any unresolved `[TYPE_XXXXX]`-shaped substring left in the output after step 3 is also a fail-closed condition ... raise rather than returning it opaque or silently dropped."
- **Token format** is `[A-Z_]+_[0-9A-F]{10}` (design spec §5, ADR-0009: entity type + `secrets.token_hex(5).upper()`). The `[TYPE_XXXXX]` notation in the design prose is shorthand for that shape — there are **no literal square brackets** in a token; `TokenVault.create_mapping` returns `f"{entity_type}_{secrets.token_hex(5).upper()}"`.
- **ADR-0001 network isolation:** `privacy_gateway/` (including everything this plan adds) never imports `httpx`, `requests`, `aiohttp`, `urllib3`, `socket`, `urllib`, `http`, `ftplib`, `smtplib`, or `telnetlib` — enforced by the existing import-linter contract in `backend/pyproject.toml`. Design spec §6: "no network calls from `privacy_gateway/` itself." `backend/scripts/fetch_reference_data.py` uses `urllib.request` and is deliberately outside the `app` root package the contract covers.
- **Build-time, not runtime** (design spec §6): the spaCy `de_core_news_lg` model is installed via `python -m spacy download` and the ORDO/Krankenhausverzeichnis files are fetched by `backend/scripts/fetch_reference_data.py`, both during `docker build` — "so the running container never needs network access for reference data." Reference data is "parsed once, held in memory, no per-request I/O."
- **Reference data is gitignored** (design spec §6): "Both fetched once, not committed to git (large/stale-prone)."
- **New backend dependencies** (design spec §6): `presidio-analyzer`, `spacy` + `de_core_news_lg`, `rdflib`, `openpyxl`. Everything else already in `backend/pyproject.toml` stays.
- **Golden corpus** (design spec §7): 20–25 hand-written synthetic German clinical notes as JSON fixtures in `evaluation/golden_corpus/`, each with ground-truth entity-span annotations (offset, type), covering each entity type at least twice, one deliberate quasi-identifier combination case, one exact-string-match-limitation case. This plan creates the corpus data only, **not** the scored-benchmark harness.
- **Coreference is exact-string-match within a conversation** (design spec §1, master spec §5) — "Hans Müller" and a later "Herr Müller" get different tokens. Explicit, not a bug.
- **`sanitize()`/`deanonymize()` take `tenant_id`/`conversation_id` as explicit `uuid.UUID` arguments**, same pattern as `TokenVault` — no auth, no HTTP, nothing in this plan calls an actual LLM.
- Local dev assumes `docker compose up -d postgres` is running at `localhost:5432` with the `.env.example` credentials, `alembic upgrade head` has been run, and `secrets/master.key` exists.

## Resolved Design Ambiguities

These are judgment calls made while writing this plan where the design spec was silent or self-inconsistent. Each is implemented exactly as described below and is called out again at the task that implements it.

1. **`PERSON`/`DOCTOR`/`PATIENT` are classified as direct identifiers.** Design spec §3 lists direct identifiers as `INSURANCE_NUMBER`/`PATIENT_NUMBER`/`PHONE`/`EMAIL` and quasi-identifiers as `LOCATION`/`HOSPITAL`/`DATE`/`AGE`, leaving person names in the "everything not otherwise classified → medical content, left intact" bucket. That reading would leave patient names unpseudonymized and directly contradicts §7's corpus-wide invariant and ADR-0009's `PATIENT_` token examples. Names are therefore direct identifiers: always tokenized, no threshold, no escalation contribution. **This is the single largest interpretation call in the plan.**
2. **`DATE_TIME` is a quasi-identifier aliased to the `DATE` category.** Layer 2 emits `DATE_TIME`; §3's quasi list names `DATE`. Both are quasi-identifiers, and both count as the *same* category when counting distinct categories for the ≥2 escalation rule, so a message with a regex `DATE` and a spaCy `DATE_TIME` does not spuriously escalate.
3. **Non-hospital `ORGANIZATION` spans are dropped.** §2 says layer 2's outputs are "generic `PERSON`, `LOCATION`, `DATE_TIME`" — `ORGANIZATION` exists only so layer 3 can look for hospitals in it. An `ORGANIZATION` span not promoted to `HOSPITAL` is discarded rather than carried into risk scoring.
4. **Rare-disease matching is against the whole message text, not "medical-content spans".** §3 says "substring/lemma match against the message's medical-content spans", but no detector layer produces medical-content spans. The scorer normalizes the message into word tokens and looks up 1..8-word n-grams in the ORDO-derived set.
5. **ORDO 4.9 German has no `skos:prefLabel` and no `efo:alternative_term`.** §6 says "German `rdfs:label`/`skos:prefLabel` values". Verified against the real file: 16,372 `rdfs:label` values, 0 `skos:prefLabel`, 0 `efo:alternative_term`, and the German labels carry **no `xml:lang` attribute**. The parser therefore reads `rdfs:label` on `http://www.orpha.net/ORDO/Orphanet_*` subjects with no language filter.
6. **A minimum rare-disease-name length of 6 characters is enforced.** ORDO's label set includes 4–5 character entries like `Fall`, `Pest`, `Kuru`, `Gliom`, `renin`. Matching `Fall` would fire on `Fallnummer` in almost every clinical note and escalate everything to HIGH. `MIN_RARE_DISEASE_NAME_LENGTH = 6` plus whole-word n-gram matching (never raw substring) is the fail-safe. Not in the spec; a safety necessity.
7. **Within-message token reuse only.** Master spec §5 says token determinism is "exact-string-match within a conversation", but `TokenVault` has no value-lookup API — `create_mapping` mints a fresh random token on every call and `encrypted_value` is not searchable. The `Pseudonymizer` therefore caches `(entity_type, original_value) → token` for the duration of one `sanitize()` call, giving determinism **within a message**. Cross-message determinism within a conversation is not achievable without a new deterministic (HMAC) index column on `token_mappings` and is out of scope here — flagged as a follow-up. **This is a real gap between the spec and implementable reality.**
8. **`de_core_news_lg` cannot emit `DATE_TIME`.** German spaCy models have exactly four NER labels: `PER`, `LOC`, `ORG`, `MISC`. Presidio maps `PER→PERSON`, `LOC→LOCATION`, `ORG→ORGANIZATION`, and drops `MISC`. `DATE_TIME` is kept in the supported-entity list (so the code is correct if a future model emits it) but in practice all date coverage comes from layer 1's regex.
9. **`detectors/stack.py` is added to the package structure.** §8's tree lists `base.py`/`regex_detector.py`/`presidio_detector.py`/`custom_recognizers.py`. The precedence composition needs a home that both `pipeline.py` and `output_guard/guard.py` can import without a circular import through `detectors/__init__.py`; `detectors/stack.py` is that home.
10. **Reference-data parsing has a specified fallback.** `rdflib` parsing a 53 MB RDF/XML file is the spec-mandated implementation, but its cost is measured in Task 4 and, above a stated threshold, replaced with a fully-written streaming `xml.etree.ElementTree.iterparse` implementation given in that task. This also drove `Settings.warm_reference_data_on_startup`, so the test suite never pays that cost.
11. **The corpus-wide invariant test may fail on first run** for spaCy-dependent spans (notably `note_017`'s "Charité Universitätsmedizin Berlin"). Task 12 Step 4 gives a strict triage order and an explicit prohibition on editing corpus notes to dodge failures — genuine model gaps go in `KNOWN_RECALL_GAPS` with recorded evidence.
12. **Presidio's recognizer registry holds only `SpacyRecognizer`.** Presidio's predefined recognizer set pulls in `tldextract`, which fetches a public-suffix list over the network on first use. Not mentioned in the spec, but required to honor ADR-0001 in spirit — import-linter would not catch it, because it treats external packages as squashed nodes and does not trace imports inside them.

---

## Task 1: Detection dependencies, build-time spaCy model, `patient_number_pattern` setting

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/config.py`
- Modify: `backend/Dockerfile`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `backend/tests/conftest.py`
- Create: `backend/app/privacy_gateway/risk_scoring/data/.gitignore`
- Test: `backend/tests/unit/test_detection_dependencies.py`

**Interfaces:**
- Consumes: existing `Settings` (`environment`, `database_url`, `master_key_path`, `app_runtime_password`, `app_database_url`) from the data-model plan.
- Produces:
  - `presidio_analyzer`, `spacy`, `rdflib`, `openpyxl` importable, and the `de_core_news_lg` spaCy package installed.
  - `app.config.DEFAULT_PATIENT_NUMBER_PATTERN: str`.
  - `Settings.patient_number_pattern: str` (defaulted, consumed by Task 2's `RegexDetector`).
  - `Settings.warm_reference_data_on_startup: bool = True` (consumed by Task 10's `app/main.py` lifespan).
  - `backend/app/privacy_gateway/risk_scoring/data/` existing in git as an otherwise-empty, gitignored directory (consumed by Task 4).

- [ ] **Step 1: Add the detection dependencies to `backend/pyproject.toml`**

Change the `dependencies` list (in `[project]`) to:

```toml
dependencies = [
    "fastapi>=0.115,<0.116",
    "uvicorn[standard]>=0.32,<0.33",
    "pydantic>=2.9,<3",
    "pydantic-settings>=2.6,<3",
    "sqlalchemy>=2.0,<2.1",
    "psycopg[binary]>=3.2,<4",
    "alembic>=1.13,<2",
    "cryptography>=43,<44",
    "presidio-analyzer>=2.2,<3",
    "spacy>=3.8,<3.9",
    "rdflib>=7.1,<8",
    "openpyxl>=3.1,<4",
]
```

Leave `[project.optional-dependencies]`, `[build-system]`, `[tool.setuptools.packages.find]`, `[tool.pytest.ini_options]`, `[tool.ruff]`, and both `[[tool.importlinter.contracts]]` sections unchanged. In particular, do **not** relax the "Privacy gateway must not be network-capable" contract — nothing this plan adds under `app/privacy_gateway/` imports a forbidden module. (`import-linter` treats external packages as squashed nodes and does not trace imports *inside* `spacy`/`presidio_analyzer`, so depending on them is contract-clean.)

- [ ] **Step 2: Install the new dependencies and the German spaCy model**

Run:
```bash
cd backend
pip install -e ".[dev]"
python -m spacy download de_core_news_lg
```
Expected: both succeed; `pip list` shows `presidio-analyzer`, `spacy`, `rdflib`, `openpyxl`, and `de-core-news-lg`.

- [ ] **Step 3: Write the failing dependency/settings tests**

`backend/tests/unit/test_detection_dependencies.py`:

```python
import openpyxl  # noqa: F401
import rdflib  # noqa: F401
import spacy
from presidio_analyzer import AnalyzerEngine  # noqa: F401

from app.config import DEFAULT_PATIENT_NUMBER_PATTERN, Settings


def test_german_spacy_model_is_installed_at_build_time():
    """ADR-0001 / design spec §6: the model is installed during the image build,
    never downloaded at runtime from inside privacy_gateway."""
    assert spacy.util.is_package("de_core_news_lg")


def test_german_spacy_model_tags_persons_and_locations():
    nlp = spacy.load("de_core_news_lg")
    doc = nlp("Lukas Berger wurde in Heidelberg behandelt.")
    labels = {ent.label_ for ent in doc.ents}
    assert "PER" in labels
    assert "LOC" in labels


def test_settings_default_patient_number_pattern_matches_german_id_labels(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.delenv("PATIENT_NUMBER_PATTERN", raising=False)
    settings = Settings(_env_file=None)
    assert settings.patient_number_pattern == DEFAULT_PATIENT_NUMBER_PATTERN


def test_patient_number_pattern_is_overridable(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.setenv("PATIENT_NUMBER_PATTERN", r"(?P<value>\d{4})")
    settings = Settings(_env_file=None)
    assert settings.patient_number_pattern == r"(?P<value>\d{4})"


def test_warm_reference_data_on_startup_defaults_to_true(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.delenv("WARM_REFERENCE_DATA_ON_STARTUP", raising=False)
    settings = Settings(_env_file=None)
    assert settings.warm_reference_data_on_startup is True
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_detection_dependencies.py -v`
Expected: FAIL with `ImportError: cannot import name 'DEFAULT_PATIENT_NUMBER_PATTERN' from 'app.config'` (collection error for the whole module).

- [ ] **Step 5: Add the new settings to `backend/app/config.py`**

Replace the file in full with:

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_detection_dependencies.py -v`
Expected: PASS (5 passed).

- [ ] **Step 7: Turn the startup warm-up off for the test suite**

Add this line to `backend/tests/conftest.py`, immediately after the existing `os.environ.setdefault("APP_RUNTIME_PASSWORD", "change-me-app-runtime")` line:

```python
os.environ.setdefault("WARM_REFERENCE_DATA_ON_STARTUP", "false")
```

- [ ] **Step 8: Create the gitignored reference-data directory**

`backend/app/privacy_gateway/risk_scoring/data/.gitignore`:

```gitignore
# Design spec §6: the ORDO OWL (~50MB) and Krankenhausverzeichnis xlsx (~2MB) are
# fetched at build time by backend/scripts/fetch_reference_data.py, never committed.
# This file keeps the directory itself in git so the fetch script has a target.
*
!.gitignore
```

- [ ] **Step 9: Install the model and the reference data at image build time**

Replace `backend/Dockerfile` in full with:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

RUN pip install --no-cache-dir -e .

# Build time, never runtime (design spec §6, ADR-0001). The German spaCy model and
# the ORDO / Krankenhausverzeichnis reference data are baked into the image so the
# running container needs no network access to detect or risk-score anything, and
# nothing under app/privacy_gateway/ ever imports a network-capable module.
RUN python -m spacy download de_core_news_lg
RUN python scripts/fetch_reference_data.py

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

`scripts/fetch_reference_data.py` is created in Task 4; the image build will fail until then, which is expected and is verified at the end of Task 4.

- [ ] **Step 10: Add the model and cached reference data to CI**

In `.github/workflows/ci.yml`, in the `backend` job, replace the steps list with:

```yaml
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: python -m spacy download de_core_news_lg
      - name: Cache reference data
        uses: actions/cache@v4
        with:
          path: backend/app/privacy_gateway/risk_scoring/data
          key: reference-data-v1
      - run: python scripts/fetch_reference_data.py
      - run: head -c 32 /dev/urandom > /tmp/ci-master.key
      - run: ruff check .
      - run: lint-imports
      - run: alembic upgrade head
      - run: pytest --cov=app
```

The cache path is repo-relative (not affected by the job's `working-directory: backend`); the `run:` steps stay relative to `backend/`. The cache key is static because the datasets are versioned releases — bump it to `reference-data-v2` when the pinned ORDO/Destatis URLs change.

- [ ] **Step 11: Document the local setup in `README.md`**

In `README.md`, under `## Local development`, insert a new step between the existing step 2 (master key) and step 3 (`docker compose up --build`), renumbering the following steps to 4/5/6:

    3. Install the German spaCy model and fetch the reference datasets. Both are
       build-time inputs (design spec §6) — `app/privacy_gateway/` never downloads
       anything at runtime (ADR-0001):

       ```bash
       cd backend
       python -m spacy download de_core_news_lg
       python scripts/fetch_reference_data.py
       ```

       The fetch downloads the German ORDO OWL release (~51 MB, CC BY 4.0, Orphadata)
       and the Destatis Krankenhausverzeichnis (~2.4 MB, free use with attribution)
       into `backend/app/privacy_gateway/risk_scoring/data/`, which is gitignored. The
       script skips files that are already present, so it is safe to re-run.

And in `## Backend tests`, add this line immediately before the `pytest` line's code block:

    The detection tests need the `de_core_news_lg` model installed (step 3 above).
    They do **not** need the reference datasets — they inject small in-memory
    rare-disease and hospital sets instead.

- [ ] **Step 12: Run the full backend suite and the linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; `lint-imports` reports both contracts KEPT; all tests pass.

- [ ] **Step 13: Commit**

```bash
git add backend/pyproject.toml backend/app/config.py backend/Dockerfile backend/tests/conftest.py backend/tests/unit/test_detection_dependencies.py backend/app/privacy_gateway/risk_scoring/data/.gitignore .github/workflows/ci.yml README.md
git commit -m "feat: add Presidio/spaCy/rdflib/openpyxl deps and build-time model install"
```

---

## Task 2: `Span` model, `Detector` protocol, and the regex detector (layer 1)

**Files:**
- Create: `backend/app/privacy_gateway/detectors/base.py`
- Create: `backend/app/privacy_gateway/detectors/regex_detector.py`
- Test: `backend/tests/unit/test_regex_detector.py`

**Interfaces:**
- Consumes: `Settings.patient_number_pattern` via `app.config.get_settings()` (Task 1).
- Produces:
  - `Span(start: int, end: int, entity_type: str, confidence: float, source_layer: str)` — a frozen, ordered dataclass, importable from `app.privacy_gateway.detectors.base`. Used by every remaining task.
  - `spans_overlap(a: Span, b: Span) -> bool`, `is_claimed(candidate: Span, claimed: Sequence[Span]) -> bool`, `normalize(value: str) -> str` (same module).
  - `Detector` protocol: attribute `layer_name: str`, method `detect(self, text: str, claimed: Sequence[Span]) -> list[Span]`.
  - `SpanRefiner` protocol: attribute `layer_name: str`, method `refine(self, text: str, spans: Sequence[Span]) -> list[Span]`.
  - `RegexDetector(patient_number_pattern: str | None = None)` with `layer_name = "regex"`, importable from `app.privacy_gateway.detectors.regex_detector`.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_regex_detector.py`:

```python
from app.privacy_gateway.detectors.base import Span, is_claimed, normalize, spans_overlap
from app.privacy_gateway.detectors.regex_detector import RegexDetector


def _surfaces(text, spans, entity_type):
    return {text[s.start : s.end] for s in spans if s.entity_type == entity_type}


def test_spans_overlap_is_half_open():
    assert spans_overlap(Span(0, 5, "DATE", 1.0, "regex"), Span(4, 9, "PHONE", 1.0, "regex"))
    assert not spans_overlap(Span(0, 5, "DATE", 1.0, "regex"), Span(5, 9, "PHONE", 1.0, "regex"))


def test_is_claimed_checks_every_existing_span():
    claimed = [Span(10, 20, "EMAIL", 1.0, "regex")]
    assert is_claimed(Span(15, 25, "PHONE", 1.0, "regex"), claimed)
    assert not is_claimed(Span(20, 25, "PHONE", 1.0, "regex"), claimed)


def test_normalize_collapses_case_punctuation_and_whitespace():
    assert normalize("  Klinikum   Nürnberg, AöR ") == "klinikum nürnberg aör"


def test_detects_insurance_number():
    text = "Die Versichertennummer lautet B987654321."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "INSURANCE_NUMBER") == {"B987654321"}


def test_detects_patient_number_value_only_not_the_label():
    text = "Patientennummer: 4471029. Fallnummer: 8830145."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "PATIENT_NUMBER") == {"4471029", "8830145"}


def test_detects_german_phone_formats():
    text = "Erreichbar unter 0941 5551234 oder +49 30 1234567."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "PHONE") == {"0941 5551234", "+49 30 1234567"}


def test_detects_email_without_swallowing_the_sentence_period():
    text = "Rückfragen an s.vogel@example.de."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "EMAIL") == {"s.vogel@example.de"}


def test_detects_german_dates():
    text = "Aufnahme am 12.03.2024, Entlassung am 28.02.2024."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "DATE") == {"12.03.2024", "28.02.2024"}


def test_detects_age_in_all_german_forms():
    text = "Ein 7-jähriger, eine 52-jährige, ein 41-jährigen und jemand 67 Jahre alt."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "AGE") == {
        "7-jähriger",
        "52-jährige",
        "41-jährigen",
        "67 Jahre alt",
    }


def test_every_regex_span_has_confidence_one_and_the_regex_layer_name():
    text = "A123456789 am 12.03.2024, 0941 5551234, a@b.de, 67 Jahre alt."
    spans = RegexDetector().detect(text, [])
    assert spans
    assert all(span.confidence == 1.0 for span in spans)
    assert all(span.source_layer == "regex" for span in spans)


def test_a_date_is_never_swallowed_by_the_permissive_phone_pattern():
    text = "Telefonisch unter +49 30 1234567 am 03.01.2025."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "DATE") == {"03.01.2025"}
    assert _surfaces(text, spans, "PHONE") == {"+49 30 1234567"}


def test_already_claimed_offsets_are_skipped():
    text = "Aufnahme am 12.03.2024."
    claimed = [Span(12, 22, "DATE", 1.0, "some-higher-layer")]
    spans = RegexDetector().detect(text, claimed)
    assert spans == []


def test_spans_are_returned_in_ascending_offset_order():
    text = "Patientennummer: 4471029, Aufnahme am 12.03.2024, Kontakt a@b.de."
    spans = RegexDetector().detect(text, [])
    assert [s.start for s in spans] == sorted(s.start for s in spans)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_regex_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.detectors.base'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/detectors/base.py`**

```python
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, order=True)
class Span:
    """One detected sensitive region of a message.

    Field order matters: `order=True` sorts spans by `(start, end, entity_type, ...)`,
    which is the ascending-offset order every layer returns and the pseudonymizer
    reverses.
    """

    start: int
    end: int
    entity_type: str
    confidence: float
    source_layer: str


def spans_overlap(a: Span, b: Span) -> bool:
    """True if two half-open [start, end) ranges share at least one character."""
    return a.start < b.end and b.start < a.end


def is_claimed(candidate: Span, claimed: Sequence[Span]) -> bool:
    """Design spec §2: each layer only processes text not already claimed by a
    higher-precedence layer. Precedence is fixed (regex > Presidio > custom), never
    resolved by comparing confidence scores.
    """
    return any(spans_overlap(candidate, existing) for existing in claimed)


_WORD_PATTERN = re.compile(r"[0-9A-Za-zÄÖÜäöüß-]+")


def normalize(value: str) -> str:
    """Case-folded, punctuation-stripped, single-spaced form used for every
    gazetteer and rare-disease-name comparison, so both sides of a lookup are
    normalized identically.
    """
    return " ".join(_WORD_PATTERN.findall(value)).casefold()


@runtime_checkable
class Detector(Protocol):
    """Layers 1 and 2: scan raw text, skipping anything already claimed."""

    layer_name: str

    def detect(self, text: str, claimed: Sequence[Span]) -> list[Span]: ...


@runtime_checkable
class SpanRefiner(Protocol):
    """Layer 3: refine, don't rescan (design spec §2) — takes the spans produced by
    layers 1 and 2 and returns a re-tagged set, never new offsets."""

    layer_name: str

    def refine(self, text: str, spans: Sequence[Span]) -> list[Span]: ...
```

- [ ] **Step 4: Create `backend/app/privacy_gateway/detectors/regex_detector.py`**

```python
from __future__ import annotations

import re
from collections.abc import Sequence

from app.config import get_settings
from app.privacy_gateway.detectors.base import Span, is_claimed

LAYER_NAME = "regex"

EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
# German Versichertennummer: one uppercase letter followed by nine digits.
INSURANCE_NUMBER_PATTERN = r"\b[A-Z]\d{9}\b"
DATE_PATTERN = r"\b\d{2}\.\d{2}\.\d{4}\b"
# Design spec §2 gives `\d{1,3}\s*(?:-jährig|jährige[rn]?|Jahre alt)`. The hyphenated
# alternatives are listed longest-first here so "81-jähriger" yields the full
# "81-jähriger" rather than stopping after "81-jährig" — Python alternation is
# first-match, not longest-match.
AGE_PATTERN = r"\d{1,3}\s*(?:-jährige[rn]?|-jährig|jährige[rn]?|Jahre alt)"
PHONE_PATTERN = r"(?:\+49[ ]?|0)\d{2,5}[ /-]?\d{3,9}(?:[ -]?\d{1,4})?"


class RegexDetector:
    """Layer 1: German-specific fixed-format identifiers, confidence always 1.0."""

    layer_name = LAYER_NAME

    def __init__(self, patient_number_pattern: str | None = None) -> None:
        if patient_number_pattern is None:
            patient_number_pattern = get_settings().patient_number_pattern
        # An ordered list, not a dict: EMAIL, INSURANCE_NUMBER, PATIENT_NUMBER and
        # DATE all contain digit runs that the deliberately permissive PHONE pattern
        # would otherwise swallow, so PHONE runs last and sees them already claimed.
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            ("EMAIL", re.compile(EMAIL_PATTERN)),
            ("INSURANCE_NUMBER", re.compile(INSURANCE_NUMBER_PATTERN)),
            ("PATIENT_NUMBER", re.compile(patient_number_pattern)),
            ("DATE", re.compile(DATE_PATTERN)),
            ("AGE", re.compile(AGE_PATTERN)),
            ("PHONE", re.compile(PHONE_PATTERN)),
        ]

    def detect(self, text: str, claimed: Sequence[Span]) -> list[Span]:
        seen: list[Span] = list(claimed)
        produced: list[Span] = []
        for entity_type, pattern in self._patterns:
            for match in pattern.finditer(text):
                start, end = self._match_bounds(match)
                span = Span(start, end, entity_type, 1.0, self.layer_name)
                if is_claimed(span, seen):
                    continue
                seen.append(span)
                produced.append(span)
        return sorted(produced)

    @staticmethod
    def _match_bounds(match: re.Match[str]) -> tuple[int, int]:
        """A pattern may mark the identifier itself with a named group `value`, so a
        label like "Patientennummer: " stays in the text and only the number is
        replaced by a token."""
        if "value" in match.re.groupindex:
            return match.start("value"), match.end("value")
        return match.start(), match.end()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_regex_detector.py -v`
Expected: PASS (13 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/privacy_gateway/detectors/base.py backend/app/privacy_gateway/detectors/regex_detector.py backend/tests/unit/test_regex_detector.py
git commit -m "feat: add Span model, detector protocols, and the German regex detector"
```

---

## Task 3: Presidio + spaCy detector (layer 2)

**Files:**
- Create: `backend/app/privacy_gateway/detectors/presidio_detector.py`
- Test: `backend/tests/unit/test_presidio_detector.py`

**Interfaces:**
- Consumes: `Span`, `is_claimed` from `app.privacy_gateway.detectors.base` (Task 2); the `de_core_news_lg` package installed in Task 1.
- Produces:
  - `build_analyzer_engine() -> AnalyzerEngine` (`lru_cache`d, one engine per process).
  - `PresidioDetector(analyzer: AnalyzerEngine | None = None)` with `layer_name = "presidio"`, emitting `PERSON`, `LOCATION`, `ORGANIZATION`, `DATE_TIME` spans with Presidio's own confidence score.
  - `SPACY_MODEL_NAME = "de_core_news_lg"`, `SUPPORTED_ENTITIES: list[str]`.

**Resolved ambiguities implemented here:** #8 (`de_core_news_lg` emits no `DATE_TIME`), #12 (registry holds only `SpacyRecognizer`).

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_presidio_detector.py`:

```python
import pytest

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.presidio_detector import (
    SPACY_MODEL_NAME,
    SUPPORTED_ENTITIES,
    PresidioDetector,
    build_analyzer_engine,
)


@pytest.fixture(scope="module")
def detector():
    return PresidioDetector()


def _surfaces(text, spans, entity_type):
    return {text[s.start : s.end] for s in spans if s.entity_type == entity_type}


def test_uses_the_german_model_named_in_the_design_spec():
    assert SPACY_MODEL_NAME == "de_core_news_lg"
    assert SUPPORTED_ENTITIES == ["PERSON", "LOCATION", "ORGANIZATION", "DATE_TIME"]


def test_registry_holds_only_the_spacy_recognizer():
    """ADR-0001: Presidio's predefined recognizer set pulls in network-capable
    helpers (tldextract fetches a public-suffix list). Registering only the spaCy
    recognizer keeps the analyzer offline by construction."""
    engine = build_analyzer_engine()
    assert len(engine.registry.recognizers) == 1
    assert engine.registry.recognizers[0].name == "SpacyRecognizer"


def test_detects_person_and_location(detector):
    text = "Lukas Berger wurde in Heidelberg behandelt."
    spans = detector.detect(text, [])
    assert _surfaces(text, spans, "PERSON") == {"Lukas Berger"}
    assert _surfaces(text, spans, "LOCATION") == {"Heidelberg"}


def test_preserves_presidios_own_confidence_score(detector):
    spans = detector.detect("Lukas Berger wurde in Heidelberg behandelt.", [])
    assert spans
    for span in spans:
        assert 0.0 < span.confidence <= 1.0
        assert span.source_layer == "presidio"


def test_skips_text_already_claimed_by_a_higher_precedence_layer(detector):
    text = "Lukas Berger wurde in Heidelberg behandelt."
    claimed = [Span(0, 12, "PATIENT_NUMBER", 1.0, "regex")]
    spans = detector.detect(text, claimed)
    assert _surfaces(text, spans, "PERSON") == set()
    assert _surfaces(text, spans, "LOCATION") == {"Heidelberg"}


def test_emits_organization_for_a_clinic_name(detector):
    text = "Die Verlegung in das Universitätsklinikum Heidelberg erfolgte."
    spans = detector.detect(text, [])
    organizations = _surfaces(text, spans, "ORGANIZATION")
    assert any("Universitätsklinikum" in name for name in organizations)


def test_returns_spans_in_ascending_offset_order(detector):
    text = "Lukas Berger wurde in Heidelberg von Anna Schmitt behandelt."
    spans = detector.detect(text, [])
    assert [s.start for s in spans] == sorted(s.start for s in spans)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_presidio_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.detectors.presidio_detector'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/detectors/presidio_detector.py`**

```python
from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import SpacyRecognizer

from app.privacy_gateway.detectors.base import Span, is_claimed

LAYER_NAME = "presidio"
SPACY_MODEL_NAME = "de_core_news_lg"

# de_core_news_lg's NER labels are PER, LOC, ORG and MISC; Presidio maps
# PER -> PERSON, LOC -> LOCATION, ORG -> ORGANIZATION and drops MISC. DATE_TIME is
# listed because the design spec names it as a layer-2 output, but no German spaCy
# model emits DATE entities — in practice all date coverage comes from layer 1's
# regex. ORGANIZATION exists purely as input to the layer-3 HOSPITAL refinement.
SUPPORTED_ENTITIES = ["PERSON", "LOCATION", "ORGANIZATION", "DATE_TIME"]


@lru_cache(maxsize=1)
def build_analyzer_engine() -> AnalyzerEngine:
    """One analyzer (and one loaded spaCy model) per process.

    The registry is populated with the spaCy recognizer *before* AnalyzerEngine sees
    it, because AnalyzerEngine calls `load_predefined_recognizers()` on an empty
    registry — and several predefined recognizers reach for the network (ADR-0001).
    """
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "de", "model_name": SPACY_MODEL_NAME}],
        }
    )
    registry = RecognizerRegistry()
    registry.add_recognizer(
        SpacyRecognizer(supported_language="de", supported_entities=SUPPORTED_ENTITIES)
    )
    return AnalyzerEngine(
        nlp_engine=provider.create_engine(),
        registry=registry,
        supported_languages=["de"],
    )


class PresidioDetector:
    """Layer 2: the German-NER layer (design spec §2). Presidio's own per-entity
    confidence score is preserved rather than overwritten."""

    layer_name = LAYER_NAME

    def __init__(self, analyzer: AnalyzerEngine | None = None) -> None:
        self._analyzer = analyzer if analyzer is not None else build_analyzer_engine()

    def detect(self, text: str, claimed: Sequence[Span]) -> list[Span]:
        results = self._analyzer.analyze(
            text=text, language="de", entities=SUPPORTED_ENTITIES
        )
        seen: list[Span] = list(claimed)
        produced: list[Span] = []
        for result in sorted(results, key=lambda item: (item.start, item.end)):
            span = Span(
                result.start,
                result.end,
                result.entity_type,
                float(result.score),
                self.layer_name,
            )
            if is_claimed(span, seen):
                continue
            seen.append(span)
            produced.append(span)
        return produced
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_presidio_detector.py -v`
Expected: PASS (7 passed). The first test in the module takes ~10–20 s while the spaCy model loads; subsequent ones reuse the cached engine.

- [ ] **Step 5: Verify the network-isolation contract still holds**

Run: `cd backend && lint-imports`
Expected: both contracts KEPT — in particular "Privacy gateway must not be network-capable".

- [ ] **Step 6: Commit**

```bash
git add backend/app/privacy_gateway/detectors/presidio_detector.py backend/tests/unit/test_presidio_detector.py
git commit -m "feat: add the Presidio + de_core_news_lg detector layer"
```

---

## Task 4: Reference data — build-time fetch script and ORDO/Krankenhausverzeichnis parsers

**Files:**
- Create: `backend/scripts/fetch_reference_data.py`
- Create: `backend/app/privacy_gateway/risk_scoring/reference_data.py`
- Create: `backend/tests/fixtures/ordo_sample.owl`
- Create: `backend/tests/fixtures/krankenhausverzeichnis_sample.xlsx` (generated in Step 4)
- Test: `backend/tests/unit/test_reference_data.py`

**Interfaces:**
- Consumes: `normalize()` from `app.privacy_gateway.detectors.base` (Task 2); the gitignored `risk_scoring/data/` directory (Task 1).
- Produces:
  - `load_rare_disease_names(owl_path: str) -> frozenset[str]` — normalized German rare-disease names.
  - `load_hospital_names(xlsx_path: str) -> frozenset[str]` — normalized facility names.
  - `get_rare_disease_names() -> frozenset[str]` and `get_hospital_names() -> frozenset[str]` — the same, over the bundled files in `DATA_DIR`.
  - `DATA_DIR: Path`, `ORDO_FILENAME = "ORDO_de_4.9.owl"`, `KRANKENHAUSVERZEICHNIS_FILENAME = "krankenhausverzeichnis.xlsx"`, `MIN_RARE_DISEASE_NAME_LENGTH = 6`.
  - `backend/scripts/fetch_reference_data.py` runnable as `python scripts/fetch_reference_data.py`.

**Resolved ambiguities implemented here:** #5 (`rdfs:label` only, no language filter), #6 (6-character minimum), #10 (measured rdflib cost with a specified fallback).

**Verified upstream facts** (checked against the live files while writing this plan, not guessed):
- `https://www.orphadata.com/data/ontologies/ordo/last_version/ORDO_de_4.9.owl` returns HTTP 200 with `content-length: 53086913`. It contains 16,372 `rdfs:label` values, **zero** `skos:prefLabel`, **zero** `efo:alternative_term`, and German labels carry no `xml:lang` attribute.
- The Krankenhausverzeichnis xlsx is 2,499,840 bytes. Its hospital sheet is titled `" KHV_2024"` (**with a leading space**), the header row is row 3, `KH_Name` is column E and `Standortname` is column F, and the two columns together yield 3,564 unique facility names.

- [ ] **Step 1: Create the build-time fetch script**

`backend/scripts/fetch_reference_data.py`:

```python
"""Download the reference datasets the risk scorer needs — at build time only.

ADR-0001 and design spec §6: `app/privacy_gateway/` must never be network-capable.
This script lives outside that package (and outside the `app` root package the
import-linter contracts cover) and runs during `docker build` and CI setup, never
at request time. The files it writes are gitignored: large, stale-prone, and
re-fetchable.

Sources:
  * Orphanet Rare Disease Ontology, German release 4.9 (~51 MB, CC BY 4.0).
  * Verzeichnis der Krankenhäuser und Vorsorge- oder Rehabilitationseinrichtungen
    in Deutschland, Statistisches Bundesamt (~2.4 MB, free use with attribution).
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

DATA_DIR = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "privacy_gateway"
    / "risk_scoring"
    / "data"
)

ORDO_URL = "https://www.orphadata.com/data/ontologies/ordo/last_version/ORDO_de_4.9.owl"
ORDO_FILENAME = "ORDO_de_4.9.owl"
ORDO_MIN_BYTES = 40_000_000

KRANKENHAUSVERZEICHNIS_URL = (
    "https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Gesundheit/Krankenhauser/"
    "Publikationen/Downloads-Krankenhaeuser/krankenhausverzeichnis-3500100247005.xlsx"
    "?__blob=publicationFile&v=6"
)
KRANKENHAUSVERZEICHNIS_FILENAME = "krankenhausverzeichnis.xlsx"
KRANKENHAUSVERZEICHNIS_MIN_BYTES = 1_000_000

USER_AGENT = "chatgpt-proxy-reference-data-fetch/1.0"
CHUNK_BYTES = 1 << 20


def download(url: str, target: Path, min_bytes: int) -> None:
    if target.exists() and target.stat().st_size >= min_bytes:
        print(f"{target.name}: already present ({target.stat().st_size} bytes), skipping")
        return

    print(f"{target.name}: downloading from {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = target.parent / (target.name + ".part")
    with urllib.request.urlopen(request, timeout=300) as response:
        with partial.open("wb") as handle:
            while chunk := response.read(CHUNK_BYTES):
                handle.write(chunk)

    size = partial.stat().st_size
    if size < min_bytes:
        # A truncated download, an HTML error page, or a moved release must fail the
        # build loudly rather than leave a file the startup parser silently accepts.
        partial.unlink()
        raise SystemExit(
            f"{target.name}: downloaded only {size} bytes, expected at least "
            f"{min_bytes}; the upstream URL has probably moved — check {url}"
        )
    partial.replace(target)
    print(f"{target.name}: wrote {size} bytes")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    download(ORDO_URL, DATA_DIR / ORDO_FILENAME, ORDO_MIN_BYTES)
    download(
        KRANKENHAUSVERZEICHNIS_URL,
        DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME,
        KRANKENHAUSVERZEICHNIS_MIN_BYTES,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the fetch script**

Run: `cd backend && python scripts/fetch_reference_data.py`
Expected: prints `ORDO_de_4.9.owl: wrote 53086913 bytes` (±, releases change) and `krankenhausverzeichnis.xlsx: wrote 2499840 bytes`. Re-running prints `already present ... skipping` for both.

- [ ] **Step 3: Create the small ORDO test fixture**

`backend/tests/fixtures/ordo_sample.owl` — a hand-written miniature of the real file's structure: German `rdfs:label` values with no `xml:lang` attribute, Orphanet class IRIs, plus two entries that must be filtered out (a too-short name, and a label on a non-Orphanet subject).

```xml
<?xml version="1.0"?>
<rdf:RDF xmlns="http://www.w3.org/2002/07/owl#"
     xml:base="http://www.w3.org/2002/07/owl"
     xmlns:owl="http://www.w3.org/2002/07/owl#"
     xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
     xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
     xmlns:skos="http://www.w3.org/2004/02/skos/core#"
     xmlns:ORDO="http://www.orpha.net/ORDO/">
    <Ontology rdf:about="http://www.orpha.net/ORDO/ordo_sample"/>
    <Class rdf:about="http://www.orpha.net/ORDO/Orphanet_586">
        <rdfs:label>Zystische Fibrose</rdfs:label>
        <skos:notation>ORPHA:586</skos:notation>
    </Class>
    <Class rdf:about="http://www.orpha.net/ORDO/Orphanet_399">
        <rdfs:label>Huntington-Krankheit</rdfs:label>
        <skos:notation>ORPHA:399</skos:notation>
    </Class>
    <Class rdf:about="http://www.orpha.net/ORDO/Orphanet_558">
        <rdfs:label>Marfan-Syndrom</rdfs:label>
        <skos:notation>ORPHA:558</skos:notation>
    </Class>
    <Class rdf:about="http://www.orpha.net/ORDO/Orphanet_2394">
        <rdfs:label>Kuru</rdfs:label>
        <skos:notation>ORPHA:2394</skos:notation>
    </Class>
    <Class rdf:about="http://www.ebi.ac.uk/efo/EFO_0000001">
        <rdfs:label>Experimentelle Einheit</rdfs:label>
    </Class>
</rdf:RDF>
```

- [ ] **Step 4: Generate the small Krankenhausverzeichnis test fixture**

An `.xlsx` cannot be hand-written, so generate it. It mirrors the real workbook: a leading-space sheet title `" KHV_2024"`, two banner rows, a header row on row 3 with `KH_Name` in column E and `Standortname` in column F, and a decoy sheet that must not be picked.

Run:
```bash
cd backend
python - <<'PY'
from pathlib import Path
from openpyxl import Workbook

workbook = Workbook()
decoy = workbook.active
decoy.title = "Inhalt"
decoy["A1"] = "Verzeichnis der Krankenhäuser"

sheet = workbook.create_sheet(" KHV_2024")
sheet.append(["Zurück zum Inhalt"])
sheet.append(["Verzeichnis der Krankenhäuser (KHV_2024)"])
sheet.append(
    ["KH_ID_Pseudo", "Land", "Gemeinde", "Kreis", "KH_Name", "Standortname",
     "Straße", "Hausnummer", "PLZ", "Ort"]
)
rows = [
    ("1937", "01", "000", "002", "UKSH – Universitätsklinikum Schleswig-Holstein",
     "Campus Kiel – Stationäre Behandlung", "Arnold-Heller-Straße", "3", "24105", "Kiel"),
    ("2841", "01", "000", "002", "Städtisches Krankenhaus Kiel GmbH",
     "Hauptstandort - Stationäre Behandlung", "Chemnitzstraße", "33", "24116", "Kiel"),
    ("1525", "01", "000", "001", "Katharinen Hospiz am Park",
     "Katharinen Hospiz am Park", "Mühlenstraße", "1", "24937", "Flensburg"),
    ("9001", "11", "000", "000", "Charité Universitätsmedizin Berlin",
     "Charité Campus Virchow Klinikum", "Augustenburger Platz", "1", "13353", "Berlin"),
]
for row in rows:
    sheet.append(list(row))

target = Path("tests/fixtures/krankenhausverzeichnis_sample.xlsx")
target.parent.mkdir(parents=True, exist_ok=True)
workbook.save(target)
print("wrote", target, target.stat().st_size, "bytes")
PY
```
Expected: `wrote tests/fixtures/krankenhausverzeichnis_sample.xlsx <n> bytes`.

- [ ] **Step 5: Write the failing test**

`backend/tests/unit/test_reference_data.py`:

```python
from pathlib import Path

import pytest

from app.privacy_gateway.risk_scoring.reference_data import (
    DATA_DIR,
    KRANKENHAUSVERZEICHNIS_FILENAME,
    MIN_RARE_DISEASE_NAME_LENGTH,
    ORDO_FILENAME,
    load_hospital_names,
    load_rare_disease_names,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

reference_data = pytest.mark.skipif(
    not (DATA_DIR / ORDO_FILENAME).exists()
    or not (DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME).exists(),
    reason="run `python scripts/fetch_reference_data.py` to enable",
)


def test_ordo_labels_are_loaded_normalized():
    names = load_rare_disease_names(str(FIXTURES / "ordo_sample.owl"))
    assert "zystische fibrose" in names
    assert "huntington-krankheit" in names
    assert "marfan-syndrom" in names


def test_short_ordo_labels_are_filtered_out():
    """ORDO contains 4-5 character labels like "Kuru", "Fall" and "Pest". Matching
    those would escalate almost every clinical note to HIGH risk."""
    assert MIN_RARE_DISEASE_NAME_LENGTH == 6
    names = load_rare_disease_names(str(FIXTURES / "ordo_sample.owl"))
    assert "kuru" not in names


def test_non_orphanet_subjects_are_ignored():
    names = load_rare_disease_names(str(FIXTURES / "ordo_sample.owl"))
    assert "experimentelle einheit" not in names


def test_hospital_names_come_from_the_khv_sheet_normalized():
    names = load_hospital_names(str(FIXTURES / "krankenhausverzeichnis_sample.xlsx"))
    assert "städtisches krankenhaus kiel gmbh" in names
    assert "charité universitätsmedizin berlin" in names
    assert "katharinen hospiz am park" in names


def test_hospital_names_include_the_standortname_column():
    names = load_hospital_names(str(FIXTURES / "krankenhausverzeichnis_sample.xlsx"))
    assert "charité campus virchow klinikum" in names


def test_hospital_header_row_and_banner_rows_are_not_treated_as_names():
    names = load_hospital_names(str(FIXTURES / "krankenhausverzeichnis_sample.xlsx"))
    assert "kh_name" not in names
    assert "zurück zum inhalt" not in names


def test_missing_khv_sheet_is_a_loud_failure(tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.title = "Inhalt"
    path = tmp_path / "no_khv.xlsx"
    workbook.save(path)
    with pytest.raises(ValueError, match="KHV_"):
        load_hospital_names(str(path))


@reference_data
def test_real_ordo_release_contains_the_corpus_diseases():
    names = load_rare_disease_names(str(DATA_DIR / ORDO_FILENAME))
    assert len(names) > 10_000
    assert "zystische fibrose" in names
    assert "huntington-krankheit" in names


@reference_data
def test_real_krankenhausverzeichnis_contains_known_facilities():
    names = load_hospital_names(str(DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME))
    assert len(names) > 3_000
    assert "universitätsklinikum heidelberg" in names
    assert "charité universitätsmedizin berlin" in names
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_reference_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.risk_scoring.reference_data'`.

- [ ] **Step 7: Create `backend/app/privacy_gateway/risk_scoring/reference_data.py`**

```python
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import rdflib
from openpyxl import load_workbook

from app.privacy_gateway.detectors.base import normalize

DATA_DIR = Path(__file__).resolve().parent / "data"
ORDO_FILENAME = "ORDO_de_4.9.owl"
KRANKENHAUSVERZEICHNIS_FILENAME = "krankenhausverzeichnis.xlsx"

# ORDO's German label set includes 4-5 character entries such as "Kuru", "Fall",
# "Pest", "Gliom" and gene symbols like "renin". Matching "Fall" would fire on
# "Fallnummer" in almost every clinical note and escalate it to HIGH risk, so short
# names are dropped. Matching is whole-word n-gram, never raw substring.
MIN_RARE_DISEASE_NAME_LENGTH = 6

_ORPHANET_CLASS_PREFIX = "http://www.orpha.net/ORDO/Orphanet_"
_KHV_SHEET_PREFIX = "KHV_"
_HOSPITAL_NAME_COLUMNS = ("KH_Name", "Standortname")


@lru_cache(maxsize=None)
def load_rare_disease_names(owl_path: str) -> frozenset[str]:
    """German rare-disease names from an ORDO OWL release.

    ORDO 4.9's German release carries the German name in `rdfs:label` with **no**
    `xml:lang` attribute, and has no `skos:prefLabel` and no `efo:alternative_term`
    at all — so there is nothing to filter on by language and nothing else to read.
    Only `Orphanet_*` class IRIs are considered, which excludes the imported EFO/OBO
    scaffolding classes that also carry German labels.
    """
    graph = rdflib.Graph()
    graph.parse(owl_path, format="xml")
    names: set[str] = set()
    for subject, label in graph.subject_objects(rdflib.RDFS.label):
        if not str(subject).startswith(_ORPHANET_CLASS_PREFIX):
            continue
        normalized = normalize(str(label))
        if len(normalized) < MIN_RARE_DISEASE_NAME_LENGTH:
            continue
        names.add(normalized)
    return frozenset(names)


@lru_cache(maxsize=None)
def load_hospital_names(xlsx_path: str) -> frozenset[str]:
    """Facility names from the Destatis Krankenhausverzeichnis workbook.

    The hospital sheet's title carries a leading space in the published file
    (`" KHV_2024"`) and the year changes annually, so it is located by stripped
    prefix rather than by exact name or index. The header row is likewise found by
    content (`KH_Name`) rather than by row number, because the sheet starts with
    banner rows.
    """
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        sheet = next(
            (ws for ws in workbook.worksheets if ws.title.strip().startswith(_KHV_SHEET_PREFIX)),
            None,
        )
        if sheet is None:
            raise ValueError(
                f"{xlsx_path} has no worksheet whose title starts with "
                f"{_KHV_SHEET_PREFIX!r}; the Destatis layout has changed"
            )

        name_columns: dict[str, int] | None = None
        names: set[str] = set()
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if value is None else str(value).strip() for value in row]
            if name_columns is None:
                if _HOSPITAL_NAME_COLUMNS[0] in cells:
                    name_columns = {
                        column: cells.index(column)
                        for column in _HOSPITAL_NAME_COLUMNS
                        if column in cells
                    }
                continue
            for index in name_columns.values():
                if index >= len(cells) or not cells[index]:
                    continue
                normalized = normalize(cells[index])
                if normalized:
                    names.add(normalized)

        if name_columns is None:
            raise ValueError(
                f"{xlsx_path} has no {_HOSPITAL_NAME_COLUMNS[0]!r} header row; "
                "the Destatis layout has changed"
            )
        return frozenset(names)
    finally:
        workbook.close()


@lru_cache(maxsize=1)
def get_rare_disease_names() -> frozenset[str]:
    """Design spec §6: parsed once, held in memory, no per-request I/O."""
    return load_rare_disease_names(str(DATA_DIR / ORDO_FILENAME))


@lru_cache(maxsize=1)
def get_hospital_names() -> frozenset[str]:
    """Design spec §6: parsed once, held in memory, no per-request I/O."""
    return load_hospital_names(str(DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME))
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_reference_data.py -v`
Expected: PASS (9 passed). The two `@reference_data` tests run because Step 2 fetched the files; the ORDO one takes minutes on the first run.

- [ ] **Step 9: Measure the real ORDO parse cost**

Run:
```bash
cd backend
python -c "
import time, resource
from app.privacy_gateway.risk_scoring.reference_data import get_rare_disease_names
start = time.perf_counter()
names = get_rare_disease_names()
elapsed = time.perf_counter() - start
peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
print(f'{len(names)} names in {elapsed:.1f}s, peak RSS {peak_mb:.0f} MB')
"
```
Expected: prints roughly `15000 names in <T>s, peak RSS <M> MB`.

**If `<T>` exceeds 180 seconds or `<M>` exceeds 4000 MB, do Step 10. Otherwise skip to Step 11.**

- [ ] **Step 10 (conditional): Replace the rdflib loader with a streaming parser**

`rdflib` materializes the whole 53 MB RDF/XML graph in memory even though only one predicate is needed. Replace *only* the `load_rare_disease_names` function (and swap the `import rdflib` line for `from xml.etree import ElementTree`) in `reference_data.py` with:

```python
_RDF_NS = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_RDFS_NS = "{http://www.w3.org/2000/01/rdf-schema#}"
_OWL_CLASS_TAG = "{http://www.w3.org/2002/07/owl#}Class"
_BARE_CLASS_TAG = "Class"


@lru_cache(maxsize=None)
def load_rare_disease_names(owl_path: str) -> frozenset[str]:
    """German rare-disease names from an ORDO OWL release, streamed.

    Same semantics as the rdflib implementation (Orphanet class IRIs, `rdfs:label`,
    no language filter, minimum name length) but with constant memory: the 53 MB
    RDF/XML release is walked element by element and each `<Class>` subtree is
    discarded as soon as its label has been read. ORDO declares owl: as the default
    namespace, so classes appear both as `owl:Class` and as the unqualified `Class`
    depending on the serializer — both tags are accepted.
    """
    names: set[str] = set()
    for _, element in ElementTree.iterparse(owl_path, events=("end",)):
        if element.tag not in (_OWL_CLASS_TAG, _BARE_CLASS_TAG):
            continue
        about = element.get(f"{_RDF_NS}about", "")
        if about.startswith(_ORPHANET_CLASS_PREFIX):
            label = element.find(f"{_RDFS_NS}label")
            if label is not None and label.text:
                normalized = normalize(label.text)
                if len(normalized) >= MIN_RARE_DISEASE_NAME_LENGTH:
                    names.add(normalized)
        element.clear()
    return frozenset(names)
```

Then re-run Steps 8 and 9. Expected: the same test results, with a materially lower time and peak RSS. Record the chosen implementation in the commit message.

- [ ] **Step 11: Verify the Docker build now succeeds end to end**

Run: `docker compose build backend`
Expected: the build completes, including the `python -m spacy download de_core_news_lg` and `python scripts/fetch_reference_data.py` layers added in Task 1.

- [ ] **Step 12: Verify the network-isolation contract still holds**

Run: `cd backend && lint-imports`
Expected: both contracts KEPT. `scripts/fetch_reference_data.py` uses `urllib.request` but is outside the `app` root package the contracts cover — which is exactly why the fetch is a script and not a runtime import.

- [ ] **Step 13: Commit**

```bash
git add backend/scripts/fetch_reference_data.py backend/app/privacy_gateway/risk_scoring/reference_data.py backend/tests/fixtures/ordo_sample.owl backend/tests/fixtures/krankenhausverzeichnis_sample.xlsx backend/tests/unit/test_reference_data.py
git commit -m "feat: fetch and parse ORDO and Krankenhausverzeichnis reference data"
```

---

## Task 5: Custom recognizers (layer 3) — hospital gazetteer and doctor/patient heuristic

**Files:**
- Create: `backend/app/privacy_gateway/detectors/custom_recognizers.py`
- Test: `backend/tests/unit/test_custom_recognizers.py`

**Interfaces:**
- Consumes: `Span`, `normalize` from `app.privacy_gateway.detectors.base` (Task 2). Takes its gazetteer as a constructor argument — `frozenset[str]` of normalized names, produced by `get_hospital_names()` (Task 4) in production and injected directly in tests.
- Produces: `CustomRecognizers(hospital_names: frozenset[str])` with `layer_name = "custom"` and `refine(self, text: str, spans: Sequence[Span]) -> list[Span]`; module constants `HOSPITAL_SUFFIX_PATTERN`, `TITLE_PREFIX_PATTERN`, `TREATING_DOCTOR_PATTERN`.

**Resolved ambiguity implemented here:** #3 (non-hospital `ORGANIZATION` spans are dropped).

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_custom_recognizers.py`:

```python
from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers

GAZETTEER = frozenset(
    {"charité universitätsmedizin berlin", "katharinen hospiz am park"}
)


def _refine(text, spans):
    return CustomRecognizers(GAZETTEER).refine(text, spans)


def _typed(text, spans, entity_type):
    return {text[s.start : s.end] for s in spans if s.entity_type == entity_type}


def test_organization_with_a_spec_suffix_becomes_hospital():
    text = "Aufnahme im Universitätsklinikum Heidelberg."
    spans = [Span(12, 43, "ORGANIZATION", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "HOSPITAL") == {
        "Universitätsklinikum Heidelberg"
    }


def test_all_four_spec_suffixes_are_recognised():
    for surface in (
        "Universitätsklinikum Freiburg",
        "Klinikum Nürnberg",
        "Krankenhaus Sankt Elisabeth",
        "MVZ Gesundheitszentrum Ost",
    ):
        spans = [Span(0, len(surface), "ORGANIZATION", 0.85, "presidio")]
        assert _typed(surface, _refine(surface, spans), "HOSPITAL") == {surface}


def test_organization_in_the_gazetteer_becomes_hospital_without_a_suffix():
    text = "Verlegung in die Charité Universitätsmedizin Berlin."
    spans = [Span(17, 51, "ORGANIZATION", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "HOSPITAL") == {
        "Charité Universitätsmedizin Berlin"
    }


def test_organization_that_is_neither_is_dropped_entirely():
    """Layer 2 only emits ORGANIZATION so this layer can look for hospitals in it;
    an ORGANIZATION that is not a hospital is not part of the entity taxonomy."""
    text = "Der Bericht ging an die Siemens AG."
    spans = [Span(24, 34, "ORGANIZATION", 0.85, "presidio")]
    refined = _refine(text, spans)
    assert refined == []


def test_person_with_a_dr_prefix_becomes_doctor():
    text = "Untersucht von Dr. Miriam Falk."
    spans = [Span(19, 30, "PERSON", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "DOCTOR") == {"Miriam Falk"}


def test_person_with_a_prof_prefix_becomes_doctor():
    text = "Prof. Ulrike Brandt empfiehlt eine Kontrolle."
    spans = [Span(6, 19, "PERSON", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "DOCTOR") == {"Ulrike Brandt"}


def test_person_near_behandelnder_arzt_becomes_doctor():
    text = "Behandelnder Arzt ist Stefan Roth."
    spans = [Span(22, 33, "PERSON", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "DOCTOR") == {"Stefan Roth"}


def test_the_only_remaining_person_becomes_patient():
    text = "Herr Tobias Lang wurde von Dr. Miriam Falk untersucht."
    spans = [
        Span(5, 16, "PERSON", 0.85, "presidio"),
        Span(31, 42, "PERSON", 0.85, "presidio"),
    ]
    refined = _refine(text, spans)
    assert _typed(text, refined, "PATIENT") == {"Tobias Lang"}
    assert _typed(text, refined, "DOCTOR") == {"Miriam Falk"}


def test_most_referenced_person_wins_the_patient_label():
    text = "Lea Sommer stellte sich vor. Begleitet wurde Lea Sommer von Ingrid Sommer."
    spans = [
        Span(0, 10, "PERSON", 0.85, "presidio"),
        Span(44, 54, "PERSON", 0.85, "presidio"),
        Span(59, 72, "PERSON", 0.85, "presidio"),
    ]
    refined = _refine(text, spans)
    assert _typed(text, refined, "PATIENT") == {"Lea Sommer"}
    assert _typed(text, refined, "PERSON") == {"Ingrid Sommer"}


def test_ties_break_toward_the_earliest_mention():
    text = "Anna Weiss und Bernd Klein waren anwesend."
    spans = [
        Span(0, 10, "PERSON", 0.85, "presidio"),
        Span(15, 26, "PERSON", 0.85, "presidio"),
    ]
    refined = _refine(text, spans)
    assert _typed(text, refined, "PATIENT") == {"Anna Weiss"}
    assert _typed(text, refined, "PERSON") == {"Bernd Klein"}


def test_non_person_non_organization_spans_pass_through_untouched():
    text = "Aufnahme am 12.03.2024."
    original = Span(12, 22, "DATE", 1.0, "regex")
    assert _refine(text, [original]) == [original]


def test_retagged_spans_carry_the_custom_layer_name_and_original_offsets():
    text = "Untersucht von Dr. Miriam Falk."
    refined = _refine(text, [Span(19, 30, "PERSON", 0.85, "presidio")])
    assert refined == [Span(19, 30, "DOCTOR", 0.85, "custom")]


def test_this_layer_never_invents_new_spans():
    text = "Universitätsklinikum Heidelberg behandelte Lukas Berger am 12.03.2024."
    refined = _refine(text, [])
    assert refined == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_custom_recognizers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.detectors.custom_recognizers'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/detectors/custom_recognizers.py`**

```python
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from app.privacy_gateway.detectors.base import Span, normalize

LAYER_NAME = "custom"

# The four suffixes named verbatim in the design spec (§2) and the master spec (§5).
HOSPITAL_SUFFIX_PATTERN = re.compile(
    r"\b(?:Universitätsklinikum|Klinikum|Krankenhaus|MVZ)\b"
)

TITLE_PREFIX_PATTERN = re.compile(r"(?:Prof\.|Dr\.|PD|Priv\.-Doz\.|med\.)\s*$")
TITLE_LOOKBEHIND_CHARS = 25

TREATING_DOCTOR_PATTERN = re.compile(r"behandelnde[rn]?\s+(?:Arzt|Ärztin)", re.IGNORECASE)
TREATING_DOCTOR_PROXIMITY_CHARS = 60


class CustomRecognizers:
    """Layer 3: refine, don't rescan (design spec §2).

    Operates only on spans layer 2 already tagged PERSON or ORGANIZATION, and never
    produces a span offset that layers 1 and 2 did not produce.
    """

    layer_name = LAYER_NAME

    def __init__(self, hospital_names: frozenset[str]) -> None:
        self._hospital_names = hospital_names

    def refine(self, text: str, spans: Sequence[Span]) -> list[Span]:
        result: list[Span] = []
        unlabeled_person_indices: list[int] = []

        for span in spans:
            if span.entity_type == "ORGANIZATION":
                if self._is_hospital(text[span.start : span.end]):
                    result.append(_retag(span, "HOSPITAL"))
                # A non-hospital ORGANIZATION is not part of the entity taxonomy at
                # all — layer 2 emits it purely so this layer can look for hospitals
                # in it — so it is dropped rather than carried into risk scoring.
                continue
            if span.entity_type == "PERSON":
                if self._is_doctor(text, span):
                    result.append(_retag(span, "DOCTOR"))
                else:
                    unlabeled_person_indices.append(len(result))
                    result.append(span)
                continue
            result.append(span)

        patient_surface = _patient_surface(text, result, unlabeled_person_indices)
        for index in unlabeled_person_indices:
            span = result[index]
            surface = text[span.start : span.end]
            result[index] = _retag(
                span, "PATIENT" if surface == patient_surface else "PERSON"
            )
        return sorted(result)

    def _is_hospital(self, surface: str) -> bool:
        if HOSPITAL_SUFFIX_PATTERN.search(surface):
            return True
        return normalize(surface) in self._hospital_names

    @staticmethod
    def _is_doctor(text: str, span: Span) -> bool:
        prefix = text[max(0, span.start - TITLE_LOOKBEHIND_CHARS) : span.start]
        if TITLE_PREFIX_PATTERN.search(prefix):
            return True
        window_start = max(0, span.start - TREATING_DOCTOR_PROXIMITY_CHARS)
        return TREATING_DOCTOR_PATTERN.search(text, window_start, span.start) is not None


def _retag(span: Span, entity_type: str) -> Span:
    return Span(span.start, span.end, entity_type, span.confidence, LAYER_NAME)


def _patient_surface(text: str, spans: list[Span], indices: list[int]) -> str | None:
    """Design spec §2: "the first/most-referenced remaining unlabeled PERSON span in
    the message promoted to PATIENT". Most occurrences of the same exact surface
    string wins; ties break toward the surface that appears earliest.

    Matching is by exact surface string, which is the documented MVP coreference
    limitation: "Hans Müller" and a later "Herr Müller" are different surfaces and
    therefore different entities.
    """
    if not indices:
        return None
    surfaces = [text[spans[i].start : spans[i].end] for i in indices]
    counts = Counter(surfaces)
    # Built in reverse so the earliest position for each surface is what survives.
    first_position = {
        surface: position for position, surface in reversed(list(enumerate(surfaces)))
    }
    return min(counts, key=lambda surface: (-counts[surface], first_position[surface]))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_custom_recognizers.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/privacy_gateway/detectors/custom_recognizers.py backend/tests/unit/test_custom_recognizers.py
git commit -m "feat: add hospital gazetteer and doctor/patient custom recognizers"
```

---

## Task 6: `DetectorStack` — fixed-precedence composition of the three layers

**Files:**
- Create: `backend/app/privacy_gateway/detectors/stack.py`
- Test: `backend/tests/unit/test_detector_stack.py`

**Interfaces:**
- Consumes: `Span` (Task 2), `RegexDetector` (Task 2), `PresidioDetector` (Task 3), `CustomRecognizers` (Task 5).
- Produces: `DetectorStack(regex_detector: Detector, presidio_detector: Detector, refiner: SpanRefiner)` with `detect(self, text: str) -> list[Span]` returning spans in ascending offset order. Consumed by Task 9's `OutputGuard` and Task 10's `Pipeline`.

**Resolved ambiguity implemented here:** #9 (`detectors/stack.py` added to §8's package tree).

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_detector_stack.py`:

```python
import pytest

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack

GAZETTEER = frozenset({"charité universitätsmedizin berlin"})


class _FakeDetector:
    def __init__(self, layer_name, spans):
        self.layer_name = layer_name
        self._spans = spans
        self.claimed_at_call = None

    def detect(self, text, claimed):
        self.claimed_at_call = list(claimed)
        return [s for s in self._spans if not any(
            s.start < c.end and c.start < s.end for c in claimed
        )]


class _FakeRefiner:
    layer_name = "custom"

    def __init__(self):
        self.spans_at_call = None

    def refine(self, text, spans):
        self.spans_at_call = list(spans)
        return list(spans)


def test_presidio_layer_sees_the_regex_layers_spans_as_claimed():
    regex_span = Span(0, 10, "DATE", 1.0, "regex")
    regex = _FakeDetector("regex", [regex_span])
    presidio = _FakeDetector("presidio", [Span(0, 10, "PERSON", 0.85, "presidio")])
    refiner = _FakeRefiner()

    DetectorStack(regex, presidio, refiner).detect("0123456789 text")

    assert presidio.claimed_at_call == [regex_span]


def test_regex_wins_an_overlap_regardless_of_confidence():
    """Precedence is fixed, not confidence-based: even a 0.99-confidence Presidio
    span loses to the lower-numbered layer."""
    regex_span = Span(0, 10, "DATE", 1.0, "regex")
    regex = _FakeDetector("regex", [regex_span])
    presidio = _FakeDetector("presidio", [Span(2, 8, "PERSON", 0.99, "presidio")])

    spans = DetectorStack(regex, presidio, _FakeRefiner()).detect("0123456789 text")

    assert spans == [regex_span]


def test_the_refiner_receives_the_combined_span_set():
    regex_span = Span(0, 10, "DATE", 1.0, "regex")
    presidio_span = Span(11, 15, "PERSON", 0.85, "presidio")
    refiner = _FakeRefiner()

    DetectorStack(
        _FakeDetector("regex", [regex_span]),
        _FakeDetector("presidio", [presidio_span]),
        refiner,
    ).detect("0123456789 Anna")

    assert refiner.spans_at_call == [regex_span, presidio_span]


def test_result_is_sorted_by_offset():
    refiner = _FakeRefiner()
    spans = DetectorStack(
        _FakeDetector("regex", [Span(20, 30, "DATE", 1.0, "regex")]),
        _FakeDetector("presidio", [Span(0, 5, "PERSON", 0.85, "presidio")]),
        refiner,
    ).detect("x" * 40)
    assert [s.start for s in spans] == [0, 20]


@pytest.fixture(scope="module")
def real_stack():
    return DetectorStack(
        RegexDetector(), PresidioDetector(), CustomRecognizers(GAZETTEER)
    )


def test_end_to_end_precedence_on_a_real_clinical_sentence(real_stack):
    text = (
        "Patient Lukas Berger, Versichertennummer A123456789, wurde am 12.03.2024 "
        "im Universitätsklinikum Heidelberg von Dr. Anna Schmitt aufgenommen."
    )
    by_type = {}
    for span in real_stack.detect(text):
        by_type.setdefault(span.entity_type, set()).add(text[span.start : span.end])

    assert by_type["INSURANCE_NUMBER"] == {"A123456789"}
    assert by_type["DATE"] == {"12.03.2024"}
    assert "Lukas Berger" in by_type["PATIENT"]
    assert "Anna Schmitt" in by_type["DOCTOR"]
    assert "Universitätsklinikum Heidelberg" in by_type["HOSPITAL"]
    assert "ORGANIZATION" not in by_type


def test_the_stack_never_returns_overlapping_spans(real_stack):
    text = (
        "Elena Fischer ist 34 Jahre alt und kommt aus Leipzig. Sie ist erreichbar "
        "unter elena.fischer@example.com oder 0341 4455667."
    )
    spans = real_stack.detect(text)
    for earlier, later in zip(spans, spans[1:]):
        assert earlier.end <= later.start
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_detector_stack.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.detectors.stack'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/detectors/stack.py`**

```python
from __future__ import annotations

from app.privacy_gateway.detectors.base import Detector, Span, SpanRefiner


class DetectorStack:
    """The three detection layers in fixed precedence order (design spec §2).

    Regex > Presidio+spaCy > custom recognizers. Overlap is resolved by that order
    alone — confidence scores are never compared to decide which layer wins, so the
    result is deterministic for a given input.
    """

    def __init__(
        self,
        regex_detector: Detector,
        presidio_detector: Detector,
        refiner: SpanRefiner,
    ) -> None:
        self._regex_detector = regex_detector
        self._presidio_detector = presidio_detector
        self._refiner = refiner

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = list(self._regex_detector.detect(text, []))
        spans.extend(self._presidio_detector.detect(text, spans))
        spans.sort()
        return sorted(self._refiner.refine(text, spans))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_detector_stack.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/privacy_gateway/detectors/stack.py backend/tests/unit/test_detector_stack.py
git commit -m "feat: compose the three detector layers in fixed precedence order"
```

---

## Task 7: Deterministic risk scorer

**Files:**
- Create: `backend/app/privacy_gateway/risk_scoring/scorer.py`
- Test: `backend/tests/unit/test_risk_scorer.py`

**Interfaces:**
- Consumes: `Span`, `normalize` from `app.privacy_gateway.detectors.base` (Task 2); a `frozenset[str]` of normalized rare-disease names, from `get_rare_disease_names()` (Task 4) in production and injected in tests.
- Produces:
  - `RiskClassification` enum with members `DIRECT_IDENTIFIER`, `QUASI_IDENTIFIER`, `MEDICAL_CONTENT`.
  - `DIRECT_IDENTIFIER_TYPES`, `QUASI_IDENTIFIER_TYPES`, `CONFIDENCE_THRESHOLD = 0.4`, `QUASI_IDENTIFIER_ESCALATION_THRESHOLD = 2`, `MAX_DISEASE_NAME_WORDS = 8`.
  - `HighRiskMessageError`, `LowConfidenceSpanError` (both `Exception` subclasses).
  - `RiskAssessment(tokenize: tuple[Span, ...], quasi_identifier_categories: frozenset[str], rare_diseases: tuple[str, ...])`.
  - `RiskScorer(rare_disease_names: frozenset[str])` with `classify(span) -> RiskClassification`, `find_rare_diseases(text) -> tuple[str, ...]`, `score(text, spans) -> RiskAssessment`.

**Resolved ambiguities implemented here:** #1 (names are direct identifiers), #2 (`DATE_TIME` aliases to the `DATE` category), #4 (rare-disease matching against the whole message).

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_risk_scorer.py`:

```python
import pytest

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.risk_scoring.scorer import (
    CONFIDENCE_THRESHOLD,
    QUASI_IDENTIFIER_ESCALATION_THRESHOLD,
    HighRiskMessageError,
    LowConfidenceSpanError,
    RiskClassification,
    RiskScorer,
)

DISEASES = frozenset({"zystische fibrose", "huntington-krankheit"})


@pytest.fixture
def scorer():
    return RiskScorer(DISEASES)


def _span(entity_type, start=0, end=5, confidence=1.0):
    return Span(start, end, entity_type, confidence, "test")


def test_threshold_is_exactly_the_spec_value():
    assert CONFIDENCE_THRESHOLD == 0.4
    assert QUASI_IDENTIFIER_ESCALATION_THRESHOLD == 2


@pytest.mark.parametrize(
    "entity_type",
    ["INSURANCE_NUMBER", "PATIENT_NUMBER", "PHONE", "EMAIL", "PERSON", "DOCTOR", "PATIENT"],
)
def test_direct_identifiers(scorer, entity_type):
    assert scorer.classify(_span(entity_type)) is RiskClassification.DIRECT_IDENTIFIER


@pytest.mark.parametrize("entity_type", ["LOCATION", "HOSPITAL", "DATE", "AGE", "DATE_TIME"])
def test_quasi_identifiers(scorer, entity_type):
    assert scorer.classify(_span(entity_type)) is RiskClassification.QUASI_IDENTIFIER


def test_anything_else_is_medical_content(scorer):
    assert scorer.classify(_span("DIAGNOSIS")) is RiskClassification.MEDICAL_CONTENT


def test_finds_a_rare_disease_by_whole_word_ngram(scorer):
    found = scorer.find_rare_diseases("Diagnose Zystische Fibrose seit 2019.")
    assert found == ("zystische fibrose",)


def test_rare_disease_matching_is_case_and_punctuation_insensitive(scorer):
    assert scorer.find_rare_diseases("… ZYSTISCHE  FIBROSE!") == ("zystische fibrose",)


def test_rare_disease_matching_does_not_fire_on_a_partial_word(scorer):
    assert scorer.find_rare_diseases("Fallnummer 8830145 dokumentiert.") == ()


def test_two_quasi_categories_plus_rare_disease_escalates_to_high(scorer):
    text = "Der 19-jährige Patient aus Tübingen hat Zystische Fibrose."
    spans = [_span("AGE", 4, 14), _span("LOCATION", 27, 35)]
    with pytest.raises(HighRiskMessageError, match="quasi-identifier"):
        scorer.score(text, spans)


def test_one_quasi_category_plus_rare_disease_does_not_escalate(scorer):
    text = "Der 41-jährige Patient hat eine Huntington-Krankheit."
    spans = [_span("AGE", 4, 14)]
    assessment = scorer.score(text, spans)
    assert assessment.rare_diseases == ("huntington-krankheit",)
    assert assessment.quasi_identifier_categories == frozenset({"AGE"})


def test_two_quasi_categories_without_a_rare_disease_does_not_escalate(scorer):
    text = "Der 19-jährige Patient aus Tübingen wurde entlassen."
    spans = [_span("AGE", 4, 14), _span("LOCATION", 27, 35)]
    assessment = scorer.score(text, spans)
    assert assessment.rare_diseases == ()
    assert len(assessment.quasi_identifier_categories) == 2


def test_repeats_of_one_quasi_category_are_still_one_category(scorer):
    text = "Aus Tübingen nach Kiel verlegt, Diagnose Zystische Fibrose."
    spans = [_span("LOCATION", 4, 12), _span("LOCATION", 18, 22)]
    assessment = scorer.score(text, spans)
    assert assessment.quasi_identifier_categories == frozenset({"LOCATION"})


def test_date_and_date_time_count_as_one_category(scorer):
    """A regex DATE and a spaCy DATE_TIME are the same quasi-identifier category and
    must not on their own satisfy the two-category escalation rule."""
    text = "Am 11.05.2024, im Mai, Diagnose Zystische Fibrose."
    spans = [_span("DATE", 3, 13), _span("DATE_TIME", 18, 21)]
    assessment = scorer.score(text, spans)
    assert assessment.quasi_identifier_categories == frozenset({"DATE"})


def test_a_low_confidence_span_blocks_the_whole_message(scorer):
    spans = [_span("PERSON", 0, 5, confidence=0.39), _span("DATE", 10, 20)]
    with pytest.raises(LowConfidenceSpanError, match="0.4"):
        scorer.score("some text with a name and a date here", spans)


def test_a_span_exactly_at_the_threshold_is_accepted(scorer):
    spans = [_span("PERSON", 0, 5, confidence=0.4)]
    assert scorer.score("Anna wurde untersucht.", spans).tokenize == tuple(spans)


def test_confidence_is_checked_before_escalation(scorer):
    """Fail-closed ordering: a message that is both low-confidence and high-risk
    reports the low-confidence reason, and never gets as far as partial handling."""
    text = "Der 19-jährige Patient aus Tübingen hat Zystische Fibrose."
    spans = [_span("AGE", 4, 14), _span("LOCATION", 27, 35, confidence=0.2)]
    with pytest.raises(LowConfidenceSpanError):
        scorer.score(text, spans)


def test_direct_and_quasi_identifiers_are_tokenize_eligible_medical_content_is_not(scorer):
    spans = [_span("PATIENT", 0, 5), _span("LOCATION", 6, 11), _span("DIAGNOSIS", 12, 20)]
    assessment = scorer.score("Anna Kiel Migraene ok", spans)
    assert [s.entity_type for s in assessment.tokenize] == ["PATIENT", "LOCATION"]


def test_scoring_a_message_with_no_spans_is_not_an_error(scorer):
    assessment = scorer.score("Keine sensiblen Angaben.", [])
    assert assessment.tokenize == ()
    assert assessment.quasi_identifier_categories == frozenset()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_risk_scorer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.risk_scoring.scorer'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/risk_scoring/scorer.py`**

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from app.privacy_gateway.detectors.base import Span, normalize

# Design spec §3 lists INSURANCE_NUMBER / PATIENT_NUMBER / PHONE / EMAIL explicitly.
# PERSON / DOCTOR / PATIENT are added here: the spec's own catch-all would otherwise
# leave patient names as untokenized "medical content", which contradicts §7's
# corpus-wide invariant that no raw PII string survives sanitize(), and ADR-0009's
# PATIENT_ token examples. Names are always tokenized, never escalation inputs.
DIRECT_IDENTIFIER_TYPES = frozenset(
    {"INSURANCE_NUMBER", "PATIENT_NUMBER", "PHONE", "EMAIL", "PERSON", "DOCTOR", "PATIENT"}
)

# Design spec §3, matching §5's own list verbatim: "city, hospital, exact date, age".
# DATE_TIME is layer 2's name for the same category as layer 1's DATE.
QUASI_IDENTIFIER_TYPES = frozenset({"LOCATION", "HOSPITAL", "DATE", "AGE", "DATE_TIME"})
_QUASI_CATEGORY_ALIASES = {"DATE_TIME": "DATE"}

# Presidio's own common default. Deliberately a module constant and not a Settings
# field: a fail-closed threshold that an environment variable can relax is not
# fail-closed.
CONFIDENCE_THRESHOLD = 0.4

QUASI_IDENTIFIER_ESCALATION_THRESHOLD = 2

# The longest ORDO German label is well under this; capping the n-gram window keeps
# rare-disease lookup linear in message length.
MAX_DISEASE_NAME_WORDS = 8


class RiskClassification(Enum):
    DIRECT_IDENTIFIER = "direct_identifier"
    QUASI_IDENTIFIER = "quasi_identifier"
    MEDICAL_CONTENT = "medical_content"


class HighRiskMessageError(Exception):
    """Two or more quasi-identifier categories co-occur with a rare-disease mention.

    Design spec §3: the whole message is rejected — sanitize() raises rather than
    returning partially-sanitized text. There is no human-review or override
    workflow yet, so this is the only possible outcome.
    """


class LowConfidenceSpanError(Exception):
    """A detected span scored below CONFIDENCE_THRESHOLD.

    Design spec §3: it blocks the whole message — fail-closed, not partial.
    """


@dataclass(frozen=True)
class RiskAssessment:
    tokenize: tuple[Span, ...]
    quasi_identifier_categories: frozenset[str]
    rare_diseases: tuple[str, ...]


class RiskScorer:
    """Deterministic, not ML (design spec §3)."""

    def __init__(self, rare_disease_names: frozenset[str]) -> None:
        self._rare_disease_names = rare_disease_names

    def classify(self, span: Span) -> RiskClassification:
        if span.entity_type in DIRECT_IDENTIFIER_TYPES:
            return RiskClassification.DIRECT_IDENTIFIER
        if span.entity_type in QUASI_IDENTIFIER_TYPES:
            return RiskClassification.QUASI_IDENTIFIER
        return RiskClassification.MEDICAL_CONTENT

    def find_rare_diseases(self, text: str) -> tuple[str, ...]:
        """Whole-word n-gram lookup against the ORDO-derived name list.

        Design spec §3 says "substring/lemma match against the message's
        medical-content spans", but no detector layer produces medical-content
        spans, so the whole message is scanned. Matching is on normalized whole-word
        n-grams rather than raw substrings, so a name like "Thymom" cannot fire on a
        longer unrelated word.
        """
        words = normalize(text).split()
        found: list[str] = []
        for start in range(len(words)):
            for length in range(1, MAX_DISEASE_NAME_WORDS + 1):
                if start + length > len(words):
                    break
                candidate = " ".join(words[start : start + length])
                if candidate in self._rare_disease_names and candidate not in found:
                    found.append(candidate)
        return tuple(found)

    def score(self, text: str, spans: Sequence[Span]) -> RiskAssessment:
        for span in spans:
            if span.confidence < CONFIDENCE_THRESHOLD:
                raise LowConfidenceSpanError(
                    f"span {span.entity_type} at [{span.start}:{span.end}] from layer "
                    f"{span.source_layer!r} has confidence {span.confidence}, below the "
                    f"{CONFIDENCE_THRESHOLD} threshold; the whole message is rejected "
                    "rather than partially sanitized"
                )

        categories = frozenset(
            _QUASI_CATEGORY_ALIASES.get(span.entity_type, span.entity_type)
            for span in spans
            if self.classify(span) is RiskClassification.QUASI_IDENTIFIER
        )
        rare_diseases = self.find_rare_diseases(text)

        if len(categories) >= QUASI_IDENTIFIER_ESCALATION_THRESHOLD and rare_diseases:
            raise HighRiskMessageError(
                f"{len(categories)} quasi-identifier categories "
                f"({', '.join(sorted(categories))}) co-occur with rare-disease "
                f"mention(s) ({', '.join(rare_diseases)}); the whole message is "
                "rejected rather than partially sanitized"
            )

        tokenize = tuple(
            span
            for span in spans
            if self.classify(span) is not RiskClassification.MEDICAL_CONTENT
        )
        return RiskAssessment(
            tokenize=tokenize,
            quasi_identifier_categories=categories,
            rare_diseases=rare_diseases,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_risk_scorer.py -v`
Expected: PASS (28 passed — the two parametrized tests expand to 7 and 5 cases).

- [ ] **Step 5: Commit**

```bash
git add backend/app/privacy_gateway/risk_scoring/scorer.py backend/tests/unit/test_risk_scorer.py
git commit -m "feat: add the deterministic risk scorer with fail-closed escalation"
```

---

## Task 8: Pseudonymizer

**Files:**
- Create: `backend/app/privacy_gateway/pseudonymization/pseudonymizer.py`
- Test: `backend/tests/privacy_invariants/test_pseudonymizer.py`

**Interfaces:**
- Consumes: `Span` (Task 2); `TokenVault.create_mapping(tenant_id, conversation_id, entity_type, original_value) -> str` from `app.privacy_gateway.token_vault.vault`.
- Produces: `Pseudonymizer(vault: TokenVault)` with `apply(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str, spans: Sequence[Span]) -> str`.

**Resolved ambiguity implemented here:** #7 (within-message token reuse only).

**Prerequisite:** `docker compose up -d postgres` running and `alembic upgrade head` applied — this task's tests write real `token_mappings` rows.

- [ ] **Step 1: Write the failing test**

`backend/tests/privacy_invariants/test_pseudonymizer.py`:

```python
import re
import uuid

import pytest

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

TOKEN_SHAPE = re.compile(r"^[A-Z][A-Z_]*_[0-9A-F]{10}$")


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


@pytest.fixture
def scope(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub", email="doc@example.com", role="doctor"
        )
        conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_id = conversation.id
    return tenant_id, conversation_id


@pytest.fixture
def pseudonymizer(key_provider):
    return Pseudonymizer(TokenVault(key_provider))


def test_replaces_a_span_with_a_vault_token(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    text = "Patient Lukas Berger wurde aufgenommen."
    spans = [Span(8, 20, "PATIENT", 0.85, "custom")]

    result = pseudonymizer.apply(tenant_id, conversation_id, text, spans)

    assert "Lukas Berger" not in result
    assert result.startswith("Patient PATIENT_")
    assert result.endswith(" wurde aufgenommen.")


def test_multiple_spans_keep_their_offsets_correct(scope, pseudonymizer):
    """Descending start order (design spec §4) so an earlier replacement never
    shifts the offsets of a span that has not been processed yet."""
    tenant_id, conversation_id = scope
    text = "Lukas Berger, A123456789, am 12.03.2024."
    spans = [
        Span(0, 12, "PATIENT", 0.85, "custom"),
        Span(14, 24, "INSURANCE_NUMBER", 1.0, "regex"),
        Span(29, 39, "DATE", 1.0, "regex"),
    ]

    result = pseudonymizer.apply(tenant_id, conversation_id, text, spans)

    assert "Lukas Berger" not in result
    assert "A123456789" not in result
    assert "12.03.2024" not in result
    assert result.count(", ") == 2
    assert result.endswith(".")


def test_spans_supplied_out_of_order_are_still_replaced_correctly(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    text = "Lukas Berger, A123456789, am 12.03.2024."
    spans = [
        Span(29, 39, "DATE", 1.0, "regex"),
        Span(0, 12, "PATIENT", 0.85, "custom"),
        Span(14, 24, "INSURANCE_NUMBER", 1.0, "regex"),
    ]

    result = pseudonymizer.apply(tenant_id, conversation_id, text, spans)

    assert "Lukas Berger" not in result
    assert "A123456789" not in result
    assert "12.03.2024" not in result


def test_the_same_value_gets_the_same_token_within_one_call(scope, pseudonymizer):
    """The MVP's exact-string-match determinism, scoped to a single message: the
    vault mints a fresh random token per create_mapping call, so identical values
    are de-duplicated here rather than in the vault."""
    tenant_id, conversation_id = scope
    text = "Lea Sommer kam. Begleitet wurde Lea Sommer von Ingrid Sommer."
    spans = [
        Span(0, 10, "PATIENT", 0.85, "custom"),
        Span(32, 42, "PATIENT", 0.85, "custom"),
        Span(46, 59, "PERSON", 0.85, "custom"),
    ]

    result = pseudonymizer.apply(tenant_id, conversation_id, text, spans)
    tokens = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", result)

    assert len(tokens) == 3
    assert tokens[0] == tokens[1]
    assert tokens[2] != tokens[0]


def test_different_surfaces_of_the_same_person_get_different_tokens(scope, pseudonymizer):
    """The documented MVP coreference limitation, made visible."""
    tenant_id, conversation_id = scope
    text = "Hans Müller kam. Später berichtete Herr Müller über Schmerzen."
    spans = [
        Span(0, 11, "PATIENT", 0.85, "custom"),
        Span(34, 45, "PERSON", 0.85, "custom"),
    ]

    result = pseudonymizer.apply(tenant_id, conversation_id, text, spans)
    tokens = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", result)

    assert len(set(tokens)) == 2


def test_tokens_match_the_adr_0009_shape(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    result = pseudonymizer.apply(
        tenant_id, conversation_id, "Lukas Berger", [Span(0, 12, "PATIENT", 0.85, "custom")]
    )
    assert TOKEN_SHAPE.match(result)


def test_issued_tokens_resolve_back_to_the_original_values(scope, key_provider):
    tenant_id, conversation_id = scope
    vault = TokenVault(key_provider)
    text = "Lukas Berger wurde aufgenommen."

    result = Pseudonymizer(vault).apply(
        tenant_id, conversation_id, text, [Span(0, 12, "PATIENT", 0.85, "custom")]
    )
    token = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", result)[0]

    assert vault.resolve_token(tenant_id, conversation_id, token) == "Lukas Berger"


def test_no_spans_leaves_the_text_untouched(scope, pseudonymizer):
    tenant_id, conversation_id = scope
    text = "Keine sensiblen Angaben."
    assert pseudonymizer.apply(tenant_id, conversation_id, text, []) == text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/privacy_invariants/test_pseudonymizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.pseudonymization.pseudonymizer'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/pseudonymization/pseudonymizer.py`**

```python
from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.token_vault.vault import TokenVault


class Pseudonymizer:
    """Replaces every tokenize-eligible span with a TokenVault token (design spec §4)."""

    def __init__(self, vault: TokenVault) -> None:
        self._vault = vault

    def apply(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        text: str,
        spans: Sequence[Span],
    ) -> str:
        # TokenVault.create_mapping mints a fresh random token on every call and
        # `encrypted_value` is not searchable, so exact-string-match determinism has
        # to live here. This cache gives it within one message; cross-message
        # determinism inside a conversation would need a deterministic (HMAC) index
        # column on token_mappings and is out of scope for this plan.
        issued: dict[tuple[str, str], str] = {}
        result = text
        # Descending start order so an earlier replacement never shifts the offsets
        # of a span that has not been processed yet (design spec §4).
        for span in sorted(spans, key=lambda item: item.start, reverse=True):
            original_value = text[span.start : span.end]
            key = (span.entity_type, original_value)
            token = issued.get(key)
            if token is None:
                token = self._vault.create_mapping(
                    tenant_id, conversation_id, span.entity_type, original_value
                )
                issued[key] = token
            result = result[: span.start] + token + result[span.end :]
        return result
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/privacy_invariants/test_pseudonymizer.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/privacy_gateway/pseudonymization/pseudonymizer.py backend/tests/privacy_invariants/test_pseudonymizer.py
git commit -m "feat: add the pseudonymizer wiring risk-scored spans to the token vault"
```

---

## Task 9: Output guard (return path)

**Files:**
- Create: `backend/app/privacy_gateway/output_guard/guard.py`
- Test: `backend/tests/privacy_invariants/test_output_guard.py`

**Interfaces:**
- Consumes: `Span` (Task 2), `DetectorStack` (Task 6), `TokenVault.resolve_tokens(tenant_id, conversation_id, tokens: list[str]) -> dict[str, str]`.
- Produces:
  - `TOKEN_PATTERN: re.Pattern[str]` = `\b[A-Z][A-Z_]*_[0-9A-F]{10}\b`.
  - `LeakageDetectedError`, `UnresolvedTokenError` (both `Exception` subclasses).
  - `OutputGuard(detector_stack: DetectorStack, vault: TokenVault)` with `restore(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str) -> str`.

**Prerequisite:** Postgres running with migrations applied.

- [ ] **Step 1: Write the failing test**

`backend/tests/privacy_invariants/test_output_guard.py`:

```python
import uuid

import pytest

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import (
    TOKEN_PATTERN,
    LeakageDetectedError,
    OutputGuard,
    UnresolvedTokenError,
)
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _new_scope(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}",
            email="doc@example.com", role="doctor",
        )
        conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_id = conversation.id
    return tenant_id, conversation_id


@pytest.fixture
def scope(key_provider):
    return _new_scope(key_provider)


@pytest.fixture(scope="module")
def detector_stack():
    return DetectorStack(RegexDetector(), PresidioDetector(), CustomRecognizers(frozenset()))


@pytest.fixture
def guard(detector_stack, key_provider):
    return OutputGuard(detector_stack, TokenVault(key_provider))


def test_token_pattern_matches_the_adr_0009_shape():
    assert TOKEN_PATTERN.fullmatch("PATIENT_A1B2C3D4E5")
    assert TOKEN_PATTERN.fullmatch("INSURANCE_NUMBER_0123456789")
    assert not TOKEN_PATTERN.fullmatch("PATIENT_A1B2C3D4")
    assert not TOKEN_PATTERN.fullmatch("patient_A1B2C3D4E5")


def test_resolves_an_authorized_token_back_to_its_value(scope, guard, key_provider):
    tenant_id, conversation_id = scope
    token = TokenVault(key_provider).create_mapping(
        tenant_id, conversation_id, "PATIENT", "Lukas Berger"
    )

    restored = guard.restore(tenant_id, conversation_id, f"Die Behandlung von {token} verlief gut.")

    assert restored == "Die Behandlung von Lukas Berger verlief gut."


def test_resolves_several_tokens_in_one_output(scope, guard, key_provider):
    tenant_id, conversation_id = scope
    vault = TokenVault(key_provider)
    patient = vault.create_mapping(tenant_id, conversation_id, "PATIENT", "Lukas Berger")
    date = vault.create_mapping(tenant_id, conversation_id, "DATE", "12.03.2024")

    restored = guard.restore(tenant_id, conversation_id, f"{patient} kam am {date} an.")

    assert restored == "Lukas Berger kam am 12.03.2024 an."


def test_raw_pii_in_llm_output_is_a_leakage_event(scope, guard):
    tenant_id, conversation_id = scope
    with pytest.raises(LeakageDetectedError, match="INSURANCE_NUMBER"):
        guard.restore(
            tenant_id, conversation_id, "Die Versichertennummer lautet A123456789."
        )


def test_a_leaked_name_is_caught_too(scope, guard):
    tenant_id, conversation_id = scope
    with pytest.raises(LeakageDetectedError):
        guard.restore(tenant_id, conversation_id, "Lukas Berger wurde entlassen.")


def test_leakage_raises_rather_than_returning_partial_output(scope, guard, key_provider):
    tenant_id, conversation_id = scope
    token = TokenVault(key_provider).create_mapping(
        tenant_id, conversation_id, "PATIENT", "Lukas Berger"
    )
    with pytest.raises(LeakageDetectedError):
        guard.restore(tenant_id, conversation_id, f"{token} am 12.03.2024 entlassen.")


def test_a_fabricated_token_fails_closed(scope, guard):
    """The prompt-injection case from master spec §5: "reveal the mapping for
    PATIENT_7F82A" produces a token-shaped string that was never issued."""
    tenant_id, conversation_id = scope
    with pytest.raises(UnresolvedTokenError, match="PATIENT_0000000000"):
        guard.restore(tenant_id, conversation_id, "Der Wert von PATIENT_0000000000 ist unklar.")


def test_a_token_from_another_tenant_never_resolves(guard, key_provider):
    tenant_a, conversation_a = _new_scope(key_provider)
    tenant_b, _ = _new_scope(key_provider)
    token = TokenVault(key_provider).create_mapping(
        tenant_a, conversation_a, "PATIENT", "Lukas Berger"
    )

    with pytest.raises(UnresolvedTokenError, match=token):
        guard.restore(tenant_b, conversation_a, f"Bericht zu {token}.")


def test_a_token_from_another_conversation_never_resolves(guard, key_provider):
    tenant_id, conversation_a = _new_scope(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}",
            email="doc2@example.com", role="doctor",
        )
        conversation_b = ConversationRepository(session).create(tenant_id, user.id).id

    token = TokenVault(key_provider).create_mapping(
        tenant_id, conversation_a, "PATIENT", "Lukas Berger"
    )

    with pytest.raises(UnresolvedTokenError, match=token):
        guard.restore(tenant_id, conversation_b, f"Bericht zu {token}.")


def test_output_with_no_tokens_and_no_pii_passes_through(scope, guard):
    tenant_id, conversation_id = scope
    text = "Die Befunde sind unauffällig und es sind keine weiteren Schritte nötig."
    assert guard.restore(tenant_id, conversation_id, text) == text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/privacy_invariants/test_output_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.output_guard.guard'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/output_guard/guard.py`**

```python
from __future__ import annotations

import re
import uuid

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.token_vault.vault import TokenVault

# ADR-0009's token format: entity type + secrets.token_hex(5).upper(). The design
# spec's `[TYPE_XXXXX]` notation is shorthand for this shape — there are no literal
# square brackets in a token.
TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z_]*_[0-9A-F]{10}\b")


class LeakageDetectedError(Exception):
    """The LLM's raw output contains detected PII that is not a legitimate token.

    Design spec §5 step 1: fail-closed — raise, do not return partial output. No
    audit_events row is written here; audit_events repository access is out of scope
    per the data-model plan, so this typed exception is what the caller logs.
    """


class UnresolvedTokenError(Exception):
    """A token-shaped substring in the LLM output did not resolve for this
    (tenant_id, conversation_id).

    Design spec §5 step 4: a token the LLM could not have legitimately produced —
    another tenant's, another conversation's, or fabricated by prompt injection.
    Raise rather than returning it opaque or silently dropping it.
    """


class OutputGuard:
    def __init__(self, detector_stack: DetectorStack, vault: TokenVault) -> None:
        self._detector_stack = detector_stack
        self._vault = vault

    def restore(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
    ) -> str:
        token_bounds = [
            (match.start(), match.end()) for match in TOKEN_PATTERN.finditer(llm_output)
        ]

        # Step 1: re-run the full detector stack; anything detected outside a token is
        # raw-looking PII the LLM produced or leaked.
        leaked = [
            span
            for span in self._detector_stack.detect(llm_output)
            if not _inside_a_token(span, token_bounds)
        ]
        if leaked:
            raise LeakageDetectedError(
                "raw-looking PII in LLM output: "
                + ", ".join(
                    f"{span.entity_type} at [{span.start}:{span.end}]" for span in leaked
                )
                + "; the response is rejected rather than partially returned"
            )

        # Steps 2 and 3: extract the token-shaped substrings and let TokenVault apply
        # the (tenant_id, conversation_id) authorization scope.
        tokens = [llm_output[start:end] for start, end in token_bounds]
        resolved = self._vault.resolve_tokens(tenant_id, conversation_id, tokens)

        # Step 4: anything still unresolved is fail-closed.
        unresolved = sorted({token for token in tokens if token not in resolved})
        if unresolved:
            raise UnresolvedTokenError(
                "token(s) not issued for this tenant and conversation: "
                + ", ".join(unresolved)
                + "; the response is rejected rather than returned opaque"
            )

        restored = llm_output
        for start, end in reversed(token_bounds):
            restored = restored[:start] + resolved[llm_output[start:end]] + restored[end:]
        return restored


def _inside_a_token(span: Span, token_bounds: list[tuple[int, int]]) -> bool:
    return any(start <= span.start and span.end <= end for start, end in token_bounds)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/privacy_invariants/test_output_guard.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/privacy_gateway/output_guard/guard.py backend/tests/privacy_invariants/test_output_guard.py
git commit -m "feat: add the output guard with fail-closed leakage and token checks"
```

---

## Task 10: `pipeline.py` — `sanitize()` and `deanonymize()`

**Files:**
- Create: `backend/app/privacy_gateway/pipeline.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/privacy_invariants/test_pipeline.py`

**Interfaces:**
- Consumes: `DetectorStack` (Task 6), `RegexDetector` (Task 2), `PresidioDetector` (Task 3), `CustomRecognizers` (Task 5), `RiskScorer`/`HighRiskMessageError`/`LowConfidenceSpanError` (Task 7), `Pseudonymizer` (Task 8), `OutputGuard`/`LeakageDetectedError`/`UnresolvedTokenError` (Task 9), `get_rare_disease_names()`/`get_hospital_names()` (Task 4), `Settings.master_key_path` and `Settings.warm_reference_data_on_startup` (Task 1).
- Produces:
  - `Pipeline(detector_stack, risk_scorer, pseudonymizer, output_guard)` with `sanitize(tenant_id, conversation_id, text) -> str` and `deanonymize(tenant_id, conversation_id, llm_output) -> str`.
  - `get_pipeline() -> Pipeline` (`lru_cache`d).
  - Module-level `sanitize(tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str) -> str` and `deanonymize(tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str) -> str` — the public entry points.
  - The four pipeline exceptions re-exported from `app.privacy_gateway.pipeline`.

**Prerequisite:** Postgres running with migrations applied.

- [ ] **Step 1: Write the failing test**

`backend/tests/privacy_invariants/test_pipeline.py`:

```python
import re
import uuid

import pytest

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import OutputGuard
from app.privacy_gateway.pipeline import (
    HighRiskMessageError,
    LeakageDetectedError,
    Pipeline,
    UnresolvedTokenError,
)
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.scorer import RiskScorer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

DISEASES = frozenset({"zystische fibrose"})
HOSPITALS = frozenset({"charité universitätsmedizin berlin"})


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


@pytest.fixture
def scope(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}",
            email="doc@example.com", role="doctor",
        )
        conversation_id = ConversationRepository(session).create(tenant_id, user.id).id
    return tenant_id, conversation_id


@pytest.fixture(scope="module")
def detector_stack():
    return DetectorStack(RegexDetector(), PresidioDetector(), CustomRecognizers(HOSPITALS))


@pytest.fixture
def pipeline(detector_stack, key_provider):
    vault = TokenVault(key_provider)
    return Pipeline(
        detector_stack=detector_stack,
        risk_scorer=RiskScorer(DISEASES),
        pseudonymizer=Pseudonymizer(vault),
        output_guard=OutputGuard(detector_stack, vault),
    )


def test_sanitize_removes_every_identifier(scope, pipeline):
    tenant_id, conversation_id = scope
    text = (
        "Patient Lukas Berger, Versichertennummer A123456789, wurde am 12.03.2024 "
        "im Universitätsklinikum Heidelberg von Dr. Anna Schmitt aufgenommen."
    )

    sanitized = pipeline.sanitize(tenant_id, conversation_id, text)

    for raw in ("Lukas Berger", "A123456789", "12.03.2024",
                "Universitätsklinikum Heidelberg", "Anna Schmitt"):
        assert raw not in sanitized


def test_sanitize_keeps_the_non_identifying_text(scope, pipeline):
    tenant_id, conversation_id = scope
    sanitized = pipeline.sanitize(
        tenant_id, conversation_id, "Patient Lukas Berger wurde aufgenommen."
    )
    assert sanitized.startswith("Patient ")
    assert sanitized.endswith(" wurde aufgenommen.")


def test_sanitize_raises_on_the_high_risk_combination(scope, pipeline):
    """Design spec §3: age + city + date + rare disease rejects the whole message."""
    tenant_id, conversation_id = scope
    text = (
        "Der 19-jährige Patient Simon Kraus aus Tübingen wurde am 11.05.2024 "
        "mit der Diagnose Zystische Fibrose vorgestellt."
    )
    with pytest.raises(HighRiskMessageError):
        pipeline.sanitize(tenant_id, conversation_id, text)


def test_a_rejected_message_writes_no_partially_sanitized_output(scope, pipeline):
    tenant_id, conversation_id = scope
    text = (
        "Der 19-jährige Patient Simon Kraus aus Tübingen wurde am 11.05.2024 "
        "mit der Diagnose Zystische Fibrose vorgestellt."
    )
    with pytest.raises(HighRiskMessageError) as excinfo:
        pipeline.sanitize(tenant_id, conversation_id, text)
    assert "Simon Kraus" not in str(excinfo.value)


def test_round_trip_restores_the_original_values(scope, pipeline):
    tenant_id, conversation_id = scope
    text = "Patient Lukas Berger wurde am 12.03.2024 aufgenommen."

    sanitized = pipeline.sanitize(tenant_id, conversation_id, text)
    restored = pipeline.deanonymize(tenant_id, conversation_id, sanitized)

    assert restored == text


def test_deanonymize_rejects_leaked_pii(scope, pipeline):
    tenant_id, conversation_id = scope
    with pytest.raises(LeakageDetectedError):
        pipeline.deanonymize(
            tenant_id, conversation_id, "Die Versichertennummer lautet A123456789."
        )


def test_deanonymize_rejects_a_fabricated_token(scope, pipeline):
    tenant_id, conversation_id = scope
    with pytest.raises(UnresolvedTokenError):
        pipeline.deanonymize(tenant_id, conversation_id, "Siehe PATIENT_0000000000.")


def test_sanitized_output_contains_only_well_formed_tokens(scope, pipeline):
    tenant_id, conversation_id = scope
    sanitized = pipeline.sanitize(
        tenant_id, conversation_id, "Lukas Berger, A123456789, am 12.03.2024."
    )
    tokens = re.findall(r"[A-Z][A-Z_]*_[0-9A-F]{10}", sanitized)
    assert len(tokens) == 3
    assert {token.rsplit("_", 1)[0] for token in tokens} == {
        "PATIENT", "INSURANCE_NUMBER", "DATE"
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/privacy_invariants/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.privacy_gateway.pipeline'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/pipeline.py`**

```python
from __future__ import annotations

import uuid
from functools import lru_cache

from app.config import get_settings
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import (
    LeakageDetectedError,
    OutputGuard,
    UnresolvedTokenError,
)
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.reference_data import (
    get_hospital_names,
    get_rare_disease_names,
)
from app.privacy_gateway.risk_scoring.scorer import (
    HighRiskMessageError,
    LowConfidenceSpanError,
    RiskScorer,
)
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

__all__ = [
    "HighRiskMessageError",
    "LeakageDetectedError",
    "LowConfidenceSpanError",
    "Pipeline",
    "UnresolvedTokenError",
    "deanonymize",
    "get_pipeline",
    "sanitize",
]


class Pipeline:
    def __init__(
        self,
        detector_stack: DetectorStack,
        risk_scorer: RiskScorer,
        pseudonymizer: Pseudonymizer,
        output_guard: OutputGuard,
    ) -> None:
        self._detector_stack = detector_stack
        self._risk_scorer = risk_scorer
        self._pseudonymizer = pseudonymizer
        self._output_guard = output_guard

    def sanitize(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str
    ) -> str:
        """Design spec §4: runs all three detector layers, risk-scores every span,
        raises on HIGH-risk or low-confidence, else returns the fully pseudonymized
        string ready to send to an LLM.
        """
        spans = self._detector_stack.detect(text)
        assessment = self._risk_scorer.score(text, spans)
        return self._pseudonymizer.apply(
            tenant_id, conversation_id, text, assessment.tokenize
        )

    def deanonymize(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
    ) -> str:
        """Design spec §5: leakage scan, then authorization-checked token resolution."""
        return self._output_guard.restore(tenant_id, conversation_id, llm_output)


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    """Build the process-wide pipeline once.

    Design spec §6: the ORDO OWL and Krankenhausverzeichnis xlsx are parsed here,
    once, into in-memory sets — never per request.
    """
    detector_stack = DetectorStack(
        RegexDetector(), PresidioDetector(), CustomRecognizers(get_hospital_names())
    )
    vault = TokenVault(FileSecretKeyProvider(get_settings().master_key_path))
    return Pipeline(
        detector_stack=detector_stack,
        risk_scorer=RiskScorer(get_rare_disease_names()),
        pseudonymizer=Pseudonymizer(vault),
        output_guard=OutputGuard(detector_stack, vault),
    )


def sanitize(tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str) -> str:
    return get_pipeline().sanitize(tenant_id, conversation_id, text)


def deanonymize(
    tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
) -> str:
    return get_pipeline().deanonymize(tenant_id, conversation_id, llm_output)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/privacy_invariants/test_pipeline.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Warm the pipeline at application startup**

Replace `backend/app/main.py` in full with:

```python
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
```

- [ ] **Step 6: Run the full backend suite and the linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; all tests pass. `tests/unit/test_health.py` still passes because `conftest.py` sets `WARM_REFERENCE_DATA_ON_STARTUP=false` (Task 1, Step 7).

- [ ] **Step 7: Verify the warm-up actually runs in a real container**

Run:
```bash
docker compose up -d --build backend
docker compose logs backend | tail -20
curl -s http://localhost:8000/health
```
Expected: the container starts with no traceback and `/health` responds. Startup is slow the first time (spaCy model load plus the ORDO parse measured in Task 4).

- [ ] **Step 8: Commit**

```bash
git add backend/app/privacy_gateway/pipeline.py backend/app/main.py backend/tests/privacy_invariants/test_pipeline.py
git commit -m "feat: orchestrate detection, scoring and pseudonymization in pipeline.py"
```

---

## Task 11: Golden corpus — 22 synthetic German clinical notes

**Files:**
- Create: `evaluation/README.md`
- Create: `evaluation/golden_corpus/note_001.json` … `note_022.json` (22 files)
- Test: `backend/tests/unit/test_golden_corpus.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure data).
- Produces: `evaluation/golden_corpus/*.json`, each an object with:
  - `id: str` — matches the filename stem.
  - `text: str` — the note.
  - `expected_outcome: "sanitized" | "high_risk_rejected"`.
  - `rare_diseases: list[str]` — exact ORDO German labels present in `text`.
  - `entities: list[{entity_type: str, text: str, start: int, end: int}]` — ground-truth spans, where `text[start:end] == entity.text`.

  Consumed by Task 12.

**Every offset below was computed programmatically and asserted, not hand-counted.** `note_022` is the deliberate quasi-identifier combination case (master spec §8's "age + city + rare disease + date"); `note_021` is its negative control (rare disease, one quasi category); `note_012` is the exact-string-match coreference-limitation case.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_golden_corpus.py`:

```python
import json
from collections import Counter
from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "golden_corpus"

ENTITY_TYPES = {
    "INSURANCE_NUMBER", "PATIENT_NUMBER", "PHONE", "EMAIL", "DATE", "AGE",
    "PERSON", "LOCATION", "HOSPITAL", "DOCTOR", "PATIENT",
}


def load_corpus():
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(CORPUS_DIR.glob("*.json"))]


def test_corpus_size_is_in_the_spec_range():
    notes = load_corpus()
    assert 20 <= len(notes) <= 25


def test_every_note_id_matches_its_filename():
    for path in sorted(CORPUS_DIR.glob("*.json")):
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == path.stem


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_every_annotation_offset_matches_its_surface_string(note):
    for entity in note["entities"]:
        assert note["text"][entity["start"] : entity["end"]] == entity["text"], (
            f"{note['id']}: {entity}"
        )


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_every_annotation_uses_a_known_entity_type(note):
    for entity in note["entities"]:
        assert entity["entity_type"] in ENTITY_TYPES


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_annotations_do_not_overlap(note):
    spans = sorted((e["start"], e["end"]) for e in note["entities"])
    for (_, earlier_end), (later_start, _) in zip(spans, spans[1:]):
        assert earlier_end <= later_start


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_declared_rare_diseases_actually_appear_in_the_text(note):
    for disease in note["rare_diseases"]:
        assert disease in note["text"]


def test_every_entity_type_is_covered_at_least_twice():
    counts = Counter(
        entity["entity_type"] for note in load_corpus() for entity in note["entities"]
    )
    for entity_type in ENTITY_TYPES:
        assert counts[entity_type] >= 2, f"{entity_type} covered {counts[entity_type]} time(s)"


def test_exactly_one_note_is_the_high_risk_combination_case():
    rejected = [n for n in load_corpus() if n["expected_outcome"] == "high_risk_rejected"]
    assert len(rejected) == 1
    note = rejected[0]
    categories = {
        e["entity_type"] for e in note["entities"]
        if e["entity_type"] in {"AGE", "LOCATION", "DATE", "HOSPITAL"}
    }
    assert len(categories) >= 2
    assert note["rare_diseases"]


def test_only_the_high_risk_note_combines_a_rare_disease_with_two_quasi_categories():
    for note in load_corpus():
        if not note["rare_diseases"]:
            continue
        categories = {
            e["entity_type"] for e in note["entities"]
            if e["entity_type"] in {"AGE", "LOCATION", "DATE", "HOSPITAL"}
        }
        if len(categories) >= 2:
            assert note["expected_outcome"] == "high_risk_rejected", note["id"]


def test_the_coreference_limitation_case_exists():
    """One note refers to the same human three times under two distinct surfaces, so
    the exact-string-match limitation shows up in test output rather than passing
    silently."""
    note = json.loads((CORPUS_DIR / "note_012.json").read_text(encoding="utf-8"))
    surfaces = [e["text"] for e in note["entities"] if e["entity_type"] in {"PATIENT", "PERSON"}]
    assert len(surfaces) == 3
    assert len(set(surfaces)) == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_golden_corpus.py -v`
Expected: FAIL — `load_corpus()` returns `[]` and `test_corpus_size_is_in_the_spec_range` fails with `assert 20 <= 0`.

- [ ] **Step 3: Create `evaluation/README.md`**

    # evaluation/

    `golden_corpus/` holds the hand-written synthetic German clinical notes described
    in `docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md` §8 and
    `docs/superpowers/specs/2026-08-15-detection-pseudonymization-pipeline-design.md` §7.

    Every note is entirely synthetic. Names, numbers, dates and email addresses are
    invented; hospital names are real public institutions from the Destatis
    Krankenhausverzeichnis and are not associated with any real patient.

    ## Format

    One JSON object per file:

    | field | meaning |
    | --- | --- |
    | `id` | matches the filename stem |
    | `text` | the note |
    | `expected_outcome` | `sanitized`, or `high_risk_rejected` when `pipeline.sanitize()` must raise |
    | `rare_diseases` | exact German ORDO labels present in `text` (may be empty) |
    | `entities` | ground-truth spans: `entity_type`, `text`, `start`, `end`, with `text[start:end] == entity.text` |

    `backend/tests/unit/test_golden_corpus.py` validates every one of those
    invariants, so an offset typo fails immediately and loudly.

    ## Scope

    This directory holds corpus **data** only. The scored benchmark that consumes it —
    precision/recall/F1, leakage rate, latency, cost — is a separate later plan.
    `backend/tests/privacy_invariants/test_pipeline_corpus.py` uses the corpus for
    pass/fail assertions, not for scored reporting.

- [ ] **Step 4: Create `evaluation/golden_corpus/note_001.json` through `note_011.json`**

`evaluation/golden_corpus/note_001.json`:
```json
{
  "id": "note_001",
  "text": "Patient Lukas Berger, Versichertennummer A123456789, wurde am 12.03.2024 im Universitätsklinikum Heidelberg von Dr. Anna Schmitt aufgenommen.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Lukas Berger", "start": 8, "end": 20},
    {"entity_type": "INSURANCE_NUMBER", "text": "A123456789", "start": 41, "end": 51},
    {"entity_type": "DATE", "text": "12.03.2024", "start": 62, "end": 72},
    {"entity_type": "HOSPITAL", "text": "Universitätsklinikum Heidelberg", "start": 76, "end": 107},
    {"entity_type": "DOCTOR", "text": "Anna Schmitt", "start": 116, "end": 128}
  ]
}
```

`evaluation/golden_corpus/note_002.json`:
```json
{
  "id": "note_002",
  "text": "Die Patientin Marie Hoffmann ist 67 Jahre alt und lebt in Regensburg. Telefonisch erreichbar unter 0941 5551234.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Marie Hoffmann", "start": 14, "end": 28},
    {"entity_type": "AGE", "text": "67 Jahre alt", "start": 33, "end": 45},
    {"entity_type": "LOCATION", "text": "Regensburg", "start": 58, "end": 68},
    {"entity_type": "PHONE", "text": "0941 5551234", "start": 99, "end": 111}
  ]
}
```

`evaluation/golden_corpus/note_003.json`:
```json
{
  "id": "note_003",
  "text": "Aufnahme von Jonas Weber am 05.11.2023 im Klinikum Nürnberg. Behandelnder Arzt ist Prof. Peter Krüger.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Jonas Weber", "start": 13, "end": 24},
    {"entity_type": "DATE", "text": "05.11.2023", "start": 28, "end": 38},
    {"entity_type": "HOSPITAL", "text": "Klinikum Nürnberg", "start": 42, "end": 59},
    {"entity_type": "DOCTOR", "text": "Peter Krüger", "start": 89, "end": 101}
  ]
}
```

`evaluation/golden_corpus/note_004.json`:
```json
{
  "id": "note_004",
  "text": "Sabine Vogel ist eine 52-jährige Patientin aus Kassel. Rückfragen an s.vogel@example.de.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Sabine Vogel", "start": 0, "end": 12},
    {"entity_type": "AGE", "text": "52-jährige", "start": 22, "end": 32},
    {"entity_type": "LOCATION", "text": "Kassel", "start": 47, "end": 53},
    {"entity_type": "EMAIL", "text": "s.vogel@example.de", "start": 69, "end": 87}
  ]
}
```

`evaluation/golden_corpus/note_005.json`:
```json
{
  "id": "note_005",
  "text": "Patientennummer: 4471029. Die Entlassung erfolgte am 28.02.2024 nach Hause.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT_NUMBER", "text": "4471029", "start": 17, "end": 24},
    {"entity_type": "DATE", "text": "28.02.2024", "start": 53, "end": 63}
  ]
}
```

`evaluation/golden_corpus/note_006.json`:
```json
{
  "id": "note_006",
  "text": "Herr Tobias Lang wurde von Dr. Miriam Falk im Krankenhaus Sankt Elisabeth untersucht. Der Bericht geht an praxis.falk@example.org.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Tobias Lang", "start": 5, "end": 16},
    {"entity_type": "DOCTOR", "text": "Miriam Falk", "start": 31, "end": 42},
    {"entity_type": "HOSPITAL", "text": "Krankenhaus Sankt Elisabeth", "start": 46, "end": 73},
    {"entity_type": "EMAIL", "text": "praxis.falk@example.org", "start": 106, "end": 129}
  ]
}
```

`evaluation/golden_corpus/note_007.json`:
```json
{
  "id": "note_007",
  "text": "Lea Sommer stellte sich zur Kontrolle vor. Begleitet wurde Lea Sommer von ihrer Mutter Ingrid Sommer.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Lea Sommer", "start": 0, "end": 10},
    {"entity_type": "PATIENT", "text": "Lea Sommer", "start": 59, "end": 69},
    {"entity_type": "PERSON", "text": "Ingrid Sommer", "start": 87, "end": 100}
  ]
}
```

`evaluation/golden_corpus/note_008.json`:
```json
{
  "id": "note_008",
  "text": "Die Versichertennummer lautet B987654321. Die Aufnahme erfolgte am 19.07.2024 in Bremen.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "INSURANCE_NUMBER", "text": "B987654321", "start": 30, "end": 40},
    {"entity_type": "DATE", "text": "19.07.2024", "start": 67, "end": 77},
    {"entity_type": "LOCATION", "text": "Bremen", "start": 81, "end": 87}
  ]
}
```

`evaluation/golden_corpus/note_009.json`:
```json
{
  "id": "note_009",
  "text": "Telefonische Rücksprache mit der Angehörigen unter +49 30 1234567 am 03.01.2025.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PHONE", "text": "+49 30 1234567", "start": 51, "end": 65},
    {"entity_type": "DATE", "text": "03.01.2025", "start": 69, "end": 79}
  ]
}
```

`evaluation/golden_corpus/note_010.json`:
```json
{
  "id": "note_010",
  "text": "Karl Reinhardt ist ein 81-jähriger Patient. Fallnummer: 8830145. Er wohnt in Dortmund.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Karl Reinhardt", "start": 0, "end": 14},
    {"entity_type": "AGE", "text": "81-jähriger", "start": 23, "end": 34},
    {"entity_type": "PATIENT_NUMBER", "text": "8830145", "start": 56, "end": 63},
    {"entity_type": "LOCATION", "text": "Dortmund", "start": 77, "end": 85}
  ]
}
```

`evaluation/golden_corpus/note_011.json`:
```json
{
  "id": "note_011",
  "text": "Die Überweisung an das MVZ Gesundheitszentrum Ost erfolgte durch Dr. Hannes Bauer.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "HOSPITAL", "text": "MVZ Gesundheitszentrum Ost", "start": 23, "end": 49},
    {"entity_type": "DOCTOR", "text": "Hannes Bauer", "start": 69, "end": 81}
  ]
}
```

- [ ] **Step 5: Create `evaluation/golden_corpus/note_012.json` through `note_022.json`**

`evaluation/golden_corpus/note_012.json` — the coreference-limitation case: the same human is referred to three times under two distinct surfaces, so "Hans Müller" and "Herr Müller" must receive different tokens.
```json
{
  "id": "note_012",
  "text": "Hans Müller wurde am 14.06.2024 eingewiesen. Später berichtete Herr Müller über anhaltende Beschwerden. Hans Müller erhielt daraufhin eine Infusion.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Hans Müller", "start": 0, "end": 11},
    {"entity_type": "DATE", "text": "14.06.2024", "start": 21, "end": 31},
    {"entity_type": "PERSON", "text": "Herr Müller", "start": 63, "end": 74},
    {"entity_type": "PATIENT", "text": "Hans Müller", "start": 104, "end": 115}
  ]
}
```

`evaluation/golden_corpus/note_013.json`:
```json
{
  "id": "note_013",
  "text": "Elena Fischer ist 34 Jahre alt und kommt aus Leipzig. Sie ist erreichbar unter elena.fischer@example.com oder 0341 4455667.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Elena Fischer", "start": 0, "end": 13},
    {"entity_type": "AGE", "text": "34 Jahre alt", "start": 18, "end": 30},
    {"entity_type": "LOCATION", "text": "Leipzig", "start": 45, "end": 52},
    {"entity_type": "EMAIL", "text": "elena.fischer@example.com", "start": 79, "end": 104},
    {"entity_type": "PHONE", "text": "0341 4455667", "start": 110, "end": 122}
  ]
}
```

`evaluation/golden_corpus/note_014.json`:
```json
{
  "id": "note_014",
  "text": "Die Kontrolle erfolgte am 22.09.2024. Versichertennummer C112233445. Patientennummer: 9012347.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "DATE", "text": "22.09.2024", "start": 26, "end": 36},
    {"entity_type": "INSURANCE_NUMBER", "text": "C112233445", "start": 57, "end": 67},
    {"entity_type": "PATIENT_NUMBER", "text": "9012347", "start": 86, "end": 93}
  ]
}
```

`evaluation/golden_corpus/note_015.json`:
```json
{
  "id": "note_015",
  "text": "Prof. Ulrike Brandt vom Universitätsklinikum Freiburg empfiehlt eine Kontrolle in sechs Monaten.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "DOCTOR", "text": "Ulrike Brandt", "start": 6, "end": 19},
    {"entity_type": "HOSPITAL", "text": "Universitätsklinikum Freiburg", "start": 24, "end": 53}
  ]
}
```

`evaluation/golden_corpus/note_016.json`:
```json
{
  "id": "note_016",
  "text": "Der 7-jährige Patient Nico Wagner wurde in der Notaufnahme in Rostock vorgestellt.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "AGE", "text": "7-jährige", "start": 4, "end": 13},
    {"entity_type": "PATIENT", "text": "Nico Wagner", "start": 22, "end": 33},
    {"entity_type": "LOCATION", "text": "Rostock", "start": 62, "end": 69}
  ]
}
```

`evaluation/golden_corpus/note_017.json` — the gazetteer-only hospital case: no `Universitätsklinikum`/`Klinikum`/`Krankenhaus`/`MVZ` suffix, so it can only be recognised through the Krankenhausverzeichnis gazetteer.
```json
{
  "id": "note_017",
  "text": "Die Verlegung in die Charité Universitätsmedizin Berlin erfolgte am 08.04.2024.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "HOSPITAL", "text": "Charité Universitätsmedizin Berlin", "start": 21, "end": 55},
    {"entity_type": "DATE", "text": "08.04.2024", "start": 68, "end": 78}
  ]
}
```

`evaluation/golden_corpus/note_018.json`:
```json
{
  "id": "note_018",
  "text": "Rückfragen bitte an station3@klinikum-example.de oder telefonisch unter 0221 9876543.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "EMAIL", "text": "station3@klinikum-example.de", "start": 20, "end": 48},
    {"entity_type": "PHONE", "text": "0221 9876543", "start": 72, "end": 84}
  ]
}
```

`evaluation/golden_corpus/note_019.json`:
```json
{
  "id": "note_019",
  "text": "Fatima Demir ist eine 29-jährige Patientin. Behandelnder Arzt ist Dr. Stefan Roth.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "PATIENT", "text": "Fatima Demir", "start": 0, "end": 12},
    {"entity_type": "AGE", "text": "29-jährige", "start": 22, "end": 32},
    {"entity_type": "DOCTOR", "text": "Stefan Roth", "start": 70, "end": 81}
  ]
}
```

`evaluation/golden_corpus/note_020.json`:
```json
{
  "id": "note_020",
  "text": "Entlassbrief vom 30.10.2024: Georg Ackermann wurde beschwerdefrei nach Hause entlassen. Die Weiterbehandlung übernimmt Dr. Claudia Neumann in Augsburg.",
  "expected_outcome": "sanitized",
  "rare_diseases": [],
  "entities": [
    {"entity_type": "DATE", "text": "30.10.2024", "start": 17, "end": 27},
    {"entity_type": "PATIENT", "text": "Georg Ackermann", "start": 29, "end": 44},
    {"entity_type": "DOCTOR", "text": "Claudia Neumann", "start": 123, "end": 138},
    {"entity_type": "LOCATION", "text": "Augsburg", "start": 142, "end": 150}
  ]
}
```

`evaluation/golden_corpus/note_021.json` — the escalation **negative** case: a rare disease with only one quasi-identifier category present, so it must sanitize normally.
```json
{
  "id": "note_021",
  "text": "Bei dem 41-jährigen Patienten Martin Seidel besteht eine gesicherte Huntington-Krankheit.",
  "expected_outcome": "sanitized",
  "rare_diseases": ["Huntington-Krankheit"],
  "entities": [
    {"entity_type": "AGE", "text": "41-jährigen", "start": 8, "end": 19},
    {"entity_type": "PATIENT", "text": "Martin Seidel", "start": 30, "end": 43}
  ]
}
```

`evaluation/golden_corpus/note_022.json` — the deliberate quasi-identifier combination case from master spec §8 ("age + city + rare disease + date"). `sanitize()` must raise `HighRiskMessageError`.
```json
{
  "id": "note_022",
  "text": "Der 19-jährige Patient Simon Kraus aus Tübingen wurde am 11.05.2024 mit der Diagnose Zystische Fibrose vorgestellt.",
  "expected_outcome": "high_risk_rejected",
  "rare_diseases": ["Zystische Fibrose"],
  "entities": [
    {"entity_type": "AGE", "text": "19-jährige", "start": 4, "end": 14},
    {"entity_type": "PATIENT", "text": "Simon Kraus", "start": 23, "end": 34},
    {"entity_type": "LOCATION", "text": "Tübingen", "start": 39, "end": 47},
    {"entity_type": "DATE", "text": "11.05.2024", "start": 57, "end": 67}
  ]
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_golden_corpus.py -v`
Expected: PASS (115 passed — 5 parametrized tests × 22 notes, plus 5 standalone tests).

- [ ] **Step 7: Verify the corpus covers every entity type at least twice**

Run:
```bash
cd backend
python -c "
import json, collections, pathlib
counts = collections.Counter()
for path in sorted(pathlib.Path('../evaluation/golden_corpus').glob('*.json')):
    for entity in json.loads(path.read_text(encoding='utf-8'))['entities']:
        counts[entity['entity_type']] += 1
for entity_type, count in sorted(counts.items()):
    print(f'{entity_type:18} {count}')
"
```
Expected:
```
AGE                8
DATE               10
DOCTOR             7
EMAIL              4
HOSPITAL           6
INSURANCE_NUMBER   3
LOCATION           8
PATIENT            16
PATIENT_NUMBER     3
PERSON             2
PHONE              4
```

- [ ] **Step 8: Commit**

```bash
git add evaluation backend/tests/unit/test_golden_corpus.py
git commit -m "feat: add the 22-note synthetic German golden corpus with ground truth"
```

---

## Task 12: Corpus-wide privacy-invariant tests

**Files:**
- Create: `backend/tests/privacy_invariants/conftest.py`
- Test: `backend/tests/privacy_invariants/test_pipeline_corpus.py`

**Interfaces:**
- Consumes: the corpus JSON files (Task 11); `Pipeline` and its four exceptions (Task 10); `DetectorStack`/`RegexDetector`/`PresidioDetector`/`CustomRecognizers` (Tasks 2/3/5/6); `RiskScorer` (Task 7); `Pseudonymizer` (Task 8); `OutputGuard` (Task 9); `TokenVault`/`FileSecretKeyProvider`; the repositories and `tenant_scoped_session` from the data-model plan.
- Produces: pytest fixtures `golden_corpus`, `corpus_pipeline`, `corpus_vault`, `corpus_key_provider`, `corpus_scope` and the helper `new_scope()`, shared across the `privacy_invariants` suite. No production code.

**Resolved ambiguity implemented here:** #11 (`KNOWN_RECALL_GAPS` and the triage procedure).

**Prerequisite:** Postgres running with migrations applied; `de_core_news_lg` installed. The reference datasets are **not** needed — the fixtures inject rare-disease and hospital sets derived from the corpus itself, so the suite stays fast and hermetic.

- [ ] **Step 1: Create the shared fixtures**

`backend/tests/privacy_invariants/conftest.py`:

```python
import json
import uuid
from pathlib import Path

import pytest

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.detectors.base import normalize
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import OutputGuard
from app.privacy_gateway.pipeline import Pipeline
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.scorer import RiskScorer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

CORPUS_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "golden_corpus"


def load_golden_corpus() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(CORPUS_DIR.glob("*.json"))
    ]


@pytest.fixture(scope="session")
def golden_corpus() -> list[dict]:
    return load_golden_corpus()


@pytest.fixture(scope="session")
def corpus_key_provider(tmp_path_factory):
    master_key_path = tmp_path_factory.mktemp("keys") / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


@pytest.fixture(scope="session")
def corpus_vault(corpus_key_provider) -> TokenVault:
    return TokenVault(corpus_key_provider)


@pytest.fixture(scope="session")
def corpus_pipeline(golden_corpus, corpus_vault) -> Pipeline:
    """A pipeline whose reference data is derived from the corpus itself.

    The real ORDO OWL and Krankenhausverzeichnis are 50 MB of build-time input and
    minutes of parse time (design spec §6); injecting the corpus's own rare-disease
    names and hospital names keeps this suite hermetic and fast while exercising
    exactly the same code paths. `tests/unit/test_reference_data.py` covers the real
    files.
    """
    rare_diseases = frozenset(
        normalize(name) for note in golden_corpus for name in note["rare_diseases"]
    )
    hospitals = frozenset(
        normalize(entity["text"])
        for note in golden_corpus
        for entity in note["entities"]
        if entity["entity_type"] == "HOSPITAL"
    )
    stack = DetectorStack(RegexDetector(), PresidioDetector(), CustomRecognizers(hospitals))
    return Pipeline(
        detector_stack=stack,
        risk_scorer=RiskScorer(rare_diseases),
        pseudonymizer=Pseudonymizer(corpus_vault),
        output_guard=OutputGuard(stack, corpus_vault),
    )


def new_scope(key_provider) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a fresh tenant + user + conversation and return (tenant_id, conversation_id)."""
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"sub-{uuid.uuid4()}",
            email="doc@example.com",
            role="doctor",
        )
        conversation_id = ConversationRepository(session).create(tenant_id, user.id).id
    return tenant_id, conversation_id


@pytest.fixture
def corpus_scope(corpus_key_provider) -> tuple[uuid.UUID, uuid.UUID]:
    return new_scope(corpus_key_provider)
```

- [ ] **Step 2: Write the failing corpus test**

`backend/tests/privacy_invariants/test_pipeline_corpus.py`:

```python
import re

import pytest

from app.privacy_gateway.pipeline import (
    HighRiskMessageError,
    LeakageDetectedError,
    UnresolvedTokenError,
)
from tests.privacy_invariants.conftest import load_golden_corpus, new_scope

TOKEN_PATTERN = re.compile(r"[A-Z][A-Z_]*_[0-9A-F]{10}")

CORPUS = load_golden_corpus()
SANITIZABLE = [note for note in CORPUS if note["expected_outcome"] == "sanitized"]
REJECTED = [note for note in CORPUS if note["expected_outcome"] == "high_risk_rejected"]

# Cases where de_core_news_lg genuinely cannot tag an annotated span. Populating this
# list is the ONLY sanctioned way to make a failure below go away other than fixing
# the detector — never edit the corpus to dodge a recall gap. Each entry is
# (note_id, entity_text) and must carry a comment saying why.
KNOWN_RECALL_GAPS: set[tuple[str, str]] = set()


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_no_raw_pii_string_survives_sanitize(note, corpus_pipeline, corpus_scope):
    """Design spec §7 / master spec §9: no raw golden-corpus PII string ever appears
    in sanitize()'s output, for every corpus example."""
    tenant_id, conversation_id = corpus_scope
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    leaked = [
        entity["text"]
        for entity in note["entities"]
        if entity["text"] in sanitized
        and (note["id"], entity["text"]) not in KNOWN_RECALL_GAPS
    ]
    assert not leaked, (
        f"{note['id']}: raw PII survived sanitize(): {leaked}\n"
        f"sanitized output: {sanitized}"
    )


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_sanitize_emits_only_well_formed_tokens(note, corpus_pipeline, corpus_scope):
    tenant_id, conversation_id = corpus_scope
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
    for token in TOKEN_PATTERN.findall(sanitized):
        assert token.rsplit("_", 1)[0].isupper()


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_round_trip_restores_the_original_note(note, corpus_pipeline, corpus_scope):
    tenant_id, conversation_id = corpus_scope
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
    restored = corpus_pipeline.deanonymize(tenant_id, conversation_id, sanitized)
    assert restored == note["text"]


@pytest.mark.parametrize("note", REJECTED, ids=lambda note: note["id"])
def test_the_combination_case_is_rejected_not_partially_sanitized(
    note, corpus_pipeline, corpus_scope
):
    tenant_id, conversation_id = corpus_scope
    with pytest.raises(HighRiskMessageError) as excinfo:
        corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
    for entity in note["entities"]:
        assert entity["text"] not in str(excinfo.value)


def test_the_coreference_limitation_produces_distinct_tokens(corpus_pipeline, corpus_scope):
    """The MVP limitation, made visible: "Hans Müller" and "Herr Müller" are different
    exact strings and therefore different tokens, while the two "Hans Müller"
    mentions share one."""
    tenant_id, conversation_id = corpus_scope
    note = next(n for n in CORPUS if n["id"] == "note_012")

    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
    person_tokens = [
        token for token in TOKEN_PATTERN.findall(sanitized)
        if token.startswith(("PATIENT_", "PERSON_"))
    ]

    assert len(set(person_tokens)) >= 2, (
        "three references to one human collapsed into a single token; the "
        f"exact-string-match limitation is no longer being exercised: {sanitized}"
    )


def test_every_corpus_note_sanitizes_under_a_single_conversation(
    corpus_pipeline, corpus_scope
):
    """One conversation, every note: token minting must not collide or fail on the
    (tenant_id, conversation_id, token) unique constraint across many messages."""
    tenant_id, conversation_id = corpus_scope
    for note in SANITIZABLE:
        sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])
        assert sanitized


def test_a_corpus_token_never_resolves_for_another_tenant(
    corpus_pipeline, corpus_scope, corpus_key_provider
):
    tenant_id, conversation_id = corpus_scope
    other_tenant_id, _ = new_scope(corpus_key_provider)
    note = next(n for n in SANITIZABLE if n["id"] == "note_001")

    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    with pytest.raises(UnresolvedTokenError):
        corpus_pipeline.deanonymize(other_tenant_id, conversation_id, sanitized)


def test_a_corpus_token_never_resolves_for_another_conversation(
    corpus_pipeline, corpus_scope, corpus_key_provider
):
    tenant_id, conversation_id = corpus_scope
    _, other_conversation_id = new_scope(corpus_key_provider)
    note = next(n for n in SANITIZABLE if n["id"] == "note_001")

    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    with pytest.raises(UnresolvedTokenError):
        corpus_pipeline.deanonymize(tenant_id, other_conversation_id, sanitized)


@pytest.mark.parametrize(
    "leaked_output",
    [
        "Die Versichertennummer lautet A123456789.",
        "Lukas Berger wurde am 12.03.2024 entlassen.",
        "Bitte melden Sie sich unter s.vogel@example.de.",
        "Rückruf unter 0941 5551234 vereinbart.",
    ],
)
def test_raw_pii_in_llm_output_is_always_caught(
    leaked_output, corpus_pipeline, corpus_scope
):
    tenant_id, conversation_id = corpus_scope
    with pytest.raises(LeakageDetectedError):
        corpus_pipeline.deanonymize(tenant_id, conversation_id, leaked_output)


def test_a_token_mixed_with_leaked_pii_still_fails_closed(corpus_pipeline, corpus_scope):
    tenant_id, conversation_id = corpus_scope
    note = next(n for n in SANITIZABLE if n["id"] == "note_001")
    sanitized = corpus_pipeline.sanitize(tenant_id, conversation_id, note["text"])

    with pytest.raises(LeakageDetectedError):
        corpus_pipeline.deanonymize(
            tenant_id, conversation_id, sanitized + " Kontakt: A987654321."
        )
```

- [ ] **Step 3: Run the test to see which notes fail**

Run: `cd backend && pytest tests/privacy_invariants/test_pipeline_corpus.py -v`
Expected: the module imports cleanly (`backend/tests/privacy_invariants/__init__.py` already exists in this repo) and the tests fail only where detection is genuinely incomplete. **Record which `(note_id, entity_text)` pairs fail before proceeding.**

- [ ] **Step 4: Triage every failure in `test_no_raw_pii_string_survives_sanitize`**

For each failing `(note_id, entity_text)` pair, apply exactly one of these three remediations — in this priority order — and record which one in the commit message:

1. **A detector bug.** If the entity is one layer 1 or layer 3 owns (`INSURANCE_NUMBER`, `PATIENT_NUMBER`, `PHONE`, `EMAIL`, `DATE`, `AGE`, `HOSPITAL`, or a `DOCTOR`/`PATIENT` promotion) and the miss is a pattern or heuristic gap, fix the detector in its own task's module and re-run that task's unit tests plus this one.
2. **A gazetteer/normalization gap.** If a `HOSPITAL` is missed because the `corpus_pipeline` fixture's injected gazetteer doesn't contain it, that is a fixture or `normalize()` bug — the fixture derives the gazetteer from the corpus's own `HOSPITAL` annotations, so a miss means the two sides disagree. Fix `normalize()` or the fixture, never the note.
3. **A genuine `de_core_news_lg` recall gap.** Only for `PERSON`/`LOCATION`/`ORGANIZATION`-derived entities, and only after confirming it directly:

   ```bash
   cd backend
   python -c "
   import spacy
   nlp = spacy.load('de_core_news_lg')
   doc = nlp('<the note text>')
   print([(ent.text, ent.label_) for ent in doc.ents])
   "
   ```

   If the model genuinely does not produce the span, add the pair to `KNOWN_RECALL_GAPS` with a comment naming the model and what it produced instead, e.g.:

   ```python
   KNOWN_RECALL_GAPS: set[tuple[str, str]] = {
       # de_core_news_lg tags only "Charité" as ORG here, not the full facility name,
       # so the gazetteer lookup for the whole string misses. Measured, not hidden.
       ("note_017", "Charité Universitätsmedizin Berlin"),
   }
   ```

**Never edit a corpus note to make a failure disappear.** The corpus is the measurement; adjusting it to fit the detector destroys the measurement.

- [ ] **Step 5: Run the corpus tests to verify they pass**

Run: `cd backend && pytest tests/privacy_invariants/test_pipeline_corpus.py -v`
Expected: PASS. With no recall gaps that is 21 + 21 + 21 + 1 + 1 + 1 + 1 + 1 + 4 + 1 = 73 passed.

- [ ] **Step 6: Run the whole suite and the linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT (in particular "Privacy gateway must not be network-capable"); every test passes.

- [ ] **Step 7: Document the corpus regression suite in `README.md`**

In `README.md`, under `## Backend tests`, append:

    ### Golden-corpus regression

    `backend/tests/privacy_invariants/test_pipeline_corpus.py` runs the whole
    `evaluation/golden_corpus/` through `sanitize()` and asserts that no annotated
    raw PII string survives, that every note round-trips through `deanonymize()`,
    and that cross-tenant / cross-conversation token resolution always fails. It
    runs in CI on every push (design doc §9).

    If a note starts failing, fix the detector — do not edit the note. The only
    sanctioned exception is a genuine `de_core_news_lg` recall gap, which goes in
    that file's `KNOWN_RECALL_GAPS` set with a comment recording what the model
    produced instead.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/privacy_invariants/conftest.py backend/tests/privacy_invariants/test_pipeline_corpus.py README.md
git commit -m "test: run the whole golden corpus through the pipeline as a privacy invariant"
```

---

## Post-Plan State

After Task 12, `backend/app/privacy_gateway/` contains:

```
detectors/
  base.py               # Span, Detector/SpanRefiner protocols, normalize()
  regex_detector.py     # layer 1
  presidio_detector.py  # layer 2
  custom_recognizers.py # layer 3
  stack.py              # fixed-precedence composition
risk_scoring/
  scorer.py             # deterministic classification + fail-closed escalation
  reference_data.py     # ORDO + Krankenhausverzeichnis loaders
  data/                 # gitignored, build-time fetched
pseudonymization/
  pseudonymizer.py
output_guard/
  guard.py
pipeline.py             # sanitize() / deanonymize()
token_vault/            # unchanged, from the data-model plan
```

plus `backend/scripts/fetch_reference_data.py` and `evaluation/golden_corpus/`.

**Deliberately not built here** (design spec §1): the scored benchmark report, the
LLM Gateway, auth, the chat API/UI, procedure-mention risk escalation, and
coreference resolution.

**Known follow-ups this plan surfaces:**
- Cross-message token determinism within a conversation needs a deterministic
  (HMAC) index column on `token_mappings`; today determinism is per-message only
  (Resolved Design Ambiguity #7).
- `audit_events` rows for leakage and rejection events — the pipeline raises typed
  exceptions the caller is expected to log, because `audit_events` repository access
  is out of scope per the data-model plan.
- Any entry that lands in `KNOWN_RECALL_GAPS` is a measured detection gap, not a
  closed issue.
- `DATE_TIME` is currently unreachable from layer 2 because `de_core_news_lg` has no
  `DATE` NER label (Resolved Design Ambiguity #8); a future German date-NER model or
  a Presidio `DateRecognizer` would activate that path.

## Spec Coverage Map

| Design spec section | Task |
| --- | --- |
| §2 layer 1 regex (all six entity types, exact German patterns) | 2 |
| §2 layer 2 Presidio + `de_core_news_lg` | 3 (deps/model install in 1) |
| §2 layer 3 custom recognizers (hospital gazetteer + suffix, doctor/patient heuristic) | 5 |
| §2 fixed precedence / "refine, don't rescan" | 6 |
| §3 direct / quasi / medical classification | 7 |
| §3 ≥2-quasi + rare-disease HIGH escalation, fail-closed | 7, exercised end-to-end in 10 and 12 |
| §3 0.4 confidence threshold, fail-closed | 7 |
| §4 pseudonymization, descending offset order | 8 |
| §4 `sanitize()` public entry point | 10 |
| §5 output guard steps 1–4 | 9, `deanonymize()` in 10 |
| §6 fetch script, Dockerfile build-time, rdflib/openpyxl parsers, startup load, CI caching, README | 1 and 4 (startup warm-up in 10) |
| §7 golden corpus (20–25 notes, coverage, combination case, coreference case) | 11 |
| §7 tests: detector precedence, risk escalation/threshold, corpus-wide no-raw-PII, output-guard cross-tenant + leakage | 6, 7, 12 |
| §8 package structure | all (plus the one documented addition, `detectors/stack.py`, Resolved Design Ambiguity #9) |
