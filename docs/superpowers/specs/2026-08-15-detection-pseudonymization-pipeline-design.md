# Detection & Pseudonymization Pipeline — Design

**Status:** Approved

**Depends on:** `docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md`
§5 (detection/pseudonymization pipeline), §8 (golden corpus), ADR-0001
(privacy_gateway isolation), ADR-0002 (pseudonymization over anonymization),
ADR-0006 (Presidio for PII detection), ADR-0007 (hybrid entity detection),
ADR-0009 (token scope). Consumes `TokenVault` and the repository layer from
`docs/superpowers/specs/2026-08-13-data-model-tenant-rls-design.md`.

**Follows:** the Data Model & Tenant RLS plan (merged to `main`). This is
the second of four layers being built full-spec, one at a time (detection
pipeline → LLM gateway → auth → chat API/UI).

## 1. Purpose & Scope

Turn raw clinical text into pseudonymized text safe to send to an LLM, and
reverse that transformation on the way back — the core mechanism the whole
project exists to prove out. Detects sensitive spans in German clinical
text via a fixed-precedence three-layer stack, scores re-identification
risk deterministically, replaces spans the scorer flags with opaque tokens
via the existing `TokenVault`, and on the return path re-runs detection on
raw LLM output (leakage check) before resolving only authorized tokens back
to their original values.

**In scope:**
- `privacy_gateway/detectors/`: regex, Presidio+spaCy, custom recognizers
  (hospital gazetteer, doctor/patient heuristic), in fixed precedence order.
- `privacy_gateway/risk_scoring/`: deterministic risk classification,
  including the rare-disease/quasi-identifier escalation rule.
- `privacy_gateway/pseudonymization/`: wires risk-scoring output to
  `TokenVault.create_mapping`.
- `privacy_gateway/output_guard/`: the reverse path — leakage scan +
  authorization-checked `TokenVault.resolve_tokens`.
- `privacy_gateway/pipeline.py`: orchestrates the above into
  `sanitize()`/`deanonymize()`.
- Real external reference data: German ORDO (rare-disease names) and the
  Destatis Krankenhausverzeichnis (hospital names), fetched once at build
  time, parsed locally at startup — no network calls from `privacy_gateway/`
  itself (ADR-0001).
- A 20-25 example golden corpus of synthetic German clinical notes with
  ground-truth entity annotations, used by this plan's own tests.

**Out of scope (deferred):**
- The scored benchmark report (precision/recall/F1, cost, latency
  aggregation) that *consumes* the golden corpus — §8's full benchmark
  tooling is a separate later plan. This plan's tests use the corpus for
  pass/fail assertions, not scored reporting.
- LLM Gateway (§6) — the pipeline produces sanitized text and consumes raw
  LLM output as inputs/outputs of `sanitize()`/`deanonymize()`; nothing in
  this plan calls an actual LLM.
- Auth (§7) — `sanitize()`/`deanonymize()` take `tenant_id`/`conversation_id`
  as explicit arguments, same pattern as `TokenVault`.
- Chat API/UI — nothing in this plan is reachable over HTTP yet.
- Procedure-mention risk escalation — §5 says "rare-disease/procedure
  mention" but no procedure ontology was sourced; this plan implements the
  rare-disease half only. A procedure list is a future extension, not a
  silently-dropped requirement.
- Coreference resolution — token determinism is exact-string-match within
  a conversation (§5's stated MVP limitation): "Hans Müller" and a later
  "Herr Müller" get different tokens. Explicit, not a bug.

## 2. Detection Layers (fixed precedence, not confidence-based)

Each layer only processes text not already claimed by a higher-precedence
layer. A `Span` is `(start, end, entity_type, confidence, source_layer)`.

**1. Regex** (German-specific direct identifiers, exact matches, confidence
always 1.0):
- `INSURANCE_NUMBER` — Versichertennummer format (`[A-Z]\d{9}`).
- `PATIENT_NUMBER` — configurable numeric ID pattern.
- `PHONE` — German phone number formats.
- `EMAIL` — standard email regex.
- `DATE` — `DD.MM.YYYY`.
- `AGE` — `\d{1,3}\s*(?:-jährig|jährige[rn]?|Jahre alt)`. Needed as a
  quasi-identifier category (§8's own example: "age + city + rare disease +
  date") but never given an explicit detector in §5's text — added here to
  close that gap.

**2. Presidio + spaCy** (`de_core_news_lg`, this *is* the German-NER layer
per §5 — no separate medical-NER model): generic `PERSON`, `LOCATION`,
`DATE_TIME`. Presidio's own per-entity confidence score is preserved.

**3. Custom recognizers** — refine, don't rescan. Operate only on spans
already tagged `PERSON`/`ORG` by layer 2:
- `HOSPITAL`: an `ORG` span promoted if it matches the
  Krankenhausverzeichnis-backed gazetteer OR a suffix pattern
  (`Universitätsklinikum`, `Klinikum`, `Krankenhaus`, `MVZ`).
- `DOCTOR`/`PATIENT`/`PERSON`: a `PERSON` span promoted to `DOCTOR` if
  prefixed by `Dr.`/`Prof.` or near "behandelnder Arzt"; the
  first/most-referenced remaining unlabeled `PERSON` span in the message
  promoted to `PATIENT`; everything else stays `PERSON`.

## 3. Risk Scoring (deterministic, not ML)

Every span gets one of three classifications:

- **Direct identifier** (`INSURANCE_NUMBER`, `PATIENT_NUMBER`, `PHONE`,
  `EMAIL`) — always tokenized, no threshold, no escalation logic.
- **Quasi-identifier** (`LOCATION`, `HOSPITAL`, `DATE`, `AGE` — matching
  §5's own list verbatim: "city, hospital, exact date, age") — tokenized
  by default. If **two or more** distinct
  quasi-identifier categories co-occur in the same message **and** a
  rare-disease mention is present (checked against the ORDO-derived name
  list — substring/lemma match against the message's medical-content
  spans), risk escalates to **HIGH**.
- **Medical content** (everything not otherwise classified — diagnoses,
  treatments, free text) — left intact for utility, *unless* it's the
  rare-disease mention that triggered a HIGH escalation.

**HIGH escalation behavior (this plan's concrete interpretation of §5's
"triggering a policy check before sending"):** the whole message is
rejected — `sanitize()` raises rather than returning partially-sanitized
text. No human-review or override workflow exists yet; this is the same
fail-closed shape as the confidence-threshold rule below, applied to the
combination case.

**Confidence threshold:** any span with `confidence < 0.4` (Presidio's own
common default) blocks the whole message the same way — fail-closed, not
partial.

## 4. Pseudonymization

For every span the risk scorer marks tokenize-eligible, `pseudonymization/`
calls the existing `TokenVault.create_mapping(tenant_id, conversation_id,
entity_type, original_value)` and replaces the span in the text with the
returned token. Spans are processed in descending `start` order so earlier
replacements don't shift the offsets of not-yet-processed spans.

`pipeline.sanitize(tenant_id, conversation_id, text) -> str` is the public
entry point: runs all three detector layers, risk-scores every span,
raises on HIGH-risk or low-confidence, else returns the fully pseudonymized
string ready to send to an LLM.

## 5. Output Guard (return path)

`pipeline.deanonymize(tenant_id, conversation_id, llm_output) -> str`:

1. Re-run the full detector stack on `llm_output`. Any detected span that
   *isn't* inside a `[TYPE_XXXXX]`-shaped substring is raw-looking PII the
   LLM produced or leaked — a leakage event. Fail-closed: raise, do not
   return partial output. (No `audit_events` row is written by this plan —
   `audit_events` repository access is out of scope per the data-model
   plan; this plan raises a typed exception the caller can log.)
2. Extract all `[A-Z_]+_[0-9A-F]{10}`-shaped substrings (the token format
   from ADR-0009: entity type + `secrets.token_hex(5).upper()`).
3. Call `TokenVault.resolve_tokens(tenant_id, conversation_id, tokens)` —
   already enforces the tenant+conversation authorization scope from the
   last plan. A token for another tenant/conversation, or a fabricated one,
   simply doesn't resolve.
4. Substitute back only tokens that resolved; any unresolved
   `[TYPE_XXXXX]`-shaped substring left in the output after step 3 is also
   a fail-closed condition (a token the LLM couldn't have legitimately
   produced) — raise rather than returning it opaque or silently dropped.

## 6. Reference Data (ORDO + Krankenhausverzeichnis)

Both fetched once, not committed to git (large/stale-prone), pulled by a
build-time script:

- `backend/scripts/fetch_reference_data.py` downloads the German ORDO OWL
  release (`ORDO_de_4.9.owl`, ~50MB, CC BY 4.0, from Orphadata) and the
  Destatis Krankenhausverzeichnis (`.xlsx`, ~2MB, free/unrestricted use
  with attribution) into `backend/app/privacy_gateway/risk_scoring/data/`
  (gitignored).
- `backend/Dockerfile` runs this script during image build, so the running
  container never needs network access for reference data (consistent with
  `privacy_gateway/` never importing anything network-capable — the fetch
  itself is a build-time script outside the `privacy_gateway/` package,
  not a runtime import within it).
- At app startup, `risk_scoring/reference_data.py` parses the bundled OWL
  file with `rdflib` (extracting German `rdfs:label`/`skos:prefLabel`
  values into an in-memory set of rare-disease names) and the bundled
  `.xlsx` with `openpyxl` (extracting the facility-name column into an
  in-memory set for the hospital gazetteer) — both parsed once, held in
  memory, no per-request I/O.
- Local dev / CI must run the fetch script before first use (documented in
  README); CI caches the downloaded files between runs to avoid re-fetching
  ~50MB every build.

**New backend dependencies:** `presidio-analyzer`, `spacy` +
`de_core_news_lg` (downloaded via `python -m spacy download` at build
time, same build-time-not-runtime principle as the reference data),
`rdflib`, `openpyxl`.

## 7. Golden Corpus & Testing

`evaluation/golden_corpus/` (new top-level directory — the scaffold plan
deferred creating `evaluation/` to "the benchmark plan"; this plan creates
just the corpus data, not the scored-benchmark harness that will later live
alongside it): 20-25 hand-written synthetic German clinical notes as JSON
fixtures, each with ground-truth entity-span annotations (offset, type).
Covers: each entity type at least twice, a deliberate quasi-identifier
combination case (age + city + rare disease + date, per §8's own example)
to exercise the HIGH-escalation path, and one exact-string-match-limitation
case (a name referenced 3+ ways in one note, to make the coreference
limitation visible in test output rather than silently passing).

Tests (`backend/tests/privacy_invariants/`):
- Detection: each detector layer's precedence and refinement behavior.
- Risk scoring: quasi-identifier combination escalation fires correctly;
  confidence threshold blocks correctly.
- Pipeline: no raw golden-corpus PII string ever appears in `sanitize()`'s
  output, for every corpus example (regression, run against the whole
  corpus in CI, per §9).
- Output guard: cross-tenant/cross-conversation token resolution always
  fails (reuses the same negative-test pattern already established for
  `TokenVault` itself); a raw-looking PII string in LLM output is always
  caught as a leakage event.

## 8. Package Structure

```
backend/app/privacy_gateway/
  detectors/
    base.py            # Span dataclass, Detector protocol
    regex_detector.py
    presidio_detector.py
    custom_recognizers.py
  risk_scoring/
    scorer.py
    reference_data.py  # ORDO + Krankenhausverzeichnis loaders
    data/               # gitignored, build-time fetched
  pseudonymization/
    pseudonymizer.py
  output_guard/
    guard.py
  pipeline.py
backend/scripts/
  fetch_reference_data.py
evaluation/
  golden_corpus/
    *.json
```
