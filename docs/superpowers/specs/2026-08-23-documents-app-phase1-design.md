# Documents App — Phase 1 (Upload, OCR, Markdown, Text Anonymization, Chat Attach)

**Status:** Approved for implementation planning
**Date:** 2026-08-23
**Related:** ADR-0001, ADR-0004, ADR-0006, ADR-0007, ADR-0008, ADR-0009, ADR-0013,
ADR-0020, `docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md`,
`docs/superpowers/specs/2026-08-21-conversation-history-context-and-usage-design.md`

## 1. Purpose & Scope

Users (healthcare staff) want to anonymize documents — PDFs and pictures, e.g. a
scanned röntgen (x-ray) image with a patient name printed on it — without a
separate native application. This spec adds a "Documents" app to this product's
existing multi-app chat catalog (alongside the existing "anonymization" chat app),
reusing the current `privacy_gateway` anonymization engine rather than building a
new one.

A user uploads a document to a new library view. The backend extracts its text
(OCR for scans/images, text-layer extraction for digital PDFs), converts it to
Markdown, and anonymizes the extracted text through the existing `privacy_gateway`
pipeline. If the tenant is entitled to the new "documents" app, the user can attach
a processed document into a chat conversation.

The pipeline's approach (stage breakdown, entity types, document model) is adapted
from a reference macOS app at `/Users/imac/Documents/PROD/pdf-mdfier` — its Swift
code is not reused, only its architecture. That research surfaced an important gap
to close explicitly in this spec, not silently inherit: the reference app never
redacts PII printed in image *pixels* (e.g., a name on an x-ray) — it only
anonymizes OCR'd *text*, then drops images from its exports to avoid leaking them.

**Explicitly out of scope for this slice** (Phase 2 candidates, called out here so
they aren't silently assumed):

- Anonymized PDF/DOCX export (returning a redacted PDF/DOCX rather than markdown).
- Pixel-level image redaction (blacking out a name directly on x-ray/scan pixels).
- Manual/search-based redaction UI (a human reviewing and additionally redacting).
- Rich table reconstruction — Tesseract's layout/table detection is weak; this
  slice ships paragraph/heading/list reconstruction only, tables extract as
  best-effort concatenated text.
- Multi-column reading order detection — flat top-to-bottom order only.
- Cross-document/patient-level linking.
- Tenant-wide document sharing/visibility (branch/tenant read access like
  conversations have) — documents are private to their uploader.
- A persisted message-level attachment record (attached document content is
  folded into the message's own `content`/`sanitized_content`, not tracked as
  separate structured metadata).
- SSE/real-time push for processing status — polling only.

This slice retains enough intermediate data (`structured_blocks` with OCR bounding
boxes) that a future Phase 2 doesn't need to re-run OCR/extraction from scratch.

## 2. Binding Product Decisions

These were decided with the user during design and are constraints on this spec,
not open questions for the implementer:

1. **OCR engine: self-hosted Tesseract**, not a cloud OCR API. Consistent with this
   project's "no external data boundary" stance (ADR-0001, ADR-0013) — raw,
   un-anonymized document images must never leave this infrastructure.
2. **Entitlement: a new standalone `"documents"` app-catalog entry/entitlement**
   (mirrors `"anonymization"`), independently grantable — not bundled into the
   existing anonymization entitlement.
3. **Original-file preview: the uploader (and only the uploader) can view the
   original raw file** in their own library, behind a permanent "not anonymized"
   warning banner. The original is never sent anywhere else (chat, exports, other
   users) — this is what makes "recognize the document's type and offer a preview"
   meaningful without contradicting the privacy stance.

Because this slice has no pixel-level redaction, an x-ray upload gets its
OCR'd/printed name replaced only in the resulting *markdown* — the original image's
pixels are unchanged, and the UI must make this unambiguous (§7), not leave it as
an implementation detail a user could miss.

## 3. Reuse Points (do not reimplement)

- `backend/app/privacy_gateway/pipeline.py::Pipeline.sanitize/deanonymize` —
  text-in, text-out pseudonymization operating on character offsets. Works
  unmodified on markdown text extracted from a document; this spec generalizes its
  *scoping* (§5) but not its detection/pseudonymization logic (ADR-0006, ADR-0007).
- `backend/app/auth/dependencies.py::require_app_entitlement(app_key)` — the exact
  mechanism already gating the `"anonymization"` app; reused verbatim for the new
  `"documents"` app key.
- `backend/alembic/versions/0007_apps_catalog_and_entitlements.py` — template for
  both an RLS-protected tenant table and seeding a new app-catalog row.
- The frontend app-catalog pattern (`frontend/lib/appCatalog.ts` +
  `AvailableAppsProvider.tsx` + `GET /api/apps`) — already generic over app `key`;
  a new `"documents"` row needs no admin-UI or provider code changes.
- `frontend/lib/api/{conversations,admin}.ts` — the `fetch`/`accessToken`-first/
  `*ApiError` convention mirrored by a new `lib/api/documents.ts`.

## 4. Data Model

### 4.1 New table `documents`

```
documents
  id               uuid PK, default gen_random_uuid()
  tenant_id        uuid NOT NULL, FK -> tenants.id
  user_id          uuid NOT NULL  (uploader; composite FK -> users(tenant_id, id))
  filename         text NOT NULL
  content_type     text NOT NULL
  document_type    text NULL      -- "pdf_digital" | "pdf_scanned" | "image"; set during processing
  byte_size        integer NOT NULL
  status           text NOT NULL DEFAULT 'queued'  -- queued | processing | ready | failed
  error_message    text NULL
  page_count       integer NULL
  raw_storage_path text NOT NULL  -- filesystem path, see §4.4
  structured_blocks jsonb NULL    -- Phase 2 hook, see §4.3
  sanitized_markdown text NULL    -- the Phase 1 output artifact
  created_at       timestamptz NOT NULL DEFAULT now()
  updated_at       timestamptz NOT NULL DEFAULT now()
  deleted_at       timestamptz NULL
```

RLS: `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`,
`CREATE POLICY tenant_isolation ON documents USING (tenant_id = current_setting('app.current_tenant_id')::uuid) WITH CHECK (same)`,
`GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_runtime` — following the
exact pattern used for `conversations`/`messages` (verify against those tables'
migration at implementation time).

New `backend/app/db/repositories/document_repository.py::DocumentRepository(BaseRepository)`
mirroring `ConversationRepository`: `create`, `get(tenant_id, id)`,
`list_for_user(tenant_id, user_id)` (excludes soft-deleted, ordered
`created_at desc`), `set_status(tenant_id, id, status, error_message=None)`,
`set_result(tenant_id, id, structured_blocks, sanitized_markdown, page_count, document_type)`,
`soft_delete(tenant_id, id)`.

### 4.2 `TokenMapping` scope generalization

Today (`backend/app/models/token_mapping.py`): `conversation_id` is `NOT NULL` with
a composite FK `(tenant_id, conversation_id) -> conversations(tenant_id, id)`, and
`UniqueConstraint("tenant_id", "conversation_id", "token")`. Documents are not
conversations, so every token mapping needs a scope that can be either kind:

- Add `scope_type: text NOT NULL` (`"conversation"` | `"document"`).
- Make `conversation_id` nullable.
- Add `document_id: uuid NULL` with its own composite FK
  `(tenant_id, document_id) -> documents(tenant_id, id)`.
- `CHECK` constraint:
  ```sql
  CHECK (
    (scope_type = 'conversation' AND conversation_id IS NOT NULL AND document_id IS NULL)
    OR
    (scope_type = 'document' AND document_id IS NOT NULL AND conversation_id IS NULL)
  )
  ```
- Replace the single unique constraint with two **partial unique indexes** (a plain
  constraint over sometimes-NULL columns would not enforce per-scope uniqueness,
  since Postgres treats each NULL as distinct):
  ```sql
  CREATE UNIQUE INDEX uq_token_mappings_conversation_scope
    ON token_mappings (tenant_id, conversation_id, token) WHERE scope_type = 'conversation';
  CREATE UNIQUE INDEX uq_token_mappings_document_scope
    ON token_mappings (tenant_id, document_id, token) WHERE scope_type = 'document';
  ```

`backend/app/privacy_gateway/token_vault/vault.py::TokenVault` — every method
(`create_mapping`, `resolve_token`, `resolve_tokens`, `delete_mapping`,
`expire_mapping`, and the internal `_find_mapping`/`_associated_data` helpers)
changes its scoping parameter from `conversation_id: UUID` to
`scope_type: Literal["conversation", "document"], scope_id: UUID`, populating
whichever of `conversation_id`/`document_id` matches `scope_type`. The AES-GCM AAD
changes from `f"{tenant_id}:{conversation_id}:{token}"` to
`f"{tenant_id}:{scope_type}:{scope_id}:{token}"` — binding `scope_type` into the
ciphertext means a document-scoped mapping's ciphertext structurally cannot decrypt
under a conversation scope (an `InvalidTag` at decrypt time), independent of the
`CHECK`/unique-index guarantees at the schema layer. This is defense in depth, not
redundant: schema constraints protect against bugs in this codebase; AAD binding
protects against a row's `scope_type` being altered or misread after the fact.

`backend/app/privacy_gateway/pseudonymization/pseudonymizer.py::apply` and
`backend/app/privacy_gateway/output_guard/guard.py`'s vault-touching paths take the
same generalized `(scope_type, scope_id)` pair. `backend/app/privacy_gateway/pipeline.py::Pipeline.sanitize/deanonymize`
change signature from `(tenant_id, conversation_id, text)` to
`(tenant_id, scope_type, scope_id, text)`.

**This is a security-boundary change**, not a routine schema addition — it touches
the guarantees ADR-0008 (isolated token vault) and ADR-0009 (token scope) document.
Record it in a new `docs/adr/0023-token-vault-scope-generalization.md` that extends
those two ADRs: states the new `scope_type` dimension, the AAD-binding change, and
explicitly notes this is *not* the "per-patient scope" alternative ADR-0009
considered and deferred — document scope is document-local (one document, one
scope), not identity-linking, and creates no cross-conversation correlation.

**All existing call sites must be updated in the same change** (a breaking
internal-protocol change, same category as prior signature changes to this
pipeline):
- `backend/app/api/chat.py`: `send_message`'s `pipeline.sanitize(...)` call and
  `_stream_and_guard`'s `pipeline.deanonymize(...)` call — both pass
  `scope_type="conversation", scope_id=conversation_id`.
- `backend/app/api/conversations.py::get_messages`: its
  `pipeline.deanonymize(tenant_id, conversation_id, ...)` call — same update.
- New document-processing code (§6) passes `scope_type="document", scope_id=document.id`.

### 4.3 `structured_blocks` (Phase 2 hook)

JSONB list of `{page: int, block_type: str, order: int, sanitized_text: str,
source_bbox: [x, y, w, h] | null}`. `block_type` ∈
`heading|paragraph|list_item|caption`. `source_bbox` is the OCR word/line bounding
box on the source page image when known (null for a digital-PDF text-layer block,
which has no useful pixel coordinates in this slice). Phase 1 itself only ever
reads `sanitized_markdown` (rendered from these blocks) for display/attach; this
column exists purely so a future Phase 2 (pixel redaction, anonymized PDF export)
doesn't need to re-run OCR/extraction.

### 4.4 Raw file storage

Raw uploads are stored on a new filesystem volume
(`documents_data:/data/documents`, added to `docker-compose.yml` alongside
`postgres_data`/`ollama_data`), one file per document at
`/data/documents/{tenant_id}/{document_id}/original.<ext>`, **not** in Postgres
(avoids bloating the DB with binary blobs; no existing precedent for that in this
schema). `documents.raw_storage_path` stores the path. Raw bytes are never sent to
the LLM provider or included in any response except the owner-only
`GET /api/documents/{id}/original` endpoint (§6.3) implementing the original-file
preview decision (§2, decision 3).

### 4.5 Migrations

1. `0008_documents_table_and_catalog_entry.py` — creates `documents` + RLS +
   grants, and seeds the `"documents"` app-catalog row (`INSERT INTO apps (...)
   VALUES (..., 'documents', 'Dokumente', 'Dokumenten-Upload, OCR und
   Anonymisierung', true)`). **Deliberately no backfill** of
   `tenant_app_entitlements`/`tenant_app_assignments` for existing tenants (unlike
   migration 0007's anonymization backfill) — this is a new, separately-sold
   entitlement; ops grants it per tenant explicitly via `ops_admin`. Comment this
   explicitly in the migration so a future reader doesn't "fix" it by copying
   0007's backfill pattern.
2. `0009_token_mapping_document_scope.py` — the `TokenMapping` changes in §4.2,
   including a data migration backfilling existing rows to
   `scope_type = 'conversation'` before the `CHECK` constraint is added.

## 5. Backend Module Layout: `backend/app/document_gateway/`

New package, sibling to `privacy_gateway/`/`llm_gateway/`, following the same
internal layering convention (stages as sub-packages, a thin orchestrator).
Imports `privacy_gateway`; never the reverse. Add an import-linter contract in
`backend/pyproject.toml` mirroring the existing `privacy_gateway` contract:

```toml
[[tool.importlinter.contracts]]
name = "Document gateway must not import API or LLM gateway layers"
type = "forbidden"
source_modules = ["app.document_gateway"]
forbidden_modules = ["app.api", "app.llm_gateway"]
```

```
backend/app/document_gateway/
├── __init__.py
├── pipeline.py                     # DocumentPipeline: orchestrates the stages below
├── models.py                       # RawPage, Block, ProcessResult dataclasses
├── classification.py               # content_type + text-layer presence -> document_type
├── extraction/
│   ├── __init__.py
│   ├── base.py                     # Extractor protocol: extract(bytes) -> list[RawPage]
│   ├── pdf_text_layer.py           # pypdf: digital PDFs with an existing text layer
│   ├── pdf_rasterizer.py           # pdf2image/poppler: PDF page -> PIL.Image, for scans
│   └── ocr.py                      # Tesseract wrapper (pytesseract), behind an interface
├── structure/
│   ├── __init__.py
│   └── reconstructor.py            # heading/list/reading-order heuristics -> list[Block]
└── rendering/
    ├── __init__.py
    └── markdown_renderer.py        # sanitized list[Block] -> markdown string
```

### 5.1 Pipeline stages

`DocumentPipeline.process(tenant_id: UUID, document_id: UUID, raw_bytes: bytes, content_type: str) -> ProcessResult`:

1. **Classify** (`classification.py`): inspect `content_type`. For PDFs, attempt
   `pdf_text_layer.extract` first — if it yields a non-trivial text layer
   (heuristic: more than N characters per page on average), classify
   `pdf_digital`; otherwise `pdf_scanned`. Images are always `image`.
2. **Extract** (`extraction/`):
   - `pdf_digital` → `pdf_text_layer.extract` returns one `RawPage(page_no, text,
     word_boxes=None)` per page — no OCR needed.
   - `pdf_scanned` → `pdf_rasterizer.rasterize(bytes) -> list[PIL.Image]`, then
     `ocr.run(image) -> RawPage(page_no, text, word_boxes)` per page via Tesseract.
   - `image` → a single `ocr.run(image)` call, one `RawPage`.
   - `ocr.run` invokes Tesseract locally (subprocess via `pytesseract`), entirely
     offline — no network call, satisfying the OCR-engine decision (§2, decision 1).
3. **Structure** (`structure/reconstructor.py`): per-page heuristics on the
   `RawPage` text/boxes — font-size/line-height deltas (from `pdf_text_layer`) or
   box-height deltas (from OCR) to guess headings; leading bullet/numeral patterns
   for lists; top-to-bottom, left-to-right reading order (no multi-column
   detection). Produces `list[Block(page, block_type, order, raw_text, bbox)]` —
   this *is* the pre-sanitization shape of `structured_blocks`.
4. **Anonymize (reuse point — not reimplemented)**: for each `Block`, call
   `privacy_gateway.Pipeline.sanitize(tenant_id, "document", document_id,
   block.raw_text)` — **once per block, not once for the whole document**.
   Rationale: `RiskScorer` fails the whole unit closed on a single low-confidence
   span (ADR-0020). Sanitizing an entire multi-page document as one string means
   one ambiguous paragraph anywhere fails the *entire document*. Per-block scoping
   keeps the fail-closed guarantee — uncertain text never reaches output, which is
   the ADR's actual requirement — while keeping partial documents usable: on a
   block-level `LowConfidenceSpanError`/`HighRiskMessageError`/`ResidualPIIError`,
   that block's `sanitized_text` becomes a fixed, content-free placeholder (e.g.
   `> [Block konnte nicht sicher anonymisiert werden]`), the block is flagged
   `blocked: true` in `structured_blocks`, and the document as a whole still
   reaches `status="ready"`, not `failed`. No raw or uncertain PII is ever included
   in the output either way — this changes the *unit* the fail-closed guarantee
   applies to (from "message" to "block"), not the guarantee itself.
5. **Render** (`rendering/markdown_renderer.py`): walk the sanitized `list[Block]`
   in order, emit Markdown (`#`/`##` for headings, `-`/`1.` for lists, plain
   paragraphs, the placeholder blockquote for blocked blocks), producing the final
   `sanitized_markdown` string — the only artifact the frontend/chat-attach flow
   reads in this slice.

### 5.2 New dependencies

`backend/pyproject.toml`: `pypdf`, `pdf2image`, `pytesseract`, `pillow` (verify
whether already present transitively via spaCy/Presidio).

`backend/Dockerfile`: add
`RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu poppler-utils && rm -rf /var/lib/apt/lists/*`
before the `pip install` step — `tesseract-ocr-deu` specifically, matching this
product's German-medical-context language model choices elsewhere (`de_core_news_lg`).
Both are system packages baked into the image at build time; no runtime network
access needed, consistent with the existing Dockerfile comment about spaCy/ORDO
data being baked in.

`docker-compose.yml`: no new service — Tesseract runs as a subprocess inside the
existing `backend` container.

## 6. API Endpoints

New router `backend/app/api/documents.py`
(`APIRouter(prefix="/api/documents", tags=["documents"])`), registered in
`backend/app/main.py` alongside the existing routers. Every endpoint requires
`Depends(require_app_entitlement(app_key="documents"))` stacked with
`Depends(get_current_user)`/`Depends(get_db_session)`, matching the existing
convention in `backend/app/api/chat.py`.

New Pydantic schemas in `backend/app/api/schemas.py`:

```python
class DocumentSummary(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    document_type: str | None       # null while queued/processing
    status: str                      # queued | processing | ready | failed
    error_message: str | None
    page_count: int | None
    byte_size: int
    created_at: datetime
    updated_at: datetime

class DocumentDetail(DocumentSummary):
    sanitized_markdown: str | None   # populated only once status == "ready"

class SendMessageIn(BaseModel):      # existing schema, extended
    content: str
    document_ids: list[uuid.UUID] | None = None
```

### 6.1 `POST /api/documents`

`multipart/form-data`, single `file` field. Validates `content_type` against an
allowlist (`application/pdf`, `image/jpeg`, `image/png`, `image/tiff`) and a max
size (new `Settings.max_document_size_bytes`, default e.g. 25 MB). Creates a
`Document` row (`status="queued"`), writes raw bytes to the storage path (§4.4),
schedules background processing (§8), returns `202 Accepted` with `DocumentSummary`.
Unsupported content-type or oversized file → `422`.

### 6.2 `GET /api/documents`

Returns `list[DocumentSummary]` for the caller's own uploads only
(`user_id`-filtered — no tenant-wide/branch sharing in this slice, unlike
conversations' visibility scopes), excluding soft-deleted, newest first.

### 6.3 `GET /api/documents/{id}`

Returns `DocumentDetail` — what the frontend polls. `404` if not found or not
owned by the caller.

### 6.4 `GET /api/documents/{id}/original`

Returns the raw original file bytes with its original `content_type`, **owner-only**
— implements the original-file-preview decision (§2, decision 3). `404` if not found/not
owned. The frontend renders the permanent "not anonymized" warning banner around
this, never presents it as safe to share.

### 6.5 `DELETE /api/documents/{id}`

Soft-delete (`deleted_at` set, matching `Conversation.soft_delete`). Also expires
the document's `TokenMapping` rows via `TokenVault.expire_mapping` for each token
(or a bulk repository method) — a safer default given this is a privacy-critical
vault, and low added complexity. Owner-only. `204`.

### 6.6 `POST /api/conversations/{conversation_id}/messages` (extended)

`SendMessageIn.document_ids` accepted. When non-empty:
1. Load each `Document` — must belong to the caller (`user_id` match) and be
   `status == "ready"`; otherwise `404`/`422`.
2. Concatenate each document's `sanitized_markdown`, each wrapped in a clear
   delimiter (e.g. `\n\n--- Angehängtes Dokument: {filename} ---\n\n{markdown}\n\n--- Ende Dokument ---\n\n`),
   and prepend to `body.content` **before** the existing
   `pipeline.sanitize(user.tenant_id, "conversation", conversation_id, combined_content)`
   call — the exact same fail-closed path every chat message already goes through;
   the attach path is not a bypass of the chat privacy pipeline.
3. Requires the `"documents"` entitlement **in addition to** `"anonymization"` —
   checked explicitly in the handler body (only when `document_ids` is non-empty),
   since a tenant without `"documents"` must not be able to smuggle document
   content into chat via a raw API call even without the UI affordance. (Not a
   blanket second `Depends`, because it's conditional on the field being present.)
4. If the combined content would exceed the model's context window (per
   `docs/superpowers/specs/2026-08-21-conversation-history-context-and-usage-design.md`'s
   trimming design), reject with a clear `422` error rather than silently
   truncating — truncation risks dropping pseudonym tokens the model needs for
   consistency with earlier turns.

## 7. Frontend Changes

- `frontend/lib/appCatalog.ts` — add a `documents` entry (icon + color) to
  `APP_CATALOG`. No changes needed to `AvailableAppsProvider`, the dashboard grid,
  or the admin entitlement UI — all are already generic over app `key`; the new
  catalog row (§4.5) is picked up automatically once seeded and granted.
- New `frontend/lib/api/documents.ts` (+ colocated `documents.test.ts`) — follows
  the `fetch`/`accessToken`-first/`*ApiError`-throwing convention of
  `lib/api/conversations.ts`:
  - `uploadDocument(accessToken, file: File): Promise<DocumentSummary>` — the
    first `FormData` upload in this codebase. Do **not** set a `Content-Type`
    header; let the browser generate the multipart boundary.
  - `listDocuments(accessToken): Promise<DocumentSummary[]>`
  - `getDocument(accessToken, id): Promise<DocumentDetail>` — used for polling.
  - `getOriginalFileUrl(id)` / a fetch helper for the owner-only original-file view.
  - `deleteDocument(accessToken, id): Promise<void>`
- `frontend/lib/api/types.ts` — add `DocumentSummary`, `DocumentDetail` mirroring
  the backend schemas.
- New route tree under `frontend/app/(shell)/apps/documents/`, mirroring
  `apps/anonymization/`'s sidebar+detail pattern and `admin/{users,apps}/page.tsx`'s
  list/table conventions:
  ```
  apps/documents/
  ├── layout.tsx              # DocumentSidebar + <main>{children}</main>
  ├── layout.module.css
  ├── page.tsx                 # empty-state / upload CTA
  └── [documentId]/
      ├── page.tsx              # detail: status, markdown preview, original-file toggle
      └── page.module.css
  ```
- New `frontend/components/DocumentSidebar.tsx` (+ `.module.css`, `.test.tsx`),
  mirroring `ConversationSidebar.tsx`: upload control (hidden `<input
  type="file">`, calls `uploadDocument`, optimistically prepends the returned
  summary, navigates to the new document), list of `DocumentSummary` rows with a
  status indicator (queued/processing spinner, ready checkmark, failed icon) and
  delete button. Unlike `ConversationSidebar`, the list needs periodic re-fetch
  while any document is `queued`/`processing` (`setInterval` re-`listDocuments()`
  every few seconds, cleared once none are pending — no SSE/websockets needed at
  this scale).
- `[documentId]/page.tsx`: fetch `getDocument` once, poll (same `setInterval`
  pattern) while `status` is `queued`/`processing`; once `ready`, render
  `sanitized_markdown`, a `document_type`-driven type badge/icon (satisfies "recognize
  the document's type"), metadata (filename, page count, uploaded date), and a
  toggle to view the original file via `GET /api/documents/{id}/original` — always
  shown with the **permanent warning banner** ("Diese Datei wurde nicht anonymisiert
  — nicht weitergeben." or equivalent), never dismissable, per §2, decision 3.
- `frontend/components/Composer.tsx` — extend `onSend` to
  `onSend(content: string, documentIds: string[])`; add an "attach document"
  control (icon button opening a small picker), rendered only when
  `useAvailableApps()`'s list contains `key === "documents"`, populated from
  `listDocuments` filtered to `status === "ready"`. Selected documents render as
  small removable chips above the textarea. Update the one call site in
  `apps/anonymization/c/[conversationId]/page.tsx`.
- `frontend/lib/api/chat.ts::sendMessage` — add a `documentIds?: string[]`
  parameter, included as `document_ids` in the request body when non-empty.
- `frontend/components/MessageBubble.tsx` — no structural change required; the
  optimistic locally-added pending message bubble (added in `handleSend` before
  the server round-trip) can show a "📄 filename" chip based on the locally-known
  `documentIds` at send time. After a reload, the persisted message shows its full
  delimited document text inline like normal prose (no special chip), since the
  backend does not persist attachment metadata separately in this slice — a known,
  cosmetic (not privacy-relevant) limitation, called out in §1's out-of-scope list.

### 7.1 Tests

`lib/api/documents.test.ts` (including asserting no `Content-Type` header is set
on upload), `DocumentSidebar.test.tsx`, `apps/documents/**/*.test.tsx`
(empty state, detail polling → ready render, failed state, original-file banner),
updated `Composer.test.tsx` (attach control hidden when `"documents"` app not
entitled, visible + functional when entitled, `onSend` called with selected
`documentIds`).

## 8. Background Processing

No task queue/Celery/Redis — matches `docker-compose.yml`'s explicit "tens of
concurrent users, no message/job queue" scoping.

- `POST /api/documents` creates the `Document` row (`status="queued"`) and commits
  synchronously, then schedules processing via FastAPI `BackgroundTasks`, whose
  handler runs `DocumentPipeline.process(...)` via a blocking call submitted to a
  **bounded** `concurrent.futures.ThreadPoolExecutor` (module-level singleton,
  e.g. `max_workers=2`, sized conservatively against the existing
  `UVICORN_WORKERS=4` default and shared CPU budget with Tesseract/OCR, which is
  genuinely CPU-heavy) — explicit bounding matters so a burst of uploads can't
  spawn unbounded OS threads each holding a Tesseract subprocess. This mirrors the
  reasoning already documented in `chat.py`'s `_stream_and_guard`/`iterate_in_threadpool`
  usage: blocking work must never run directly on the event loop.
- Status lifecycle: `queued` (row created) → `processing` (set at the start of
  `DocumentPipeline.process`, committed in its own transaction immediately so a
  concurrent `GET` sees it right away) → `ready` (success; `sanitized_markdown`,
  `structured_blocks`, `page_count`, `document_type` populated) or `failed`
  (`error_message` populated). `failed` is for stage failures (corrupt file,
  Tesseract crash, uncaught exception) — **not** used for the per-block
  anonymization-failure case in §5.1 step 4, which degrades gracefully to `ready`
  with flagged blocks instead.
- Each stage failure is caught at the top of the background task and converted to
  `status="failed"` with a generic, non-leaking `error_message` (never includes
  extracted text — the same "log categories, not content" discipline
  `Pipeline.sanitize` already follows) plus a full traceback at
  `logger.exception` level for operator debugging.
- A per-document processing timeout (e.g. 5 minutes, a new config setting) guards
  against a pathological large PDF or a hung Tesseract subprocess holding a pool
  slot indefinitely — enforced with `asyncio.wait_for` around the executor future,
  transitioning to `failed` on timeout.
- The frontend observes all of this purely via polling `GET /api/documents/{id}`
  (§7) — no SSE/websocket needed at this scale.

## 9. Testing Strategy

- **Backend unit** (`backend/tests/unit/document_gateway/`): classification
  decision table, PDF text-layer extraction (fixture PDF), OCR wrapper (mocked —
  the wrapper sits behind an interface specifically so most tests don't need a
  real Tesseract binary), reconstructor heuristics on synthetic input, markdown
  rendering (including the blocked-block placeholder), pipeline orchestration
  (asserts per-block `sanitize` calls via a fake `privacy_gateway.Pipeline`, and
  the partial-failure-degrades-to-ready behavior).
- **Backend integration** (`backend/tests/integration/`): `test_documents_api.py`
  (full upload → poll → `GET` markdown flow, 404 for another tenant's/user's
  document, delete removes it and expires its tokens, unsupported content-type
  and oversized upload rejected), `test_documents_entitlement.py`
  (`require_app_entitlement("documents")` gates every endpoint; a tenant entitled
  to `anonymization` but not `documents` gets 403 on `/api/documents` and on the
  chat-attach path), `test_chat_document_attach.py` (extends the existing
  `chat.py` integration coverage: `document_ids` prepends markdown before
  `sanitize`, a not-`ready`/not-owned document is rejected).
- **Backend privacy_invariants** (`backend/tests/privacy_invariants/`) — the
  highest-value additions: `test_document_scope_isolation.py` (a token minted
  under `scope_type="document", scope_id=doc_A` must not resolve under
  `scope_type="document", scope_id=doc_B`, nor under any `scope_type="conversation"`
  query — extends the existing `test_token_vault.py` pattern);
  `test_document_never_reaches_provider_raw.py` (a fixture document with known PII,
  run through the real `DocumentPipeline` with a stubbed deterministic OCR layer,
  asserts zero raw PII occurrences in the persisted `sanitized_markdown`, only
  tokens); `test_document_fail_closed_per_block.py` (a single ambiguous block
  doesn't abort the whole document, and its placeholder never contains original
  text); extend chat's existing privacy test to confirm a `document_ids`-attached
  message still goes through `sanitize`/`assert_no_raw_pii` exactly like any other
  message.
- **Frontend (Vitest)**: per §7.1.

## 10. Verification (end-to-end)

1. `cd backend && pytest tests/unit tests/integration tests/privacy_invariants` —
   all new and existing tests pass; `lint-imports` confirms the new
   `document_gateway` import-linter contract holds.
2. `alembic upgrade head` against a fresh dev DB — both new migrations apply
   cleanly; `\d+ documents` in psql shows `FORCE ROW LEVEL SECURITY`.
3. Manual, via `docker compose up`: grant a dev tenant the `"documents"`
   entitlement through `ops_admin` (mirroring how `"anonymization"` is granted
   today); upload a scanned image containing a fictitious name; confirm it reaches
   `status="ready"` with the name replaced by a token in the markdown preview;
   confirm the original-file view shows the permanent warning banner; attach the
   document into a chat message and confirm the assistant's context includes the
   document content per the streamed response.
4. `cd frontend && npm test` — new/updated Vitest suites pass; manual check that
   the "attach document" control in `Composer` is absent for a tenant without the
   `"documents"` entitlement.
