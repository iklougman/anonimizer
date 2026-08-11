# Privacy-First Medical LLM Gateway — MVP Design

**Status:** Approved for implementation planning
**Date:** 2026-08-11
**Related:** `docs/adr/0001`–`0022`, `docs/threat-model.md`

## 1. Purpose & Non-Goals

This is a research/evaluation platform for German medical professionals that
pseudonymizes sensitive input before it reaches an LLM, and deterministically
resolves tokens back on the way out. It exists to let us *measure* different
anonymization strategies against privacy and utility, not to be a finished
clinical product.

**Non-goal:** this system does not by itself constitute GDPR compliance.
Technical controls (pseudonymization, tenant isolation, encryption) are
necessary but not sufficient; legal/organizational compliance (DPIAs, BAAs/AVVs,
staff training, data processing agreements with LLM providers) is out of scope
for this codebase and must never be implied by its existence. This distinction
is stated explicitly here because it is the single easiest thing for this
project to get wrong by omission.

## 2. Repository Structure

```
chatgpt-proxy/
├── docker-compose.yml              # postgres, keycloak, ollama, backend, frontend
├── docker-compose.override.yml.example
├── .env.example
├── docs/
│   ├── adr/                        # 0001-0022
│   ├── threat-model.md
│   └── superpowers/specs/          # this file, future specs
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py                # pydantic-settings, fail-closed defaults
│   │   ├── auth/                    # Keycloak/OIDC, tenant resolution, JWT validation
│   │   ├── api/                     # HTTP routes (chat, conversations, admin/debug)
│   │   ├── privacy_gateway/         # security-critical core, structurally isolated
│   │   │   ├── detectors/           # regex, presidio, german_ner, custom (hospital/doctor)
│   │   │   ├── risk_scoring/
│   │   │   ├── pseudonymization/    # token generation; strategies/ scaffolded for A-E
│   │   │   ├── token_vault/         # TokenVault abstraction + Postgres impl
│   │   │   ├── output_guard/        # leakage detection, token authorization
│   │   │   └── pipeline.py
│   │   ├── guardrails/              # NeMo integration point, stub only in MVP (Phase 2)
│   │   ├── llm_gateway/             # LLMProvider abstraction; ollama + openai adapters
│   │   ├── models/                  # SQLAlchemy models, tenant-scoped
│   │   ├── db/                      # alembic migrations, tenant-guarded session mgmt
│   │   └── observability/           # structured logging, redaction-safe event emitter
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── privacy_invariants/      # automated tests for never-cross-boundary rules
├── frontend/                        # React/Next.js chat UI + privacy debugger panel
└── evaluation/                      # golden corpus + benchmark harness (pytest-based)
```

**Structural boundary enforcement:** `privacy_gateway/` never imports from
`api/` or `llm_gateway/` — only the reverse is allowed, and `privacy_gateway/`
never imports anything network-capable. This is enforced with an import-linter
rule in CI, not just a convention, per the requirement to make security
boundaries explicit (rule #3) and isolated (rule #2).

## 3. System Architecture

```
Doctor's browser
  |
  v
Next.js Chat UI  <---(OIDC redirect)---  Keycloak (realm-per-tenant)
  |  (JWT bearer token)
  v
FastAPI API layer
  |  - validates JWT, extracts tenant_id + user_id, attaches to request context
  |  - fail-closed if Keycloak unreachable: no session issued, no fallback auth
  v
Privacy Gateway (tenant_id threaded through every call, no exceptions)
  |
  +-- Detection: Regex(DE) -> Presidio(DE spaCy) -> custom (hospital/doctor)
  +-- Risk Scoring (deterministic, entity-class + combination rules)
  +-- Pseudonymization (opaque tokens, scoped to tenant+conversation)
  +-- Token Vault (Postgres, envelope-encrypted, tenant_id in every row+query, RLS)
  |
  v
LLM Gateway (LLMProvider interface)
  |
  +-- OllamaProvider   (local, no egress)
  +-- OpenAIProvider   (external egress — only sanitized text ever crosses this line)
  |
  v
Privacy Gateway (return path)
  |
  +-- Output leakage scan (raw PII reappeared? untrusted token appeared?)
  +-- Token authorization check (token issued for this tenant+conversation?)
  +-- Deanonymization (only authorized, resolvable tokens are ever substituted)
  |
  v
Doctor's browser
```

## 4. Data Model & Tenant/Token Scoping

**Core principle:** `messages` store only pseudonymized text — nothing
sensitive is persisted outside the vault. Human-readable text is
reconstructed on demand by resolving tokens through the output guard. This
gives retention "for free": deleting a tenant's DEK renders their historical
messages permanently unreadable (crypto-shredding) without touching message
rows.

```
tenants(id, name, keycloak_realm, retention_days, created_at)
users(id, tenant_id, keycloak_subject, email, role, created_at)
conversations(id, tenant_id, user_id, created_at, expires_at)
messages(id, conversation_id, tenant_id, role, sanitized_content, created_at)
token_mappings(id, tenant_id, conversation_id, token, entity_type,
               encrypted_value, dek_id, created_at, expires_at, deleted_at)
tenant_keys(tenant_id, wrapped_dek, key_version, created_at)
audit_events(id, tenant_id, conversation_id, event_type, entity_type,
             token, actor, timestamp)                          # no raw values, ever
llm_requests(id, tenant_id, conversation_id, provider, model,
             sanitized_prompt, sanitized_response, tokens_in, tokens_out,
             cost_usd, latency_ms, created_at)                  # safe: sanitized only
```

**Tenant isolation — defense in depth:**
1. Every repository method takes `tenant_id` as a mandatory first argument;
   there is no code path that can omit it.
2. Postgres Row-Level Security policies key every tenant-scoped table on
   `tenant_id`, set from the authenticated session. A missing `WHERE` clause
   in application code fails closed at the database layer instead of leaking
   cross-tenant.
3. CI includes a schema-migration test asserting every tenant-scoped table
   has an RLS policy before merge.

**Encryption:** envelope encryption. Each tenant has a DEK wrapped by a
master key sourced from a mounted secret (Docker secret / file), never
committed or logged. `token_mappings.encrypted_value` is AES-GCM under the
tenant's DEK. The `KeyProvider` interface is the seam for a real KMS/Vault
later.

**Token format:** `[TYPE_XXXXX]`, e.g. `PATIENT_7F82A`. The suffix is
cryptographically random (`secrets.token_hex`), never sequential — the LLM
cannot infer entity count or ordering across a conversation. Tokens are
unique within `(tenant_id, conversation_id)`.

## 5. Detection, Pseudonymization & Output Protection Pipeline

**Detection layers, in fixed precedence order (regex > Presidio custom
recognizers > generic NER) so span-overlap resolution is deterministic, not
confidence-score-based:**

1. **Regex** — German-specific fixed-format direct identifiers:
   Versichertennummer, Patientennummer, phone, email, exact dates (DD.MM.YYYY).
2. **Presidio + German spaCy model** (`de_core_news_lg` initially) — PERSON,
   LOCATION, generic DATE_TIME. This *is* the German-NER layer; a separate
   medical-NER model is Phase 3, not duplicated here.
3. **Custom recognizers** — hospital/institution (gazetteer + suffix pattern:
   `Universitätsklinikum`, `Klinikum`, `Krankenhaus`, `MVZ`, cross-checked
   against spaCy `ORG`), and a doctor/patient role heuristic (title prefixes
   `Dr.`/`Prof.` or proximity to "behandelnder Arzt" → `DOCTOR_`; the
   first/most-referenced unlabeled person → `PATIENT_`; others → `PERSON_`).

**Risk scoring (deterministic, not ML, for MVP):**
- Direct identifiers: always tokenized, no threshold.
- Quasi-identifiers (city, hospital, exact date, age): tokenized by default;
  risk escalates to HIGH when ≥2 quasi-identifier categories co-occur with a
  rare-disease/procedure mention, triggering a policy check before sending.
- Medical content (diagnoses, treatments): left intact for utility, unless on
  a rare-disease list combined with escalated risk.
- Any entity below a confidence threshold blocks the whole message
  (fail-closed) rather than partially sanitizing.

**Known MVP limitation (explicit, not silently assumed away):** token
determinism is exact-string-match within a conversation. "Hans Müller" and a
later "Herr Müller" do not automatically collapse to the same token — v1 has
no coreference resolution. This is a measured limitation for the benchmark,
not a bug to be discovered later.

**Output protection (return path):**
1. Re-run the detector stack on the raw LLM output. Any raw-looking PII that
   isn't a legitimate token is a leakage event → fail-closed.
2. Extract `[TYPE_XXXXX]`-shaped substrings.
3. For each, verify it was issued for *this* `tenant_id` + `conversation_id`
   before resolving. A token from another conversation, or a fabricated one
   (e.g. via prompt injection asking to "reveal the mapping for
   PATIENT_7F82A"), fails authorization and is never resolved — this is the
   concrete mechanism that defeats that attack class.
4. Only authorized, resolvable tokens are substituted back; anything else is
   left opaque or the response is rejected, per policy.

## 6. LLM Provider Abstraction

`LLMProvider` interface with two MVP adapters: `OllamaProvider` (local, no
egress) and `OpenAIProvider` (external egress; only sanitized text ever
crosses this boundary). OpenAI is included in the MVP — not deferred to
Phase 4 as originally scoped — specifically so the "external LLM never sees
the identity mapping" invariant is a real, tested code path from the first
increment, not a claim exercised only later (see ADR-0022).

## 7. Authentication & Multi-Tenancy

Keycloak, one realm per tenant (tenant = clinic/hospital organization).
Doctors are users belonging to exactly one tenant. JWT bearer tokens carry
the tenant claim, which is re-validated against the RLS session variable on
every request — never trusted transitively from the token alone. Keycloak
being unreachable fails closed: no session, no fallback auth path (ADR-0021).

## 8. First Privacy/Utility Benchmark

A golden corpus of ~20–30 hand-crafted synthetic German clinical notes,
covering the entity taxonomy including deliberate quasi-identifier
combinations (age + city + rare disease + date), each with ground-truth
entity-span annotations. Lives in `evaluation/`, runs via pytest, produces a
markdown/JSON report.

Measured per axis:
- **Privacy:** entity-level precision/recall/F1 vs. golden annotations;
  leakage rate; token-resolution failure rate.
- **Utility:** embedding cosine similarity between the pseudonymized
  round-trip answer and a raw-text baseline — baseline only ever runs against
  **Ollama**, never OpenAI, since it requires sending unsanitized text.
- **Performance:** per-stage latency (detection / LLM call / deanonymization).
- **Cost:** tokens + $ per external call, read from `llm_requests`.
- **Re-identification (lightweight):** confirms the risk-scoring escalation
  rule fires on the combination test cases — a correctness check on the risk
  engine, not adversarial red-teaming (that's Phase 5).

## 9. Testing Strategy

- Unit tests per detector.
- Integration tests running the full pipeline against the golden corpus.
- `privacy_invariants/` suite: no vault query without `tenant_id`; no raw
  golden-corpus PII string ever appears in a `sanitized_prompt` (regression,
  run against every corpus example in CI); cross-tenant/cross-conversation
  token resolution always fails (explicit negative test for the injection
  attack in §5).
- One E2E test: Keycloak login (test realm) → known input → captured
  OpenAI/Ollama request body contains zero raw identifiers → deanonymized
  response is correct.

## 10. MVP Scope

**In:** FastAPI + Next.js; Keycloak/OIDC multi-tenant auth + RLS; Postgres
persistence (conversations, sanitized messages, token vault, audit events,
llm_requests); regex + Presidio(DE) + custom hospital/doctor detection;
deterministic risk scoring; TokenVault with envelope encryption,
tenant+conversation scoped; output leakage scan + authorization-checked
deanonymization; `LLMProvider` with Ollama + OpenAI; structured
redaction-safe logging/audit events; dev-only privacy debugger (disabled by
default); golden-corpus benchmark; all 22 ADRs; threat model doc; Docker
Compose for the full local stack.

**Out (explicitly deferred):** NeMo Guardrails / prompt-injection rails
(Phase 2); medical-NER model and the A–E strategy comparison benchmark
(Phase 3 — `pseudonymization/strategies/` interface is scaffolded now so this
doesn't require rearchitecting); Azure/other providers beyond Ollama+OpenAI
(Phase 4); adversarial re-identification red-teaming at scale (Phase 5);
cross-turn coreference/fuzzy name matching; a dedicated secrets vault
(HashiCorp Vault/KMS — abstraction exists, concrete impl is
Postgres+envelope-encryption only); any legal/compliance sign-off.

## 11. Assumptions Requiring Validation

These are stated explicitly so the benchmark (§8) is designed to test them,
rather than the design silently depending on them:

1. Presidio + `de_core_news_lg` achieves acceptable recall on German medical
   sentence structure (not validated yet — the reason this project measures
   precision/recall at all).
2. The hospital/doctor gazetteer-and-heuristic approach generalizes beyond
   the hand-crafted golden corpus; false negatives here are a silent utility
   leak (institution name reaches the LLM unredacted).
3. Exact-string-match token determinism (§5 limitation) doesn't cause
   confusing pseudonym proliferation in longer conversations — worth an
   explicit test case in the golden corpus with a name referenced 3+ ways.
4. RLS policies plus mandatory-argument repositories are sufficient
   defense-in-depth for tenant isolation without a per-tenant database/schema
   split; revisit if a real multi-hospital pilot is ever contemplated.
