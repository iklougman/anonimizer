# Documents App — Phase 1: `document_gateway` Backend Pipeline, API, Background Processing, and Frontend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Context

An earlier audit of this project's unfinished work found that the "Documents" app — spec'd in full in `docs/superpowers/specs/2026-08-23-documents-app-phase1-design.md` — was only half-built: the most recent plan (`docs/superpowers/plans/2026-08-23-documents-data-model-and-token-vault-foundation.md`) implemented only the data-model foundation (the `documents` table, `Document` model/repository, and the `TokenVault`/`Pipeline` scope generalization to support document-scoped tokens), and explicitly deferred everything else — the OCR/extraction/markdown pipeline, the API endpoints, background processing, and the entire frontend — as separate follow-on work. This plan is that follow-on work. The user asked to plan it out next.

Direct code inspection (three parallel exploration passes) confirmed the foundation is fully in place and needs no changes: `documents` table + RLS + `Document` model + `DocumentRepository` (`create/get/list_for_user/set_status/set_result/soft_delete`), `TokenMapping.scope_type`/nullable `conversation_id`/`document_id` + CHECK + partial unique indexes, `TokenVault`/`Pipeline.sanitize`/`Pipeline.deanonymize`/`Pseudonymizer`/`OutputGuard` all already taking `(scope_type, scope_id)`, migrations `0008`/`0009` merged, ADR-0023 written, and `chat.py`/`conversations.py` already calling the generalized signature with `scope_type="conversation"`. This plan builds only on top of that — it does not re-touch any of it.

**Goal:** Build the `document_gateway` backend pipeline (classification, extraction incl. Tesseract OCR + PDF text-layer + PDF rasterization, structure reconstruction, markdown rendering, per-block anonymization), its API endpoints, bounded-concurrency background processing, and the complete frontend Documents app.

**Architecture:** A new `backend/app/document_gateway/` package (sibling to `privacy_gateway/`), layered as stages (`classification` → `extraction` → `structure` → per-block `privacy_gateway.Pipeline.sanitize` → `rendering`) orchestrated by a thin `DocumentPipeline.process(...)`. A new `backend/app/api/documents.py` router exposes upload/list/detail/original/delete, gated by `require_app_entitlement(app_key="documents")`. Upload creates a `queued` row synchronously and schedules processing via FastAPI `BackgroundTasks`, whose handler blocks on a **bounded** (`max_workers=2`) module-level `ThreadPoolExecutor` with a per-document timeout, transitioning `queued → processing → ready|failed`. `POST /api/conversations/{id}/messages` gains an optional `document_ids` field that prepends each attached document's `sanitized_markdown` before the existing `sanitize()` call. The frontend adds a `documents` app-catalog entry, a `DocumentSidebar` (with upload + status polling), a `d/[documentId]` detail route (markdown preview + original-file toggle behind a permanent warning banner), and extends `Composer`/`MessageBubble` to attach ready documents into chat.

**Tech Stack:** Python 3.12, FastAPI (`BackgroundTasks`), `pypdf` (PDF text-layer), `pdf2image`/poppler (PDF rasterization), `pytesseract`/Tesseract (OCR, `deu` language pack), Pillow, `concurrent.futures.ThreadPoolExecutor`, existing SQLAlchemy 2.0 / repositories / `Pipeline` / `tenant_scoped_session` layer, pytest. Next.js/React frontend, Vitest + Testing Library, no MSW.

**Spec:** `docs/superpowers/specs/2026-08-23-documents-app-phase1-design.md` §5–§10. §1–§4 (purpose/scope, product decisions, reuse points, data model) are already implemented and are **not** re-planned here.

## Global Constraints

- **No new migration.** The `documents` table (migration 0008) already has every column this plan needs (`status`, `error_message`, `page_count`, `document_type`, `structured_blocks`, `sanitized_markdown`). `TokenMapping`'s `(scope_type, scope_id)` generalization (migration 0009) is already merged and already covered end-to-end by `tests/privacy_invariants/test_token_vault.py`'s `test_document_scoped_*` tests — **do not re-implement document-scope token isolation tests**; this plan only adds a new bulk-expire method (Task 8) on top of the existing `TokenVault`.
- **`app.document_gateway` must never import `app.api` or `app.llm_gateway`** (new import-linter contract, Task 1) — the same pattern already enforced for `app.privacy_gateway`. It may freely import `app.privacy_gateway` (the reuse point for per-block `sanitize`).
- **Per-block anonymization, not per-document** (spec §5.1 step 4): `DocumentPipeline.process` calls `privacy_gateway.Pipeline.sanitize(tenant_id, "document", document_id, block.raw_text)` once per reconstructed block. A block that fails `LowConfidenceSpanError`/`HighRiskMessageError`/`ResidualPIIError` becomes a fixed, content-free placeholder and is flagged `blocked: true` — the *document* still reaches `status="ready"`. Only stage-level failures (corrupt file, OCR crash, uncaught exception, timeout) produce `status="failed"`. Never log or persist raw extracted text in an error path.
- **OCR sits behind an interface** (`OCREngine` protocol) specifically so the classification/extraction/structure/pipeline unit tests never need a real Tesseract binary — only a small number of integration-style tests (if any) touch the real `TesseractOCREngine`, and even those are optional for this plan (CI has no guaranteed Tesseract binary; the Dockerfile installs it for runtime, not for the test image).
- **Background processing is a plain synchronous function**, not `async def` — dispatched via FastAPI's `BackgroundTasks` (which runs sync callables in Starlette's own worker threadpool) and internally blocking on `future.result(timeout=...)` against a separate, bounded (`max_workers=2`) `ThreadPoolExecutor`. This keeps every test in this plan ordinary synchronous pytest — no `pytest-asyncio` dependency is introduced, deliberately, since none exists anywhere else in this codebase today.
- **Documents are private to their uploader** — every read/delete endpoint checks `document.user_id == user.user_id`, not just `tenant_id`; unlike conversations there is no branch/tenant visibility scope in this slice.
- Run `cd backend && ruff check . && lint-imports && pytest -v` at the end of every backend task, and `cd frontend && npm test` at the end of every frontend task — not just the new tests.
- No `--no-verify`, no skipping tests, no `git commit --amend`.

---

## Part A — Backend: `document_gateway` pipeline

### Task 1: Package scaffold, dependencies, import-linter contract, shared dataclasses

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/document_gateway/__init__.py`
- Create: `backend/app/document_gateway/models.py`
- Create: `backend/tests/unit/document_gateway/__init__.py`
- Create: `backend/tests/unit/document_gateway/test_models.py`

**Interfaces:**
- Produces: `WordBox(text, x, y, width, height)`, `RawPage(page_no, text, word_boxes=None)`, `Block(page, block_type, order, raw_text, bbox=None, sanitized_text=None, blocked=False)`, `ProcessResult(document_type, page_count, structured_blocks, sanitized_markdown)`. Consumed by every later task in Part A.

- [ ] **Step 1: Add new dependencies and the import-linter contract**

Edit `backend/pyproject.toml`'s `dependencies` list — add after `openpyxl`:

```toml
    "openpyxl>=3.1,<4",
    "pypdf>=5.0,<6",
    "pdf2image>=1.17,<2",
    "pytesseract>=0.3,<0.4",
    "pillow>=10.4,<11",
```

Add a third contract to `[[tool.importlinter.contracts]]` (verbatim from spec §5):

```toml
[[tool.importlinter.contracts]]
name = "Document gateway must not import API or LLM gateway layers"
type = "forbidden"
source_modules = ["app.document_gateway"]
forbidden_modules = ["app.api", "app.llm_gateway"]
```

- [ ] **Step 2: Install and verify**

Run: `cd backend && pip install -e ".[dev]"`
Expected: succeeds; `pip list` shows `pypdf`, `pdf2image`, `pytesseract`, `pillow`.

- [ ] **Step 3: Write the failing dataclasses test**

Create `backend/tests/unit/document_gateway/__init__.py` (empty).

Create `backend/tests/unit/document_gateway/test_models.py`:

```python
from app.document_gateway.models import Block, ProcessResult, RawPage, WordBox


def test_raw_page_defaults_word_boxes_to_none():
    page = RawPage(page_no=1, text="Hallo Welt")
    assert page.word_boxes is None


def test_word_box_is_a_plain_bounding_box():
    box = WordBox(text="Hallo", x=1.0, y=2.0, width=30.0, height=10.0)
    assert (box.x, box.y, box.width, box.height) == (1.0, 2.0, 30.0, 10.0)


def test_block_defaults_are_unsanitized_and_unblocked():
    block = Block(page=1, block_type="paragraph", order=0, raw_text="Hallo Herr Müller")
    assert block.sanitized_text is None
    assert block.blocked is False
    assert block.bbox is None


def test_process_result_carries_the_render_output():
    result = ProcessResult(
        document_type="image",
        page_count=1,
        structured_blocks=[{"page": 1, "block_type": "paragraph", "order": 0,
                             "sanitized_text": "x", "source_bbox": None, "blocked": False}],
        sanitized_markdown="x\n",
    )
    assert result.page_count == 1
    assert result.sanitized_markdown == "x\n"
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.document_gateway'`.

- [ ] **Step 5: Create the package and dataclasses**

Create `backend/app/document_gateway/__init__.py` (empty).

Create `backend/app/document_gateway/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

# block_type values this slice's reconstructor/renderer understand. "caption" is
# accepted end-to-end (schema, renderer) for forward compatibility with a future
# richer reconstructor, but structure/reconstructor.py never emits it in Phase 1
# (spec §1: no rich table/caption detection this slice) -- documented here, not
# silently unsupported.
BLOCK_TYPES = ("heading", "paragraph", "list_item", "caption")


@dataclass(frozen=True)
class WordBox:
    """One OCR word's bounding box on its source page image, in pixel units."""

    text: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class RawPage:
    """One page's extracted plain text, before structure reconstruction.

    word_boxes is populated only for OCR-sourced pages (image / pdf_scanned) and
    is None for pdf_text_layer-sourced pages, which have no useful pixel
    coordinates in this slice (spec §4.3).
    """

    page_no: int
    text: str
    word_boxes: list[WordBox] | None = None


@dataclass
class Block:
    """One reconstructed structural unit (heading/paragraph/list item), before
    and after per-block anonymization. `sanitized_text`/`blocked` are populated
    by DocumentPipeline.process, not by structure/reconstructor.py."""

    page: int
    block_type: str
    order: int
    raw_text: str
    bbox: tuple[float, float, float, float] | None = None
    sanitized_text: str | None = None
    blocked: bool = False


@dataclass(frozen=True)
class ProcessResult:
    """DocumentPipeline.process's return value -- exactly what
    DocumentRepository.set_result persists."""

    document_type: str
    page_count: int
    structured_blocks: list[dict]
    sanitized_markdown: str
```

- [ ] **Step 6: Run to verify it passes, then the full suite + linters**

Run:
```bash
cd backend
pytest tests/unit/document_gateway/test_models.py -v
ruff check .
lint-imports
pytest -v
```
Expected: new tests PASS; `ruff` clean; all three import-linter contracts KEPT (the new one trivially, since nothing yet imports `app.api`/`app.llm_gateway` from `app.document_gateway`); full suite green.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/app/document_gateway/__init__.py \
  backend/app/document_gateway/models.py backend/tests/unit/document_gateway/__init__.py \
  backend/tests/unit/document_gateway/test_models.py
git commit -m "feat: scaffold document_gateway package and shared dataclasses"
```

---

### Task 2: Classification + PDF text-layer extraction

**Files:**
- Create: `backend/app/document_gateway/extraction/__init__.py`
- Create: `backend/app/document_gateway/extraction/base.py`
- Create: `backend/app/document_gateway/extraction/pdf_text_layer.py`
- Create: `backend/app/document_gateway/classification.py`
- Create: `backend/tests/unit/document_gateway/test_pdf_text_layer.py`
- Create: `backend/tests/unit/document_gateway/test_classification.py`

**Interfaces:**
- Produces: `Extractor` protocol (`extract(bytes) -> list[RawPage]`); `pdf_text_layer.extract(raw_bytes: bytes) -> list[RawPage]`; `classification.classify(content_type: str, raw_bytes: bytes) -> tuple[str, list[RawPage] | None]` (the `document_type` string plus, when it already had to extract to decide, the already-extracted `pdf_digital` pages — avoids extracting the same PDF twice). Consumed by Task 6 (`DocumentPipeline`).

- [ ] **Step 1: Write the failing extractor test using a generated in-memory PDF**

Create `backend/tests/unit/document_gateway/test_pdf_text_layer.py`. Uses `pypdf.PdfWriter` to build a tiny digital PDF at test time (no binary fixture committed — matches this repo having no other binary test fixtures):

```python
import io

from pypdf import PdfWriter

from app.document_gateway.extraction import pdf_text_layer


def _make_digital_pdf(pages_text: list[str]) -> bytes:
    """A minimal digital PDF with a real, extractable text layer -- built with
    pypdf itself so this test needs no committed binary fixture and no external
    PDF-generation tool."""
    writer = PdfWriter()
    for text in pages_text:
        writer.add_blank_page(width=595, height=842)
        # add_blank_page has no text-drawing API; pypdf's own text layer is
        # populated by any real PDF producer (Word export, scanner OCR layer,
        # etc.) in production. For a hermetic unit test we instead monkeypatch
        # PdfReader.pages[i].extract_text in the test below rather than
        # fight pypdf's low-level content-stream API here.
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_extract_returns_one_raw_page_per_pdf_page(monkeypatch):
    raw_bytes = _make_digital_pdf(["", ""])

    texts = iter(["Erste Seite Text.", "Zweite Seite Text."])
    monkeypatch.setattr(
        "pypdf.PageObject.extract_text", lambda self, *a, **kw: next(texts)
    )

    pages = pdf_text_layer.extract(raw_bytes)

    assert [p.page_no for p in pages] == [1, 2]
    assert [p.text for p in pages] == ["Erste Seite Text.", "Zweite Seite Text."]
    assert all(p.word_boxes is None for p in pages)


def test_extract_returns_empty_string_for_a_page_with_no_text_layer(monkeypatch):
    raw_bytes = _make_digital_pdf([""])
    monkeypatch.setattr("pypdf.PageObject.extract_text", lambda self, *a, **kw: None)

    pages = pdf_text_layer.extract(raw_bytes)

    assert pages[0].text == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_pdf_text_layer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.document_gateway.extraction'`.

- [ ] **Step 3: Create the extraction package and `pdf_text_layer.py`**

Create `backend/app/document_gateway/extraction/__init__.py` (empty).

Create `backend/app/document_gateway/extraction/base.py`:

```python
from __future__ import annotations

from typing import Protocol

from app.document_gateway.models import RawPage


class Extractor(Protocol):
    def extract(self, raw_bytes: bytes) -> list[RawPage]: ...
```

Create `backend/app/document_gateway/extraction/pdf_text_layer.py`:

```python
from __future__ import annotations

import io

import pypdf

from app.document_gateway.models import RawPage


def extract(raw_bytes: bytes) -> list[RawPage]:
    """Extracts a digital PDF's existing text layer -- no OCR involved.

    A page whose extract_text() returns None (pypdf's documented behavior for
    a page with no recoverable text) becomes an empty-string RawPage, not a
    dropped page -- page numbering must stay 1:1 with the source PDF.
    """
    reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
    return [
        RawPage(page_no=index, text=page.extract_text() or "")
        for index, page in enumerate(reader.pages, start=1)
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/unit/document_gateway/test_pdf_text_layer.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing classification test**

Create `backend/tests/unit/document_gateway/test_classification.py`:

```python
import pytest

from app.document_gateway import classification
from app.document_gateway.models import RawPage


def _fake_pages(monkeypatch, pages: list[RawPage]) -> None:
    monkeypatch.setattr(
        "app.document_gateway.classification.pdf_text_layer.extract", lambda raw_bytes: pages
    )


def test_image_content_type_is_always_classified_image():
    document_type, pages = classification.classify("image/png", b"fake-bytes")
    assert document_type == "image"
    assert pages is None


@pytest.mark.parametrize("content_type", ["image/jpeg", "image/tiff"])
def test_every_supported_image_content_type_classifies_as_image(content_type):
    document_type, _ = classification.classify(content_type, b"fake-bytes")
    assert document_type == "image"


def test_pdf_with_a_substantial_text_layer_classifies_as_pdf_digital(monkeypatch):
    long_text = "Befund: " + "Wort " * 40  # well over the per-page threshold
    _fake_pages(monkeypatch, [RawPage(page_no=1, text=long_text)])

    document_type, pages = classification.classify("application/pdf", b"fake-bytes")

    assert document_type == "pdf_digital"
    assert pages == [RawPage(page_no=1, text=long_text)]


def test_pdf_with_no_meaningful_text_layer_classifies_as_pdf_scanned(monkeypatch):
    _fake_pages(monkeypatch, [RawPage(page_no=1, text=""), RawPage(page_no=2, text="ab")])

    document_type, pages = classification.classify("application/pdf", b"fake-bytes")

    assert document_type == "pdf_scanned"
    assert pages is None  # not reused -- pdf_scanned needs rasterized OCR, not this text layer


def test_pdf_with_zero_pages_classifies_as_pdf_scanned(monkeypatch):
    _fake_pages(monkeypatch, [])
    document_type, pages = classification.classify("application/pdf", b"fake-bytes")
    assert document_type == "pdf_scanned"
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_classification.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.document_gateway.classification'`.

- [ ] **Step 7: Create `classification.py`**

Create `backend/app/document_gateway/classification.py`:

```python
from __future__ import annotations

from app.document_gateway.extraction import pdf_text_layer
from app.document_gateway.models import RawPage

# Spec §5.1 step 1: "more than N characters per page on average" -- chosen
# conservatively so a PDF whose text layer is just a filename/header (a common
# shape for a scan with a thin embedded OCR layer added by the scanner
# software) still routes to pdf_scanned and gets this pipeline's own OCR pass,
# rather than being classified pdf_digital off a near-empty layer.
MIN_AVERAGE_CHARS_PER_PAGE = 40


def classify(content_type: str, raw_bytes: bytes) -> tuple[str, list[RawPage] | None]:
    """Returns (document_type, pages_if_already_extracted).

    For a PDF, this attempts pdf_text_layer.extract first so a pdf_digital
    classification doesn't force a second, redundant extraction pass in the
    pipeline -- the pages are returned here and reused directly. A
    pdf_scanned classification's pages are discarded (None): that path needs
    rasterized OCR, not this near-empty text layer.
    """
    if content_type != "application/pdf":
        return "image", None

    pages = pdf_text_layer.extract(raw_bytes)
    if not pages:
        return "pdf_scanned", None

    average_chars = sum(len(page.text) for page in pages) / len(pages)
    if average_chars >= MIN_AVERAGE_CHARS_PER_PAGE:
        return "pdf_digital", pages
    return "pdf_scanned", None
```

- [ ] **Step 8: Run to verify it passes, then full suite + linters**

Run:
```bash
cd backend
pytest tests/unit/document_gateway -v
ruff check .
lint-imports
pytest -v
```
Expected: all PASS; linters clean.

- [ ] **Step 9: Commit**

```bash
git add backend/app/document_gateway/extraction/__init__.py \
  backend/app/document_gateway/extraction/base.py \
  backend/app/document_gateway/extraction/pdf_text_layer.py \
  backend/app/document_gateway/classification.py \
  backend/tests/unit/document_gateway/test_pdf_text_layer.py \
  backend/tests/unit/document_gateway/test_classification.py
git commit -m "feat: add PDF text-layer extraction and document classification"
```

---

### Task 3: PDF rasterization + OCR wrapper (Tesseract behind an interface)

**Files:**
- Create: `backend/app/document_gateway/extraction/pdf_rasterizer.py`
- Create: `backend/app/document_gateway/extraction/ocr.py`
- Create: `backend/tests/unit/document_gateway/test_pdf_rasterizer.py`
- Create: `backend/tests/unit/document_gateway/test_ocr.py`

**Interfaces:**
- Produces: `pdf_rasterizer.rasterize(raw_bytes: bytes, dpi: int = 300) -> list[PIL.Image.Image]`; `OCREngine` protocol (`run(image: Image) -> tuple[str, list[WordBox]]`); `TesseractOCREngine(lang: str = "deu")`; `ocr.run(image: Image, page_no: int, engine: OCREngine | None = None) -> RawPage`. Consumed by Task 6 (`DocumentPipeline`), which injects a fake `OCREngine` in its own tests and a real `TesseractOCREngine` by default at runtime.

- [ ] **Step 1: Write the failing rasterizer test**

Create `backend/tests/unit/document_gateway/test_pdf_rasterizer.py`:

```python
from PIL import Image

from app.document_gateway.extraction import pdf_rasterizer


def test_rasterize_delegates_to_pdf2image_with_the_given_dpi(monkeypatch):
    captured = {}

    def fake_convert_from_bytes(raw_bytes, dpi):
        captured["raw_bytes"] = raw_bytes
        captured["dpi"] = dpi
        return [Image.new("RGB", (10, 10)), Image.new("RGB", (10, 10))]

    monkeypatch.setattr(
        "app.document_gateway.extraction.pdf_rasterizer.convert_from_bytes",
        fake_convert_from_bytes,
    )

    images = pdf_rasterizer.rasterize(b"fake-pdf-bytes", dpi=200)

    assert captured == {"raw_bytes": b"fake-pdf-bytes", "dpi": 200}
    assert len(images) == 2
    assert all(isinstance(image, Image.Image) for image in images)


def test_rasterize_defaults_to_300_dpi(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.document_gateway.extraction.pdf_rasterizer.convert_from_bytes",
        lambda raw_bytes, dpi: captured.setdefault("dpi", dpi) or [],
    )
    pdf_rasterizer.rasterize(b"x")
    assert captured["dpi"] == 300
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_pdf_rasterizer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `pdf_rasterizer.py`**

```python
from __future__ import annotations

from PIL import Image
from pdf2image import convert_from_bytes

# 300 DPI: the commonly-recommended floor for Tesseract's own accuracy
# guidance on scanned documents; higher costs proportionally more OCR time for
# limited accuracy gain at this document type (typed/printed clinical text,
# not handwriting).
DEFAULT_DPI = 300


def rasterize(raw_bytes: bytes, dpi: int = DEFAULT_DPI) -> list[Image.Image]:
    return convert_from_bytes(raw_bytes, dpi=dpi)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/unit/document_gateway/test_pdf_rasterizer.py -v` → PASS.

- [ ] **Step 5: Write the failing OCR wrapper test**

Create `backend/tests/unit/document_gateway/test_ocr.py`:

```python
from PIL import Image

from app.document_gateway.extraction import ocr
from app.document_gateway.models import WordBox


class _FakeOCREngine:
    def __init__(self, text: str, word_boxes: list[WordBox]) -> None:
        self._text = text
        self._word_boxes = word_boxes
        self.received_images: list[Image.Image] = []

    def run(self, image):
        self.received_images.append(image)
        return self._text, self._word_boxes


def test_run_wraps_the_engines_output_in_a_raw_page():
    boxes = [WordBox(text="Hallo", x=0, y=0, width=10, height=10)]
    engine = _FakeOCREngine("Hallo Welt", boxes)
    image = Image.new("RGB", (5, 5))

    page = ocr.run(image, page_no=3, engine=engine)

    assert page.page_no == 3
    assert page.text == "Hallo Welt"
    assert page.word_boxes == boxes
    assert engine.received_images == [image]


def test_run_defaults_to_a_real_tesseract_engine(monkeypatch):
    """Confirms the default-engine wiring without invoking a real Tesseract
    binary -- TesseractOCREngine.run itself is the thin wrapper under test,
    not pytesseract's own behavior."""
    from app.document_gateway.extraction.ocr import TesseractOCREngine

    called = {}

    def fake_run(self, image):
        called["image"] = image
        return "erkannt", []

    monkeypatch.setattr(TesseractOCREngine, "run", fake_run)
    image = Image.new("RGB", (5, 5))

    page = ocr.run(image, page_no=1)

    assert page.text == "erkannt"
    assert called["image"] is image


def test_tesseract_engine_calls_pytesseract_with_the_configured_language(monkeypatch):
    from app.document_gateway.extraction.ocr import TesseractOCREngine

    captured = {}

    def fake_image_to_string(image, lang):
        captured["string_lang"] = lang
        return "Text\nZweite Zeile"

    def fake_image_to_data(image, lang, output_type):
        captured["data_lang"] = lang
        return {
            "text": ["Text", "Zweite", "Zeile"],
            "left": [1, 1, 40],
            "top": [1, 20, 20],
            "width": [30, 30, 30],
            "height": [10, 10, 10],
        }

    monkeypatch.setattr("pytesseract.image_to_string", fake_image_to_string)
    monkeypatch.setattr("pytesseract.image_to_data", fake_image_to_data)

    engine = TesseractOCREngine(lang="deu")
    text, word_boxes = engine.run(Image.new("RGB", (5, 5)))

    assert captured["string_lang"] == "deu"
    assert captured["data_lang"] == "deu"
    assert text == "Text\nZweite Zeile"
    assert [box.text for box in word_boxes] == ["Text", "Zweite", "Zeile"]
    assert word_boxes[0] == WordBox(text="Text", x=1, y=1, width=30, height=10)


def test_tesseract_engine_skips_blank_data_rows(monkeypatch):
    from app.document_gateway.extraction.ocr import TesseractOCREngine

    monkeypatch.setattr("pytesseract.image_to_string", lambda image, lang: "Text")
    monkeypatch.setattr(
        "pytesseract.image_to_data",
        lambda image, lang, output_type: {
            "text": ["Text", "", "   "],
            "left": [1, 0, 0],
            "top": [1, 0, 0],
            "width": [30, 0, 0],
            "height": [10, 0, 0],
        },
    )

    engine = TesseractOCREngine()
    _, word_boxes = engine.run(Image.new("RGB", (5, 5)))
    assert len(word_boxes) == 1
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_ocr.py -v` → FAIL, `ModuleNotFoundError`.

- [ ] **Step 7: Create `ocr.py`**

```python
from __future__ import annotations

from typing import Protocol

import pytesseract
from PIL import Image

from app.document_gateway.models import RawPage, WordBox

# tesseract-ocr-deu is baked into the runtime image (Dockerfile, Task 7) --
# matches this product's other German-medical-context language choices
# (de_core_news_lg).
DEFAULT_LANGUAGE = "deu"


class OCREngine(Protocol):
    def run(self, image: Image.Image) -> tuple[str, list[WordBox]]: ...


class TesseractOCREngine:
    """Thin wrapper over pytesseract (a subprocess call to the tesseract
    binary), entirely offline -- no network call, satisfying spec §2 decision 1.
    """

    def __init__(self, lang: str = DEFAULT_LANGUAGE) -> None:
        self._lang = lang

    def run(self, image: Image.Image) -> tuple[str, list[WordBox]]:
        text = pytesseract.image_to_string(image, lang=self._lang)
        data = pytesseract.image_to_data(
            image, lang=self._lang, output_type=pytesseract.Output.DICT
        )
        word_boxes = [
            WordBox(
                text=data["text"][i],
                x=float(data["left"][i]),
                y=float(data["top"][i]),
                width=float(data["width"][i]),
                height=float(data["height"][i]),
            )
            for i in range(len(data["text"]))
            if data["text"][i].strip()
        ]
        return text, word_boxes


def run(image: Image.Image, page_no: int, engine: OCREngine | None = None) -> RawPage:
    engine = engine or TesseractOCREngine()
    text, word_boxes = engine.run(image)
    return RawPage(page_no=page_no, text=text, word_boxes=word_boxes)
```

- [ ] **Step 8: Run to verify it passes, then full suite + linters**

Run:
```bash
cd backend
pytest tests/unit/document_gateway -v
ruff check .
lint-imports
pytest -v
```
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/document_gateway/extraction/pdf_rasterizer.py \
  backend/app/document_gateway/extraction/ocr.py \
  backend/tests/unit/document_gateway/test_pdf_rasterizer.py \
  backend/tests/unit/document_gateway/test_ocr.py
git commit -m "feat: add PDF rasterization and an interface-driven Tesseract OCR wrapper"
```

---

### Task 4: Structure reconstruction (heading/list/paragraph heuristics)

**Files:**
- Create: `backend/app/document_gateway/structure/__init__.py`
- Create: `backend/app/document_gateway/structure/reconstructor.py`
- Create: `backend/tests/unit/document_gateway/test_reconstructor.py`

**Interfaces:**
- Consumes: `RawPage`, `Block`, `WordBox` (Task 1).
- Produces: `reconstructor.reconstruct(pages: list[RawPage]) -> list[Block]`. Consumed by Task 6 (`DocumentPipeline`).
- **Resolved design ambiguity:** the spec describes heading detection via "font-size/line-height deltas (from `pdf_text_layer`) or box-height deltas (from OCR)". `pypdf.extract_text()` does not expose per-line font metadata without a much deeper content-stream parse, which is out of scope for this slice. This task instead applies **one uniform, purely text-based heuristic to every page regardless of source** (short single-line paragraph, no sentence-ending punctuation → heading; leading bullet/numeral → list item; everything else → paragraph) — satisfying spec §1's "heuristics only, no rich structure" scope without requiring PDF font metadata. `bbox` is populated from `RawPage.word_boxes` only when present (OCR-sourced pages); it is always `None` for `pdf_text_layer`-sourced pages, matching spec §4.3 exactly.

- [ ] **Step 1: Write the failing reconstructor test**

Create `backend/app/document_gateway/structure/__init__.py` (empty, added in Step 3).

Create `backend/tests/unit/document_gateway/test_reconstructor.py`:

```python
from app.document_gateway.models import RawPage, WordBox
from app.document_gateway.structure.reconstructor import reconstruct


def test_a_short_standalone_line_is_a_heading():
    pages = [RawPage(page_no=1, text="Befund\n\nDer Patient ist wohlauf und beschwerdefrei heute.")]
    blocks = reconstruct(pages)
    assert blocks[0].block_type == "heading"
    assert blocks[0].raw_text == "Befund"
    assert blocks[1].block_type == "paragraph"


def test_a_short_line_ending_in_punctuation_is_not_a_heading():
    pages = [RawPage(page_no=1, text="Alles gut.")]
    blocks = reconstruct(pages)
    assert blocks[0].block_type == "paragraph"


def test_bulleted_lines_become_separate_list_item_blocks():
    pages = [RawPage(page_no=1, text="- Medikament A\n- Medikament B\n* Medikament C")]
    blocks = reconstruct(pages)
    assert [b.block_type for b in blocks] == ["list_item", "list_item", "list_item"]
    assert [b.raw_text for b in blocks] == ["Medikament A", "Medikament B", "Medikament C"]


def test_numbered_list_items_are_recognized_and_stripped():
    pages = [RawPage(page_no=1, text="1. Erste Maßnahme\n2) Zweite Maßnahme")]
    blocks = reconstruct(pages)
    assert [b.block_type for b in blocks] == ["list_item", "list_item"]
    assert blocks[0].raw_text == "Erste Maßnahme"


def test_multi_line_paragraph_is_joined_with_spaces():
    pages = [RawPage(page_no=1, text="Der Patient berichtet über\nSchmerzen im linken Knie seit drei Tagen.")]
    blocks = reconstruct(pages)
    assert len(blocks) == 1
    assert blocks[0].block_type == "paragraph"
    assert blocks[0].raw_text == "Der Patient berichtet über Schmerzen im linken Knie seit drei Tagen."


def test_order_is_sequential_across_pages():
    pages = [
        RawPage(page_no=1, text="Erster Absatz mit genug Zeichen um kein Titel zu sein wirklich."),
        RawPage(page_no=2, text="Zweiter Absatz mit genug Zeichen um kein Titel zu sein wirklich."),
    ]
    blocks = reconstruct(pages)
    assert [b.order for b in blocks] == [0, 1]
    assert [b.page for b in blocks] == [1, 2]


def test_empty_page_text_produces_no_blocks():
    pages = [RawPage(page_no=1, text="")]
    assert reconstruct(pages) == []


def test_bbox_is_none_when_the_page_has_no_word_boxes():
    pages = [RawPage(page_no=1, text="Ein Absatz ohne Wortkoordinaten der lang genug ist.")]
    blocks = reconstruct(pages)
    assert blocks[0].bbox is None


def test_bbox_is_computed_from_word_boxes_when_present():
    word_boxes = [
        WordBox(text="Ein", x=0, y=0, width=20, height=10),
        WordBox(text="Wort", x=25, y=2, width=30, height=12),
    ]
    pages = [RawPage(page_no=1, text="Ein Wort", word_boxes=word_boxes)]
    blocks = reconstruct(pages)
    assert blocks[0].bbox == (0, 0, 55, 14)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_reconstructor.py -v` → FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Create `reconstructor.py`**

```python
from __future__ import annotations

import re

from app.document_gateway.models import Block, RawPage, WordBox

_LIST_PATTERN = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(?P<text>.+)$")
_SENTENCE_ENDINGS = (".", "!", "?", ",", ":", ";")
_HEADING_MAX_CHARS = 70


def reconstruct(pages: list[RawPage]) -> list[Block]:
    blocks: list[Block] = []
    order = 0
    for page in pages:
        word_cursor = 0
        for chunk in _split_into_chunks(page.text):
            lines = chunk.splitlines()

            if len(lines) == 1 and _LIST_PATTERN.match(lines[0]) is None and _looks_like_heading(lines[0]):
                block_type = "heading"
                raw_text = lines[0].strip()
                bbox, word_cursor = _consume_bbox(page.word_boxes, word_cursor, raw_text)
                blocks.append(Block(page=page.page_no, block_type=block_type, order=order, raw_text=raw_text, bbox=bbox))
                order += 1
                continue

            if all(_LIST_PATTERN.match(line) for line in lines if line.strip()):
                for line in lines:
                    if not line.strip():
                        continue
                    raw_text = _LIST_PATTERN.match(line)["text"].strip()
                    bbox, word_cursor = _consume_bbox(page.word_boxes, word_cursor, raw_text)
                    blocks.append(Block(page=page.page_no, block_type="list_item", order=order, raw_text=raw_text, bbox=bbox))
                    order += 1
                continue

            raw_text = " ".join(line.strip() for line in lines if line.strip())
            if not raw_text:
                continue
            bbox, word_cursor = _consume_bbox(page.word_boxes, word_cursor, raw_text)
            blocks.append(Block(page=page.page_no, block_type="paragraph", order=order, raw_text=raw_text, bbox=bbox))
            order += 1

    return blocks


def _split_into_chunks(text: str) -> list[str]:
    """Splits page text on blank lines into paragraph-sized chunks, dropping
    chunks that are entirely whitespace."""
    chunks = re.split(r"\n\s*\n", text)
    return [chunk for chunk in chunks if chunk.strip()]


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > _HEADING_MAX_CHARS:
        return False
    return not stripped.endswith(_SENTENCE_ENDINGS)


def _consume_bbox(
    word_boxes: list[WordBox] | None, cursor: int, block_text: str
) -> tuple[tuple[float, float, float, float] | None, int]:
    """Pairs a block's word count against the page's flat, sequentially-ordered
    word_boxes list (only present for OCR-sourced pages -- pytesseract's
    image_to_data returns boxes in the same reading order as
    image_to_string's text). Returns (bbox_or_none, new_cursor)."""
    if word_boxes is None:
        return None, cursor
    word_count = len(block_text.split())
    consumed = word_boxes[cursor : cursor + word_count]
    return _bbox_union(consumed), cursor + word_count


def _bbox_union(boxes: list[WordBox]) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    x0 = min(box.x for box in boxes)
    y0 = min(box.y for box in boxes)
    x1 = max(box.x + box.width for box in boxes)
    y1 = max(box.y + box.height for box in boxes)
    return (x0, y0, x1 - x0, y1 - y0)
```

- [ ] **Step 4: Run to verify it passes, then full suite + linters**

Run: `cd backend && pytest tests/unit/document_gateway -v && ruff check . && lint-imports && pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/document_gateway/structure/__init__.py \
  backend/app/document_gateway/structure/reconstructor.py \
  backend/tests/unit/document_gateway/test_reconstructor.py
git commit -m "feat: add heading/list/paragraph structure reconstruction heuristics"
```

---

### Task 5: Markdown rendering

**Files:**
- Create: `backend/app/document_gateway/rendering/__init__.py`
- Create: `backend/app/document_gateway/rendering/markdown_renderer.py`
- Create: `backend/tests/unit/document_gateway/test_markdown_renderer.py`

**Interfaces:**
- Consumes: `Block` (Task 1), each block already carrying `sanitized_text`/`blocked` (populated by Task 6's pipeline, not by the renderer itself).
- Produces: `markdown_renderer.render(blocks: list[Block]) -> str`; `markdown_renderer.BLOCKED_PLACEHOLDER` (the fixed, content-free placeholder text). Consumed by Task 6.

- [ ] **Step 1: Write the failing renderer test**

Create `backend/tests/unit/document_gateway/test_markdown_renderer.py`:

```python
from app.document_gateway.models import Block
from app.document_gateway.rendering.markdown_renderer import BLOCKED_PLACEHOLDER, render


def _sanitized(page, block_type, order, sanitized_text, blocked=False):
    block = Block(page=page, block_type=block_type, order=order, raw_text="unused")
    block.sanitized_text = sanitized_text
    block.blocked = blocked
    return block


def test_heading_renders_as_an_h2():
    md = render([_sanitized(1, "heading", 0, "Befund")])
    assert md.strip() == "## Befund"


def test_paragraph_renders_as_plain_text():
    md = render([_sanitized(1, "paragraph", 0, "Der Patient ist wohlauf.")])
    assert md.strip() == "Der Patient ist wohlauf."


def test_list_items_render_as_a_bulleted_list():
    md = render([
        _sanitized(1, "list_item", 0, "Medikament A"),
        _sanitized(1, "list_item", 1, "Medikament B"),
    ])
    lines = [line for line in md.splitlines() if line.strip()]
    assert lines == ["- Medikament A", "- Medikament B"]


def test_caption_renders_as_italic_text():
    md = render([_sanitized(1, "caption", 0, "Abbildung 1: Röntgenbild")])
    assert md.strip() == "*Abbildung 1: Röntgenbild*"


def test_blocked_block_renders_the_fixed_placeholder_not_its_sanitized_text():
    block = _sanitized(1, "paragraph", 0, sanitized_text="should never be used", blocked=True)
    md = render([block])
    assert BLOCKED_PLACEHOLDER in md
    assert "should never be used" not in md


def test_blocks_are_rendered_in_order():
    md = render([
        _sanitized(1, "heading", 0, "Titel"),
        _sanitized(1, "paragraph", 1, "Text."),
    ])
    assert md.index("Titel") < md.index("Text.")


def test_empty_block_list_renders_an_empty_string():
    assert render([]) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_markdown_renderer.py -v` → FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Create `markdown_renderer.py`**

```python
from __future__ import annotations

from app.document_gateway.models import Block

# Fixed, content-free -- never includes any part of the block's original or
# attempted-sanitized text (spec §5.1 step 4). German, matching this product's
# other user-facing strings.
BLOCKED_PLACEHOLDER = "> [Block konnte nicht sicher anonymisiert werden]"


def render(blocks: list[Block]) -> str:
    lines: list[str] = []
    for block in sorted(blocks, key=lambda b: b.order):
        text = BLOCKED_PLACEHOLDER if block.blocked else (block.sanitized_text or "")
        if block.block_type == "heading":
            lines.append(f"## {text}")
        elif block.block_type == "list_item":
            lines.append(f"- {text}")
        elif block.block_type == "caption":
            lines.append(f"*{text}*")
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run to verify it passes, then full suite + linters**

Run: `cd backend && pytest tests/unit/document_gateway -v && ruff check . && lint-imports && pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/document_gateway/rendering/__init__.py \
  backend/app/document_gateway/rendering/markdown_renderer.py \
  backend/tests/unit/document_gateway/test_markdown_renderer.py
git commit -m "feat: add markdown rendering for reconstructed document blocks"
```

---

### Task 6: `DocumentPipeline` orchestration — per-block anonymization, fail-closed degrade

**Files:**
- Create: `backend/app/document_gateway/pipeline.py`
- Create: `backend/tests/unit/document_gateway/test_pipeline.py`

**Interfaces:**
- Consumes: `classification.classify`, `extraction.pdf_text_layer`/`pdf_rasterizer`/`ocr`, `structure.reconstructor.reconstruct`, `rendering.markdown_renderer.render` (Tasks 2–5); `privacy_gateway.pipeline.Pipeline`/`get_pipeline` and its `LowConfidenceSpanError`/`HighRiskMessageError`/`ResidualPIIError` (existing, generalized).
- Produces: `DocumentPipeline(privacy_pipeline: Pipeline, ocr_engine: OCREngine | None = None)` with `.process(tenant_id, document_id, raw_bytes, content_type) -> ProcessResult`; `get_document_pipeline() -> DocumentPipeline` (`lru_cache`d singleton). Consumed by Task 9 (background processing).

- [ ] **Step 1: Write the failing pipeline orchestration test**

Create `backend/tests/unit/document_gateway/test_pipeline.py`:

```python
import uuid

import pytest
from PIL import Image

from app.document_gateway.models import RawPage, WordBox
from app.document_gateway.pipeline import DocumentPipeline
from app.privacy_gateway.output_guard.guard import ResidualPIIError
from app.privacy_gateway.risk_scoring.scorer import HighRiskMessageError, LowConfidenceSpanError


class _FakePrivacyPipeline:
    """Stands in for privacy_gateway.pipeline.Pipeline -- records every
    (scope_type, scope_id, text) it was called with, and can be told to raise
    for a specific input text so the fail-closed-per-block path is testable
    without a real detector stack."""

    def __init__(self, raise_for_text: str | None = None, exc: Exception | None = None):
        self.calls: list[tuple[str, uuid.UUID, str]] = []
        self._raise_for_text = raise_for_text
        self._exc = exc or LowConfidenceSpanError("ambiguous span")

    def sanitize(self, tenant_id, scope_type, scope_id, text):
        self.calls.append((scope_type, scope_id, text))
        if text == self._raise_for_text:
            raise self._exc
        return f"SANITIZED[{text}]"


class _FakeOCREngine:
    def run(self, image):
        return "Erkannter Text vom Bild.", [WordBox(text="Erkannter", x=0, y=0, width=10, height=10)]


def test_image_upload_runs_a_single_ocr_pass_and_sanitizes_each_block(monkeypatch):
    monkeypatch.setattr(
        "app.document_gateway.pipeline.classification.classify",
        lambda content_type, raw_bytes: ("image", None),
    )
    privacy_pipeline = _FakePrivacyPipeline()
    pipeline = DocumentPipeline(privacy_pipeline=privacy_pipeline, ocr_engine=_FakeOCREngine())
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()

    result = pipeline.process(tenant_id, document_id, b"fake-image-bytes", "image/png")

    assert result.document_type == "image"
    assert result.page_count == 1
    assert "SANITIZED[Erkannter Text vom Bild.]" in result.sanitized_markdown
    assert privacy_pipeline.calls == [("document", document_id, "Erkannter Text vom Bild.")]


def test_pdf_digital_reuses_the_already_extracted_pages_no_ocr(monkeypatch):
    pages = [RawPage(page_no=1, text="Ein digitaler Absatz mit genug Zeichen für den Testfall hier.")]
    monkeypatch.setattr(
        "app.document_gateway.pipeline.classification.classify",
        lambda content_type, raw_bytes: ("pdf_digital", pages),
    )
    ocr_called = {"count": 0}
    monkeypatch.setattr(
        "app.document_gateway.pipeline.ocr.run", lambda *a, **kw: ocr_called.__setitem__("count", ocr_called["count"] + 1)
    )
    privacy_pipeline = _FakePrivacyPipeline()
    pipeline = DocumentPipeline(privacy_pipeline=privacy_pipeline)

    result = pipeline.process(uuid.uuid4(), uuid.uuid4(), b"fake-pdf-bytes", "application/pdf")

    assert result.document_type == "pdf_digital"
    assert result.page_count == 1
    assert ocr_called["count"] == 0


def test_pdf_scanned_rasterizes_and_ocrs_each_page(monkeypatch):
    monkeypatch.setattr(
        "app.document_gateway.pipeline.classification.classify",
        lambda content_type, raw_bytes: ("pdf_scanned", None),
    )
    monkeypatch.setattr(
        "app.document_gateway.pipeline.pdf_rasterizer.rasterize",
        lambda raw_bytes: [Image.new("RGB", (5, 5)), Image.new("RGB", (5, 5))],
    )
    privacy_pipeline = _FakePrivacyPipeline()
    pipeline = DocumentPipeline(privacy_pipeline=privacy_pipeline, ocr_engine=_FakeOCREngine())

    result = pipeline.process(uuid.uuid4(), uuid.uuid4(), b"fake-pdf-bytes", "application/pdf")

    assert result.document_type == "pdf_scanned"
    assert result.page_count == 2


@pytest.mark.parametrize("exc", [
    LowConfidenceSpanError("ambiguous"),
    HighRiskMessageError("too risky"),
    ResidualPIIError("residual pii"),
])
def test_a_single_blocked_block_degrades_to_ready_not_failed(monkeypatch, exc):
    pages = [RawPage(page_no=1, text="Absatz eins ist unproblematisch für den Test hier wirklich.\n\nAbsatz zwei ist mehrdeutig.")]
    monkeypatch.setattr(
        "app.document_gateway.pipeline.classification.classify",
        lambda content_type, raw_bytes: ("pdf_digital", pages),
    )
    privacy_pipeline = _FakePrivacyPipeline(raise_for_text="Absatz zwei ist mehrdeutig.", exc=exc)
    pipeline = DocumentPipeline(privacy_pipeline=privacy_pipeline)

    result = pipeline.process(uuid.uuid4(), uuid.uuid4(), b"x", "application/pdf")

    # No exception propagated -- the document still "completes" at this layer;
    # DocumentPipeline.process never raises for a block-level failure. Task 9's
    # background wiring is what would call this and persist status="ready".
    assert "mehrdeutig" not in result.sanitized_markdown
    blocked_entries = [b for b in result.structured_blocks if b["blocked"]]
    assert len(blocked_entries) == 1
    assert blocked_entries[0]["sanitized_text"] is None or "mehrdeutig" not in (blocked_entries[0]["sanitized_text"] or "")


def test_structured_blocks_never_contain_raw_text_only_sanitized_or_null(monkeypatch):
    """structured_blocks is a persisted column -- it must never carry raw_text,
    only the already-sanitized (or, when blocked, absent) text."""
    pages = [RawPage(page_no=1, text="Herr Müller wurde am linken Knie operiert erfolgreich heute.")]
    monkeypatch.setattr(
        "app.document_gateway.pipeline.classification.classify",
        lambda content_type, raw_bytes: ("pdf_digital", pages),
    )
    pipeline = DocumentPipeline(privacy_pipeline=_FakePrivacyPipeline())

    result = pipeline.process(uuid.uuid4(), uuid.uuid4(), b"x", "application/pdf")

    for entry in result.structured_blocks:
        assert "raw_text" not in entry
        assert "Herr Müller" not in (entry.get("sanitized_text") or "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_pipeline.py -v` → FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Create `pipeline.py`**

```python
from __future__ import annotations

import io
import logging
import uuid
from functools import lru_cache

from PIL import Image

from app.document_gateway import classification
from app.document_gateway.extraction import ocr, pdf_rasterizer
from app.document_gateway.extraction.ocr import OCREngine
from app.document_gateway.models import Block, ProcessResult
from app.document_gateway.rendering.markdown_renderer import render
from app.document_gateway.structure.reconstructor import reconstruct
from app.privacy_gateway.output_guard.guard import ResidualPIIError
from app.privacy_gateway.pipeline import Pipeline, get_pipeline
from app.privacy_gateway.risk_scoring.scorer import HighRiskMessageError, LowConfidenceSpanError

logger = logging.getLogger(__name__)

_BLOCK_LEVEL_FAILURES = (LowConfidenceSpanError, HighRiskMessageError, ResidualPIIError)


class DocumentPipeline:
    """Orchestrates classify -> extract -> structure -> per-block anonymize ->
    render. Never raises for a per-block anonymization failure (spec §5.1 step
    4) -- only a stage-level failure (corrupt file, OCR crash) should raise,
    and Task 9's background wiring is what catches that and marks the document
    status="failed".
    """

    def __init__(self, privacy_pipeline: Pipeline, ocr_engine: OCREngine | None = None) -> None:
        self._privacy_pipeline = privacy_pipeline
        self._ocr_engine = ocr_engine

    def process(
        self, tenant_id: uuid.UUID, document_id: uuid.UUID, raw_bytes: bytes, content_type: str
    ) -> ProcessResult:
        document_type, pages = classification.classify(content_type, raw_bytes)

        if document_type == "pdf_digital":
            assert pages is not None  # classify() always returns pages for pdf_digital
        elif document_type == "pdf_scanned":
            images = pdf_rasterizer.rasterize(raw_bytes)
            pages = [
                ocr.run(image, page_no=index, engine=self._ocr_engine)
                for index, image in enumerate(images, start=1)
            ]
        else:  # "image"
            image = Image.open(io.BytesIO(raw_bytes))
            pages = [ocr.run(image, page_no=1, engine=self._ocr_engine)]

        blocks = reconstruct(pages)

        blocked_count = 0
        for block in blocks:
            try:
                block.sanitized_text = self._privacy_pipeline.sanitize(
                    tenant_id, "document", document_id, block.raw_text
                )
            except _BLOCK_LEVEL_FAILURES as exc:
                # Never log block.raw_text -- only category/counts (same
                # discipline as Pipeline.sanitize's own logging).
                logger.warning(
                    "document_gateway.block_blocked tenant_id=%s document_id=%s "
                    "page=%s order=%s reason=%s",
                    tenant_id, document_id, block.page, block.order, type(exc).__name__,
                )
                block.blocked = True
                blocked_count += 1

        if blocked_count:
            logger.info(
                "document_gateway.process tenant_id=%s document_id=%s blocks=%d blocked=%d",
                tenant_id, document_id, len(blocks), blocked_count,
            )

        markdown = render(blocks)
        return ProcessResult(
            document_type=document_type,
            page_count=len(pages),
            structured_blocks=[_to_structured_block(block) for block in blocks],
            sanitized_markdown=markdown,
        )


def _to_structured_block(block: Block) -> dict:
    return {
        "page": block.page,
        "block_type": block.block_type,
        "order": block.order,
        "sanitized_text": None if block.blocked else block.sanitized_text,
        "source_bbox": list(block.bbox) if block.bbox is not None else None,
        "blocked": block.blocked,
    }


@lru_cache(maxsize=1)
def get_document_pipeline() -> DocumentPipeline:
    return DocumentPipeline(privacy_pipeline=get_pipeline())
```

- [ ] **Step 4: Run to verify it passes, then full suite + linters**

Run: `cd backend && pytest tests/unit/document_gateway -v && ruff check . && lint-imports && pytest -v`
Expected: all PASS; the new `document_gateway` import-linter contract still KEPT (`pipeline.py` imports `app.privacy_gateway`, never `app.api`/`app.llm_gateway`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/document_gateway/pipeline.py backend/tests/unit/document_gateway/test_pipeline.py
git commit -m "feat: orchestrate DocumentPipeline with per-block fail-closed anonymization"
```

---

### Task 7: Settings, raw file storage, Dockerfile/Compose (Tesseract, poppler, `documents_data` volume)

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/document_gateway/storage.py`
- Create: `backend/tests/unit/document_gateway/test_storage.py`
- Modify: `backend/tests/unit/test_config.py`
- Modify: `backend/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.max_document_size_bytes: int = 25 * 1024 * 1024`, `Settings.document_processing_timeout_seconds: int = 300`, `Settings.documents_storage_root: str = "/data/documents"`; `storage.save_raw_file(tenant_id, document_id, content_type, data: bytes) -> str`, `storage.read_raw_file(path: str) -> bytes`. Consumed by Task 8 (API endpoints).

- [ ] **Step 1: Write the failing Settings test**

Append to `backend/tests/unit/test_config.py`:

```python
def test_settings_document_gateway_defaults(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.setenv("APP_OPS_PASSWORD", "ops-secret")
    for var in ["MAX_DOCUMENT_SIZE_BYTES", "DOCUMENT_PROCESSING_TIMEOUT_SECONDS", "DOCUMENTS_STORAGE_ROOT"]:
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.max_document_size_bytes == 25 * 1024 * 1024
    assert settings.document_processing_timeout_seconds == 300
    assert settings.documents_storage_root == "/data/documents"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/unit/test_config.py -v` → FAIL, `AttributeError`.

- [ ] **Step 3: Add the settings**

Edit `backend/app/config.py` — add after `output_guard_enabled`:

```python
    # Documents App Phase 1 (spec §6.1, §8). 25 MB default: generous for a
    # multi-page scanned PDF at 300 DPI while still bounding worst-case
    # rasterization/OCR memory and time per upload.
    max_document_size_bytes: int = 25 * 1024 * 1024
    # Guards against a pathological large PDF or a hung Tesseract subprocess
    # holding a document-processing executor slot indefinitely (spec §8).
    document_processing_timeout_seconds: int = 300
    # Filesystem root for raw uploaded originals (spec §4.4) -- mounted from
    # the documents_data volume in docker-compose.yml, never in Postgres.
    documents_storage_root: str = "/data/documents"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/unit/test_config.py -v` → PASS.

- [ ] **Step 5: Write the failing storage test**

Create `backend/tests/unit/document_gateway/test_storage.py`:

```python
import uuid

import pytest

from app.document_gateway import storage


@pytest.fixture(autouse=True)
def _storage_root(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.document_gateway.storage.get_settings",
        lambda: type("S", (), {"documents_storage_root": str(tmp_path)})(),
    )
    return tmp_path


def test_save_raw_file_writes_under_tenant_and_document_id(tmp_path):
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    path = storage.save_raw_file(tenant_id, document_id, "application/pdf", b"%PDF-fake-bytes")

    assert path == str(tmp_path / str(tenant_id) / str(document_id) / "original.pdf")
    assert (tmp_path / str(tenant_id) / str(document_id) / "original.pdf").read_bytes() == b"%PDF-fake-bytes"


@pytest.mark.parametrize(
    "content_type,expected_ext",
    [
        ("application/pdf", ".pdf"),
        ("image/jpeg", ".jpg"),
        ("image/png", ".png"),
        ("image/tiff", ".tiff"),
    ],
)
def test_save_raw_file_maps_content_type_to_extension(content_type, expected_ext):
    path = storage.save_raw_file(uuid.uuid4(), uuid.uuid4(), content_type, b"x")
    assert path.endswith(f"original{expected_ext}")


def test_read_raw_file_round_trips():
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    path = storage.save_raw_file(tenant_id, document_id, "image/png", b"binary-png-data")
    assert storage.read_raw_file(path) == b"binary-png-data"
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_storage.py -v` → FAIL, `ModuleNotFoundError`.

- [ ] **Step 7: Create `storage.py`**

```python
from __future__ import annotations

import pathlib
import uuid

from app.config import get_settings

_EXTENSION_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
}


def save_raw_file(
    tenant_id: uuid.UUID, document_id: uuid.UUID, content_type: str, data: bytes
) -> str:
    """Writes the raw upload to /data/documents/{tenant_id}/{document_id}/original.<ext>
    (spec §4.4) -- filesystem, not Postgres, to avoid bloating the DB with
    binary blobs."""
    extension = _EXTENSION_BY_CONTENT_TYPE.get(content_type, "")
    directory = pathlib.Path(get_settings().documents_storage_root) / str(tenant_id) / str(document_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"original{extension}"
    path.write_bytes(data)
    return str(path)


def read_raw_file(path: str) -> bytes:
    return pathlib.Path(path).read_bytes()
```

- [ ] **Step 8: Run to verify it passes, then full suite + linters**

Run: `cd backend && pytest tests/unit/document_gateway -v tests/unit/test_config.py -v && ruff check . && lint-imports && pytest -v`
Expected: all PASS.

- [ ] **Step 9: Update the Dockerfile**

Edit `backend/Dockerfile` — insert before `RUN pip install --no-cache-dir -e .` (line 11):

```dockerfile
# Documents App Phase 1 (spec §5.2): OCR (Tesseract, German language pack) and
# PDF-to-image rasterization (poppler) as system packages baked into the image
# at build time -- no runtime network access needed, consistent with the
# spaCy/ORDO data baked in below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-deu poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -e .
```

- [ ] **Step 10: Update `docker-compose.yml`**

Add `documents_data:` to the top-level `volumes:` block:

```yaml
volumes:
  postgres_data:
  ollama_data:
  traefik_letsencrypt:
  documents_data:
```

Add a `volumes:` key to the `backend` service (it currently has none — only `secrets:`) and the new env vars:

```yaml
  backend:
    build: ./backend
    environment:
      # ... existing keys unchanged ...
      MAX_DOCUMENT_SIZE_BYTES: ${MAX_DOCUMENT_SIZE_BYTES:-26214400}
      DOCUMENT_PROCESSING_TIMEOUT_SECONDS: ${DOCUMENT_PROCESSING_TIMEOUT_SECONDS:-300}
      DOCUMENTS_STORAGE_ROOT: /data/documents
    volumes:
      - documents_data:/data/documents
    secrets:
      - master_key
```

- [ ] **Step 11: Document the new env vars**

Append to `.env.example`:

```
# --- Documents App (Phase 1) ---
MAX_DOCUMENT_SIZE_BYTES=26214400
DOCUMENT_PROCESSING_TIMEOUT_SECONDS=300
```

- [ ] **Step 12: Verify the Dockerfile builds (optional but recommended before merging)**

Run: `docker build -t chatgpt-proxy-backend-doctest ./backend`
Expected: builds successfully; `tesseract --version` and `pdftoppm -v` are available inside the image (`docker run --rm chatgpt-proxy-backend-doctest tesseract --list-langs` should list `deu`).

- [ ] **Step 13: Commit**

```bash
git add backend/app/config.py backend/app/document_gateway/storage.py \
  backend/tests/unit/document_gateway/test_storage.py backend/tests/unit/test_config.py \
  backend/Dockerfile docker-compose.yml .env.example
git commit -m "feat: add document-processing settings, raw file storage, and Tesseract/poppler image deps"
```

---

### Task 8: API endpoints — upload, list, detail, original, delete

**Files:**
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/privacy_gateway/token_vault/vault.py`
- Create: `backend/app/api/documents.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/integration/test_documents_api.py`
- Create: `backend/tests/integration/test_documents_entitlement.py`
- Modify: `backend/tests/privacy_invariants/test_token_vault.py`

**Interfaces:**
- Consumes: `DocumentRepository` (existing), `require_app_entitlement` (existing), `storage.save_raw_file`/`read_raw_file` (Task 7), `Settings.max_document_size_bytes` (Task 7).
- Produces: `DocumentSummary`/`DocumentDetail` Pydantic schemas; `TokenVault.expire_all_for_scope(tenant_id, scope_type, scope_id) -> int`; router `backend/app/api/documents.py` (`POST /api/documents`, `GET /api/documents`, `GET /api/documents/{id}`, `GET /api/documents/{id}/original`, `DELETE /api/documents/{id}`), registered in `main.py`. **No background processing is wired yet** — uploads stay `status="queued"` until Task 9. Consumed by Task 9 (adds `background_tasks.add_task(...)` to the same `POST` handler) and Task 10 (chat attach reads `DocumentRepository.get`/`sanitized_markdown`).

- [ ] **Step 1: Add the `TokenVault.expire_all_for_scope` bulk method (needed by `DELETE`)**

The existing per-token `expire_mapping` requires already knowing every token string; `DELETE /api/documents/{id}` needs to expire *every* mapping for a document scope in one call. Write the failing test first — append to `backend/tests/privacy_invariants/test_token_vault.py`:

```python
def test_expire_all_for_scope_expires_every_mapping_for_that_document(key_provider):
    tenant_id, _ = _create_tenant_and_conversation(key_provider)
    document_id = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)
    token_a = vault.create_mapping(tenant_id, "document", document_id, "PATIENT", "Anna Weber")
    token_b = vault.create_mapping(tenant_id, "document", document_id, "HOSPITAL", "Klinikum Nord")

    expired_count = vault.expire_all_for_scope(tenant_id, "document", document_id)

    assert expired_count == 2
    assert vault.resolve_token(tenant_id, "document", document_id, token_a) is None
    assert vault.resolve_token(tenant_id, "document", document_id, token_b) is None


def test_expire_all_for_scope_does_not_touch_a_different_documents_mappings(key_provider):
    tenant_id, _ = _create_tenant_and_conversation(key_provider)
    document_a = _create_document(key_provider, tenant_id)
    document_b = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)
    token_a = vault.create_mapping(tenant_id, "document", document_a, "PATIENT", "Anna Weber")
    token_b = vault.create_mapping(tenant_id, "document", document_b, "PATIENT", "Otto Bauer")

    vault.expire_all_for_scope(tenant_id, "document", document_a)

    assert vault.resolve_token(tenant_id, "document", document_a, token_a) is None
    assert vault.resolve_token(tenant_id, "document", document_b, token_b) == "Otto Bauer"
```

Run: `cd backend && pytest tests/privacy_invariants/test_token_vault.py -k expire_all_for_scope -v` → FAIL, `AttributeError`.

Add to `backend/app/privacy_gateway/token_vault/vault.py`'s `TokenVault` class, after `expire_mapping`:

```python
    def expire_all_for_scope(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID
    ) -> int:
        """Bulk counterpart to expire_mapping -- used when a whole scope (e.g. a
        deleted document) goes away and every mapping under it must be expired
        without the caller having to already know each token string."""
        with tenant_scoped_session(tenant_id) as session:
            scope_column = (
                TokenMapping.conversation_id if scope_type == "conversation" else TokenMapping.document_id
            )
            stmt = (
                sa.update(TokenMapping)
                .where(
                    TokenMapping.tenant_id == tenant_id,
                    TokenMapping.scope_type == scope_type,
                    scope_column == scope_id,
                    TokenMapping.deleted_at.is_(None),
                )
                .values(deleted_at=datetime.now(timezone.utc))
            )
            result = session.execute(stmt)
            return result.rowcount
```

Run: `cd backend && pytest tests/privacy_invariants/test_token_vault.py -v` → PASS (all, including the two new tests).

- [ ] **Step 2: Add the API schemas**

Edit `backend/app/api/schemas.py` — add after `SendMessageIn`:

```python
class DocumentSummary(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    document_type: str | None
    status: str
    error_message: str | None
    page_count: int | None
    byte_size: int
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    sanitized_markdown: str | None
```

Change `SendMessageIn` (this schema change is used by both this task's `sanitize` behavior test setup and Task 10 — adding the field here now keeps `Document*` and `SendMessageIn` changes in one schema-file diff):

```python
class SendMessageIn(BaseModel):
    content: str
    document_ids: list[uuid.UUID] | None = None
```

- [ ] **Step 3: Write the failing integration tests for the four read/write endpoints**

Create `backend/tests/integration/test_documents_api.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import DEFAULT_PERMISSIONS
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.main import app
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from tests.conftest import grant_app_entitlement


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.document_gateway.storage.get_settings",
        lambda: type("S", (), {"documents_storage_root": str(tmp_path)})(),
    )
    key_provider = FileSecretKeyProvider(_master_key_path())
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    grant_app_entitlement(tenant_id, app_key="documents")
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        user_id = user.id
    return tenant_id, user_id


def _master_key_path():
    import os

    return os.environ["MASTER_KEY_PATH"]


@pytest.fixture
def authenticated(scope):
    tenant_id, user_id = scope
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor", branch_id=None,
        email="doc@example.com", permissions=DEFAULT_PERMISSIONS["doctor"],
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_upload_creates_a_queued_document_and_returns_202(scope, authenticated):
    client = TestClient(app)
    response = client.post(
        "/api/documents", files={"file": ("scan.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["filename"] == "scan.pdf"
    assert body["document_type"] is None


def test_unsupported_content_type_is_rejected(scope, authenticated):
    client = TestClient(app)
    response = client.post(
        "/api/documents", files={"file": ("virus.exe", b"MZ", "application/x-msdownload")}
    )
    assert response.status_code == 422


def test_oversized_upload_is_rejected(scope, authenticated, monkeypatch):
    from app.config import get_settings as real_get_settings
    settings = real_get_settings()
    monkeypatch.setattr(settings, "max_document_size_bytes", 10)
    monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)

    client = TestClient(app)
    response = client.post(
        "/api/documents", files={"file": ("scan.pdf", b"%PDF-way-too-large-for-the-limit", "application/pdf")}
    )
    assert response.status_code == 422


def test_list_returns_only_the_callers_own_documents_newest_first(scope, authenticated):
    tenant_id, user_id = scope
    client = TestClient(app)
    client.post("/api/documents", files={"file": ("a.pdf", b"%PDF-a", "application/pdf")})
    client.post("/api/documents", files={"file": ("b.pdf", b"%PDF-b", "application/pdf")})

    response = client.get("/api/documents")

    assert response.status_code == 200
    filenames = [d["filename"] for d in response.json()]
    assert filenames == ["b.pdf", "a.pdf"]


def test_get_detail_returns_null_markdown_while_queued(scope, authenticated):
    client = TestClient(app)
    created = client.post("/api/documents", files={"file": ("a.pdf", b"%PDF-a", "application/pdf")}).json()

    response = client.get(f"/api/documents/{created['id']}")

    assert response.status_code == 200
    assert response.json()["sanitized_markdown"] is None


def test_get_returns_404_for_another_users_document(scope, authenticated):
    tenant_id, _ = scope
    client = TestClient(app)
    created = client.post("/api/documents", files={"file": ("a.pdf", b"%PDF-a", "application/pdf")}).json()

    with tenant_scoped_session(tenant_id) as session:
        other_user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="other@example.com", role="doctor"
        )
        other_user_id = other_user.id

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=other_user_id, role="doctor", branch_id=None,
        email="other@example.com", permissions=DEFAULT_PERMISSIONS["doctor"],
    )
    response = client.get(f"/api/documents/{created['id']}")
    assert response.status_code == 404


def test_get_original_returns_raw_bytes_with_the_stored_content_type(scope, authenticated):
    client = TestClient(app)
    created = client.post(
        "/api/documents", files={"file": ("a.pdf", b"%PDF-raw-bytes", "application/pdf")}
    ).json()

    response = client.get(f"/api/documents/{created['id']}/original")

    assert response.status_code == 200
    assert response.content == b"%PDF-raw-bytes"
    assert response.headers["content-type"] == "application/pdf"


def test_get_original_is_404_for_another_users_document(scope, authenticated):
    tenant_id, _ = scope
    client = TestClient(app)
    created = client.post("/api/documents", files={"file": ("a.pdf", b"%PDF-a", "application/pdf")}).json()

    with tenant_scoped_session(tenant_id) as session:
        other_user_id = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-3", email="third@example.com", role="doctor"
        ).id
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=other_user_id, role="doctor", branch_id=None,
        email="third@example.com", permissions=DEFAULT_PERMISSIONS["doctor"],
    )
    assert client.get(f"/api/documents/{created['id']}/original").status_code == 404


def test_delete_soft_deletes_and_expires_any_token_mappings(scope, authenticated):
    tenant_id, user_id = scope
    client = TestClient(app)
    created = client.post("/api/documents", files={"file": ("a.pdf", b"%PDF-a", "application/pdf")}).json()

    # Simulate a token minted during processing (Task 9 wires this for real).
    from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
    from app.privacy_gateway.token_vault.vault import TokenVault
    vault = TokenVault(FileSecretKeyProvider(_master_key_path()))
    token = vault.create_mapping(tenant_id, "document", uuid.UUID(created["id"]), "PATIENT", "Anna Weber")

    response = client.delete(f"/api/documents/{created['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/documents/{created['id']}").status_code == 404
    assert vault.resolve_token(tenant_id, "document", uuid.UUID(created["id"]), token) is None
```

- [ ] **Step 4: Run to verify these fail**

Run: `cd backend && pytest tests/integration/test_documents_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.documents'`.

- [ ] **Step 5: Create the router**

Create `backend/app/api/documents.py`:

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.schemas import DocumentDetail, DocumentSummary
from app.auth.dependencies import get_current_user, get_db_session, require_app_entitlement
from app.auth.tenant_resolver import AuthenticatedUser
from app.config import get_settings
from app.db.repositories.document_repository import DocumentRepository
from app.document_gateway import storage
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

router = APIRouter(
    prefix="/api/documents",
    tags=["documents"],
    dependencies=[Depends(require_app_entitlement(app_key="documents"))],
)

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/tiff"}


def _to_summary(document) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        filename=document.filename,
        content_type=document.content_type,
        document_type=document.document_type,
        status=document.status,
        error_message=document.error_message,
        page_count=document.page_count,
        byte_size=document.byte_size,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post("", response_model=DocumentSummary, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> DocumentSummary:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail="unsupported file type")

    data = await file.read()
    settings = get_settings()
    if len(data) > settings.max_document_size_bytes:
        raise HTTPException(status_code=422, detail="file too large")

    document = DocumentRepository(session).create(
        user.tenant_id,
        user.user_id,
        filename=file.filename or "upload",
        content_type=file.content_type,
        byte_size=len(data),
        raw_storage_path="",  # set below, before commit
    )
    raw_storage_path = storage.save_raw_file(user.tenant_id, document.id, file.content_type, data)
    document.raw_storage_path = raw_storage_path
    session.commit()

    return _to_summary(document)


@router.get("", response_model=list[DocumentSummary])
def list_documents(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[DocumentSummary]:
    documents = DocumentRepository(session).list_for_user(user.tenant_id, user.user_id)
    return [_to_summary(d) for d in documents]


def _get_owned_document(session: Session, user: AuthenticatedUser, document_id: uuid.UUID):
    document = DocumentRepository(session).get(user.tenant_id, document_id)
    if document is None or document.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="document not found")
    return document


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> DocumentDetail:
    document = _get_owned_document(session, user, document_id)
    return DocumentDetail(**_to_summary(document).model_dump(), sanitized_markdown=document.sanitized_markdown)


@router.get("/{document_id}/original")
def get_original_document(
    document_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    document = _get_owned_document(session, user, document_id)
    raw_bytes = storage.read_raw_file(document.raw_storage_path)
    return Response(content=raw_bytes, media_type=document.content_type)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    _get_owned_document(session, user, document_id)
    DocumentRepository(session).soft_delete(user.tenant_id, document_id)
    session.commit()

    vault = TokenVault(FileSecretKeyProvider(get_settings().master_key_path))
    vault.expire_all_for_scope(user.tenant_id, "document", document_id)
    return Response(status_code=204)
```

- [ ] **Step 6: Register the router**

Edit `backend/app/main.py`:

```python
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
...
app.include_router(conversations_router)
app.include_router(documents_router)
app.include_router(chat_router)
```

- [ ] **Step 7: Run to verify the tests pass**

Run: `cd backend && pytest tests/integration/test_documents_api.py -v`
Expected: PASS (all).

- [ ] **Step 8: Write the entitlement-gating test**

Create `backend/tests/integration/test_documents_entitlement.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import DEFAULT_PERMISSIONS
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.main import app
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from tests.conftest import grant_app_entitlement


@pytest.fixture
def scope_without_documents_entitlement():
    """Entitled to 'anonymization' but deliberately NOT to 'documents' -- the
    scenario spec §9 calls out explicitly."""
    import os
    key_provider = FileSecretKeyProvider(os.environ["MASTER_KEY_PATH"])
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    grant_app_entitlement(tenant_id, app_key="anonymization")
    with tenant_scoped_session(tenant_id) as session:
        user_id = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        ).id

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor", branch_id=None,
        email="doc@example.com", permissions=DEFAULT_PERMISSIONS["doctor"],
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_upload_is_forbidden_without_the_documents_entitlement(scope_without_documents_entitlement):
    client = TestClient(app)
    response = client.post("/api/documents", files={"file": ("a.pdf", b"%PDF-a", "application/pdf")})
    assert response.status_code == 403


def test_list_is_forbidden_without_the_documents_entitlement(scope_without_documents_entitlement):
    client = TestClient(app)
    assert client.get("/api/documents").status_code == 403
```

- [ ] **Step 9: Run to verify it passes, then full suite + linters**

Run:
```bash
cd backend
pytest tests/integration/test_documents_api.py tests/integration/test_documents_entitlement.py -v
ruff check .
lint-imports
pytest -v
```
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/app/api/schemas.py backend/app/privacy_gateway/token_vault/vault.py \
  backend/app/api/documents.py backend/app/main.py \
  backend/tests/integration/test_documents_api.py backend/tests/integration/test_documents_entitlement.py \
  backend/tests/privacy_invariants/test_token_vault.py
git commit -m "feat: add documents API endpoints (upload, list, detail, original, delete)"
```

---

### Task 9: Background processing — bounded executor, status lifecycle, timeout

**Files:**
- Create: `backend/app/document_gateway/executor.py`
- Modify: `backend/app/api/documents.py`
- Create: `backend/tests/unit/document_gateway/test_executor.py`
- Create: `backend/tests/integration/test_document_background_processing.py`

**Interfaces:**
- Consumes: `DocumentPipeline`/`get_document_pipeline` (Task 6), `DocumentRepository.set_status`/`set_result` (existing), `Settings.document_processing_timeout_seconds` (Task 7).
- Produces: `get_document_executor() -> concurrent.futures.ThreadPoolExecutor` (module-level singleton, `max_workers=2`); `_process_document_background(document_id, tenant_id, raw_bytes, content_type, pipeline=None) -> None` in `app/api/documents.py`, wired into `POST /api/documents` via `BackgroundTasks.add_task`.

- [ ] **Step 1: Write the failing executor test**

Create `backend/tests/unit/document_gateway/test_executor.py`:

```python
from app.document_gateway.executor import get_document_executor


def test_get_document_executor_is_a_singleton():
    assert get_document_executor() is get_document_executor()


def test_get_document_executor_is_bounded_to_two_workers():
    assert get_document_executor()._max_workers == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest tests/unit/document_gateway/test_executor.py -v` → FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Create `executor.py`**

```python
from __future__ import annotations

import concurrent.futures

# Bounded (not FastAPI/Starlette's own default background threadpool) so a
# burst of uploads can't spawn unbounded OS threads each holding a Tesseract
# subprocess (spec §8). Sized conservatively against UVICORN_WORKERS and
# shared CPU budget -- OCR is genuinely CPU-heavy.
_MAX_WORKERS = 2
_executor: concurrent.futures.ThreadPoolExecutor | None = None


def get_document_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_MAX_WORKERS, thread_name_prefix="doc-pipeline"
        )
    return _executor
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && pytest tests/unit/document_gateway/test_executor.py -v` → PASS.

- [ ] **Step 5: Write the failing background-processing integration test**

Create `backend/tests/integration/test_document_background_processing.py`:

```python
import uuid

import pytest

from app.api.documents import _process_document_background
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.document_gateway.models import ProcessResult
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


class _FakePipeline:
    def __init__(self, result=None, exc=None, sleep_seconds=0):
        self._result = result
        self._exc = exc
        self._sleep_seconds = sleep_seconds

    def process(self, tenant_id, document_id, raw_bytes, content_type):
        import time
        if self._sleep_seconds:
            time.sleep(self._sleep_seconds)
        if self._exc is not None:
            raise self._exc
        return self._result


@pytest.fixture
def scoped_document(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.document_gateway.storage.get_settings",
        lambda: type("S", (), {"documents_storage_root": str(tmp_path)})(),
    )
    import os
    key_provider = FileSecretKeyProvider(os.environ["MASTER_KEY_PATH"])
    with SessionLocal() as session:
        tenant_id = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        ).id
        session.commit()
    with tenant_scoped_session(tenant_id) as session:
        user_id = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        ).id
        document_id = DocumentRepository(session).create(
            tenant_id, user_id, filename="a.pdf", content_type="application/pdf",
            byte_size=3, raw_storage_path="/tmp/x",
        ).id
    return tenant_id, document_id


def test_success_transitions_queued_to_processing_to_ready(scoped_document):
    tenant_id, document_id = scoped_document
    result = ProcessResult(
        document_type="pdf_digital", page_count=2,
        structured_blocks=[{"page": 1, "block_type": "paragraph", "order": 0,
                             "sanitized_text": "Hallo", "source_bbox": None, "blocked": False}],
        sanitized_markdown="Hallo\n",
    )
    _process_document_background(
        document_id, tenant_id, b"raw", "application/pdf", pipeline=_FakePipeline(result=result)
    )

    with tenant_scoped_session(tenant_id) as session:
        document = DocumentRepository(session).get(tenant_id, document_id)
        assert document.status == "ready"
        assert document.sanitized_markdown == "Hallo\n"
        assert document.page_count == 2
        assert document.document_type == "pdf_digital"


def test_a_stage_level_exception_marks_the_document_failed_with_a_generic_message(scoped_document):
    tenant_id, document_id = scoped_document
    _process_document_background(
        document_id, tenant_id, b"raw", "application/pdf",
        pipeline=_FakePipeline(exc=RuntimeError("corrupt PDF: page 3 xref table invalid")),
    )

    with tenant_scoped_session(tenant_id) as session:
        document = DocumentRepository(session).get(tenant_id, document_id)
        assert document.status == "failed"
        assert document.error_message is not None
        # Never leaks the raw exception text/extracted content into a persisted column.
        assert "xref" not in document.error_message


def test_a_processing_timeout_marks_the_document_failed(scoped_document, monkeypatch):
    tenant_id, document_id = scoped_document
    settings = __import__("app.config", fromlist=["get_settings"]).get_settings()
    monkeypatch.setattr(settings, "document_processing_timeout_seconds", 0.05)
    monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)

    _process_document_background(
        document_id, tenant_id, b"raw", "application/pdf",
        pipeline=_FakePipeline(sleep_seconds=0.3),
    )

    with tenant_scoped_session(tenant_id) as session:
        document = DocumentRepository(session).get(tenant_id, document_id)
        assert document.status == "failed"
        assert "time" in document.error_message.lower()


def test_status_is_processing_immediately_visible_before_the_pipeline_finishes(scoped_document):
    """A concurrent GET should see status=processing right away -- set_status is
    committed in its own transaction before the (potentially slow) pipeline
    call, not batched with the final result."""
    tenant_id, document_id = scoped_document
    seen = {}

    class _ObservingPipeline(_FakePipeline):
        def process(self, tenant_id, document_id, raw_bytes, content_type):
            with tenant_scoped_session(tenant_id) as session:
                seen["status_at_call_time"] = DocumentRepository(session).get(tenant_id, document_id).status
            return ProcessResult(document_type="image", page_count=1, structured_blocks=[], sanitized_markdown="")

    _process_document_background(document_id, tenant_id, b"raw", "image/png", pipeline=_ObservingPipeline())
    assert seen["status_at_call_time"] == "processing"
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd backend && pytest tests/integration/test_document_background_processing.py -v`
Expected: FAIL — `ImportError: cannot import name '_process_document_background'`.

- [ ] **Step 7: Add `_process_document_background` and wire it into the upload endpoint**

Edit `backend/app/api/documents.py`:

```python
import concurrent.futures
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.schemas import DocumentDetail, DocumentSummary
from app.auth.dependencies import get_current_user, get_db_session, require_app_entitlement
from app.auth.tenant_resolver import AuthenticatedUser
from app.config import get_settings
from app.db.repositories.document_repository import DocumentRepository
from app.db.session import tenant_scoped_session
from app.document_gateway import storage
from app.document_gateway.executor import get_document_executor
from app.document_gateway.pipeline import DocumentPipeline, get_document_pipeline
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault

logger = logging.getLogger(__name__)
# ... router/ALLOWED_CONTENT_TYPES/_to_summary/_get_owned_document unchanged ...


def _process_document_background(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    raw_bytes: bytes,
    content_type: str,
    pipeline: DocumentPipeline | None = None,
) -> None:
    """Runs off the request/response cycle (scheduled via BackgroundTasks).
    Deliberately synchronous, not `async def` -- Starlette already runs a sync
    background callable in its own worker threadpool; the CPU-heavy Tesseract
    work inside `pipeline.process` is itself bounded by the separate, small
    `get_document_executor()` pool, not by however many background tasks are
    in flight.
    """
    pipeline = pipeline or get_document_pipeline()
    settings = get_settings()

    with tenant_scoped_session(tenant_id) as session:
        DocumentRepository(session).set_status(tenant_id, document_id, "processing")

    future = get_document_executor().submit(
        pipeline.process, tenant_id, document_id, raw_bytes, content_type
    )
    try:
        result = future.result(timeout=settings.document_processing_timeout_seconds)
    except concurrent.futures.TimeoutError:
        future.cancel()
        logger.error(
            "document.process TIMEOUT tenant_id=%s document_id=%s", tenant_id, document_id
        )
        with tenant_scoped_session(tenant_id) as session:
            DocumentRepository(session).set_status(
                tenant_id, document_id, "failed", error_message="Processing timed out."
            )
        return
    except Exception:
        # Never include extracted text/exception detail in the persisted
        # error_message -- same "log categories, not content" discipline as
        # Pipeline.sanitize. Full traceback goes to the log only.
        logger.exception(
            "document.process FAILED tenant_id=%s document_id=%s", tenant_id, document_id
        )
        with tenant_scoped_session(tenant_id) as session:
            DocumentRepository(session).set_status(
                tenant_id, document_id, "failed", error_message="Document could not be processed."
            )
        return

    with tenant_scoped_session(tenant_id) as session:
        DocumentRepository(session).set_result(
            tenant_id,
            document_id,
            structured_blocks=result.structured_blocks,
            sanitized_markdown=result.sanitized_markdown,
            page_count=result.page_count,
            document_type=result.document_type,
        )
    logger.info(
        "document.process COMPLETE tenant_id=%s document_id=%s -> ready", tenant_id, document_id
    )


@router.post("", response_model=DocumentSummary, status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> DocumentSummary:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail="unsupported file type")

    data = await file.read()
    settings = get_settings()
    if len(data) > settings.max_document_size_bytes:
        raise HTTPException(status_code=422, detail="file too large")

    document = DocumentRepository(session).create(
        user.tenant_id, user.user_id, filename=file.filename or "upload",
        content_type=file.content_type, byte_size=len(data), raw_storage_path="",
    )
    raw_storage_path = storage.save_raw_file(user.tenant_id, document.id, file.content_type, data)
    document.raw_storage_path = raw_storage_path
    session.commit()

    background_tasks.add_task(
        _process_document_background, document.id, user.tenant_id, data, file.content_type
    )
    return _to_summary(document)
```

- [ ] **Step 8: Run to verify it passes, then full suite + linters**

Run:
```bash
cd backend
pytest tests/unit/document_gateway tests/integration/test_document_background_processing.py \
  tests/integration/test_documents_api.py -v
ruff check .
lint-imports
pytest -v
```
Expected: all PASS. `test_upload_creates_a_queued_document_and_returns_202` (Task 8) still passes unchanged.

- [ ] **Step 9: Commit**

```bash
git add backend/app/document_gateway/executor.py backend/app/api/documents.py \
  backend/tests/unit/document_gateway/test_executor.py \
  backend/tests/integration/test_document_background_processing.py
git commit -m "feat: wire bounded background document processing with status lifecycle and timeout"
```

---

### Task 10: Extend `POST /api/conversations/{id}/messages` with `document_ids`

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/api/chat.py`
- Create: `backend/tests/integration/test_chat_document_attach.py`
- Modify: `backend/tests/unit/test_config.py`

**Interfaces:**
- Consumes: `SendMessageIn.document_ids` (Task 8), `DocumentRepository.get` (existing), `AppEntitlementRepository.is_entitled_and_assigned` (existing).
- Produces: `Settings.max_message_with_attachments_chars: int = 100_000` (this plan's minimal, explicit stand-in for spec §6.6 step 4's context-window check — see note below); `send_message` accepting and validating `document_ids`.

**Note on spec §6.6 step 4:** the referenced context-window trimming design (`docs/superpowers/specs/2026-08-21-conversation-history-context-and-usage-design.md`) has no implementation anywhere in this codebase yet (`grep` for `context_window`/`token_count`/`trimming` returns nothing) — it is evidently a separate, not-yet-built slice, not a reusable helper. Re-implementing it is out of scope here. This task instead adds one explicit, self-contained, character-count-based fail-closed guard scoped **only** to the document-attach path (so it introduces no new constraint on plain chat messages, which had none before), clearly flagged in the code as a placeholder for the real trimming design.

- [ ] **Step 1: Add the new setting**

Append to `backend/tests/unit/test_config.py`:

```python
def test_settings_max_message_with_attachments_chars_default(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.setenv("APP_OPS_PASSWORD", "ops-secret")
    monkeypatch.delenv("MAX_MESSAGE_WITH_ATTACHMENTS_CHARS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.max_message_with_attachments_chars == 100_000
```

Run: `cd backend && pytest tests/unit/test_config.py -v` → FAIL, `AttributeError`.

Add to `backend/app/config.py`, after `documents_storage_root`:

```python
    # Documents App Phase 1 (spec §6.6 step 4): a minimal, explicit fail-closed
    # guard on a chat message with attached document markdown, scoped only to
    # that path. Not a re-implementation of the conversation-history
    # context-window trimming design (2026-08-21 spec) -- that design has no
    # implementation anywhere in this codebase yet to reuse; this is a
    # deliberately simple stand-in that rejects rather than silently truncates.
    max_message_with_attachments_chars: int = 100_000
```

Run: `cd backend && pytest tests/unit/test_config.py -v` → PASS.

- [ ] **Step 2: Write the failing chat-attach integration tests**

Create `backend/tests/integration/test_chat_document_attach.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import DEFAULT_PERMISSIONS
from app.auth.tenant_resolver import AuthenticatedUser
from app.config import get_settings
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.llm_gateway.provider import ChatMessage, StreamDelta, StreamUsage
from app.llm_gateway.registry import get_provider
from app.main import app
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from tests.conftest import grant_app_entitlement


class _CapturingProvider:
    name = "stub"
    model = "stub-model"

    def __init__(self):
        self.captured_messages: list[list[ChatMessage]] = []

    def stream(self, messages):
        self.captured_messages.append(list(messages))
        yield StreamDelta(text="Antwort.")
        yield StreamUsage(tokens_in=1, tokens_out=1, cost_usd=0)


@pytest.fixture
def scope():
    import os
    key_provider = FileSecretKeyProvider(os.environ["MASTER_KEY_PATH"])
    with SessionLocal() as session:
        tenant_id = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        ).id
        session.commit()
    grant_app_entitlement(tenant_id, app_key="anonymization")
    grant_app_entitlement(tenant_id, app_key="documents")
    with tenant_scoped_session(tenant_id) as session:
        user_id = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        ).id
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id
        document_id = DocumentRepository(session).create(
            tenant_id, user_id, filename="befund.pdf", content_type="application/pdf",
            byte_size=1, raw_storage_path="/tmp/x",
        ).id
        DocumentRepository(session).set_result(
            tenant_id, document_id,
            structured_blocks=[], sanitized_markdown="Befund: alles unauffällig.",
            page_count=1, document_type="pdf_digital",
        )
    return tenant_id, user_id, conversation_id, document_id


@pytest.fixture
def authenticated(scope):
    tenant_id, user_id, _, _ = scope
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor", branch_id=None,
        email="doc@example.com", permissions=DEFAULT_PERMISSIONS["doctor"],
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_attached_ready_document_markdown_is_prepended_before_sanitize(scope, authenticated):
    tenant_id, user_id, conversation_id, document_id = scope
    provider = _CapturingProvider()
    app.dependency_overrides[get_provider] = lambda: provider
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Bitte fassen Sie zusammen.", "document_ids": [str(document_id)]},
    )

    assert response.status_code == 200
    # The final user-role message sent to the provider is the sanitized,
    # already-token-shaped combination -- content is never sent raw either way,
    # but "Befund" (the document's marker text) proves the markdown was folded
    # in at all.
    last_user_message = [m for m in provider.captured_messages[0] if m.role == "user"][-1]
    assert "Befund" in last_user_message.content
    assert "befund.pdf" in last_user_message.content
    app.dependency_overrides.pop(get_provider, None)


def test_a_not_ready_document_is_rejected(scope, authenticated):
    tenant_id, user_id, conversation_id, _ = scope
    with tenant_scoped_session(tenant_id) as session:
        queued_document_id = DocumentRepository(session).create(
            tenant_id, user_id, filename="pending.pdf", content_type="application/pdf",
            byte_size=1, raw_storage_path="/tmp/y",
        ).id
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "x", "document_ids": [str(queued_document_id)]},
    )
    assert response.status_code == 422


def test_another_users_document_is_rejected_with_404(scope, authenticated):
    tenant_id, _, conversation_id, _ = scope
    with tenant_scoped_session(tenant_id) as session:
        other_user_id = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="other@example.com", role="doctor"
        ).id
        other_document_id = DocumentRepository(session).create(
            tenant_id, other_user_id, filename="theirs.pdf", content_type="application/pdf",
            byte_size=1, raw_storage_path="/tmp/z",
        ).id
        DocumentRepository(session).set_result(
            tenant_id, other_document_id, structured_blocks=[],
            sanitized_markdown="geheim", page_count=1, document_type="pdf_digital",
        )
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "x", "document_ids": [str(other_document_id)]},
    )
    assert response.status_code == 404


def test_document_attach_requires_the_documents_entitlement_even_with_anonymization(scope, authenticated):
    """spec §6.6 step 3: a tenant without 'documents' must not be able to
    smuggle document content into chat via a raw API call, even though
    'anonymization' alone is enough to POST a plain message."""
    tenant_id, _, conversation_id, document_id = scope
    from tests.conftest import revoke_app_entitlement
    revoke_app_entitlement(tenant_id, app_key="documents")
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "x", "document_ids": [str(document_id)]},
    )
    assert response.status_code == 403


def test_plain_messages_without_document_ids_are_unaffected(scope, authenticated):
    from tests.conftest import revoke_app_entitlement
    tenant_id, _, conversation_id, _ = scope
    revoke_app_entitlement(tenant_id, app_key="documents")
    provider = _CapturingProvider()
    app.dependency_overrides[get_provider] = lambda: provider
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Hallo"}
    )
    assert response.status_code == 200
    app.dependency_overrides.pop(get_provider, None)


def test_combined_content_over_the_char_limit_is_rejected_with_422(scope, authenticated, monkeypatch):
    tenant_id, user_id, conversation_id, document_id = scope
    settings = get_settings()
    monkeypatch.setattr(settings, "max_message_with_attachments_chars", 10)
    monkeypatch.setattr("app.api.chat.get_settings", lambda: settings)
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "x", "document_ids": [str(document_id)]},
    )
    assert response.status_code == 422
```

- [ ] **Step 3: Run to verify these fail**

Run: `cd backend && pytest tests/integration/test_chat_document_attach.py -v`
Expected: FAIL — `SendMessageIn` already has `document_ids` (Task 8's schema change), so these fail on behavior, not import: attaching currently does nothing (`400`/wrong content, no 422/403/404 differentiation).

- [ ] **Step 4: Extend `send_message`**

Edit `backend/app/api/chat.py`:

```python
from app.config import get_settings
from app.db.repositories.app_entitlement_repository import AppEntitlementRepository
from app.db.repositories.document_repository import DocumentRepository

ATTACHMENT_HEADER = "\n\n--- Angehängtes Dokument: {filename} ---\n\n"
ATTACHMENT_FOOTER = "\n\n--- Ende Dokument ---\n\n"


def _combine_content_with_attachments(content: str, documents: list) -> str:
    prefix = "".join(
        ATTACHMENT_HEADER.format(filename=doc.filename) + (doc.sanitized_markdown or "") + ATTACHMENT_FOOTER
        for doc in documents
    )
    return prefix + content


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageIn,
    user: AuthenticatedUser = Depends(get_current_user),
    _entitlement: AuthenticatedUser = Depends(require_app_entitlement(app_key="anonymization")),
    session: Session = Depends(get_db_session),
    pipeline: Pipeline = Depends(get_pipeline),
    provider: LLMProvider = Depends(get_provider),
) -> StreamingResponse:
    conversation_repo = ConversationRepository(session)
    conversation = conversation_repo.get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")

    combined_content = body.content
    if body.document_ids:
        # spec §6.6 step 3: conditional on the field being present, not a
        # blanket second Depends -- a request with no document_ids never pays
        # this extra check or requires this extra entitlement.
        if not AppEntitlementRepository(session).is_entitled_and_assigned(
            user.tenant_id, "documents", user.branch_id
        ):
            raise HTTPException(
                status_code=403, detail="tenant is not entitled to the 'documents' app"
            )

        document_repo = DocumentRepository(session)
        documents = []
        for document_id in body.document_ids:
            document = document_repo.get(user.tenant_id, document_id)
            if document is None or document.user_id != user.user_id:
                raise HTTPException(status_code=404, detail="document not found")
            if document.status != "ready":
                raise HTTPException(status_code=422, detail="document is not ready to attach")
            documents.append(document)

        combined_content = _combine_content_with_attachments(body.content, documents)
        settings = get_settings()
        if len(combined_content) > settings.max_message_with_attachments_chars:
            raise HTTPException(
                status_code=422, detail="message with attached documents is too large"
            )

    logger.info(
        "chat.receive tenant_id=%s conversation_id=%s user_id=%s length=%d documents=%d",
        user.tenant_id, conversation_id, user.user_id, len(combined_content), len(body.document_ids or []),
    )

    try:
        sanitized_prompt = pipeline.sanitize(
            user.tenant_id, "conversation", conversation_id, combined_content
        )
    except (LowConfidenceSpanError, HighRiskMessageError, ResidualPIIError) as exc:
        raise HTTPException(status_code=422, detail=FAIL_CLOSED_MESSAGE) from exc

    # ... rest of the function unchanged (history assembly, persistence, streaming) ...
```

(The `_derive_title` call further down should keep deriving from `sanitized_prompt` as before — unchanged.)

- [ ] **Step 5: Run to verify these pass, then full suite + linters**

Run:
```bash
cd backend
pytest tests/integration/test_chat_document_attach.py -v
pytest tests/integration/test_chat_api.py -v   # unaffected plain-message behavior
ruff check .
lint-imports
pytest -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/api/chat.py \
  backend/tests/integration/test_chat_document_attach.py backend/tests/unit/test_config.py
git commit -m "feat: attach ready documents into chat messages before sanitize"
```

---

### Task 11: Privacy-invariant tests — raw PII never persisted, fail-closed-per-block

**Files:**
- Create: `backend/tests/privacy_invariants/test_document_never_reaches_provider_raw.py`
- Create: `backend/tests/privacy_invariants/test_document_fail_closed_per_block.py`

**Interfaces:**
- Consumes: `DocumentPipeline` (Task 6), `corpus_pipeline`/`golden_corpus` fixtures (existing, `tests/privacy_invariants/conftest.py`).
- **Note:** document-scope token isolation (a token minted under one document must not resolve under another document or any conversation scope) is **already fully covered** by the foundation plan's `test_document_scoped_*` tests in `tests/privacy_invariants/test_token_vault.py` — this task does not duplicate it.

- [ ] **Step 1: Write the failing "never reaches provider raw" test**

Create `backend/tests/privacy_invariants/test_document_never_reaches_provider_raw.py`:

```python
import uuid

from app.document_gateway.pipeline import DocumentPipeline
from tests.privacy_invariants.conftest import corpus_pipeline, golden_corpus, load_golden_corpus

CORPUS = load_golden_corpus()
SANITIZABLE = [note for note in CORPUS if note["expected_outcome"] == "sanitized"]


class _FakeOCREngine:
    """Deterministic stand-in that returns each corpus note's raw text as a
    single OCR page -- exercises the real DocumentPipeline (classify ->
    reconstruct -> per-block sanitize -> render) without a real Tesseract
    binary."""

    def __init__(self, text: str):
        self._text = text

    def run(self, image):
        return self._text, []


def test_every_golden_corpus_note_produces_zero_raw_pii_when_processed_as_a_document(
    corpus_pipeline, monkeypatch
):
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    for note in SANITIZABLE:
        monkeypatch.setattr(
            "app.document_gateway.pipeline.classification.classify",
            lambda content_type, raw_bytes, note=note: ("image", None),
        )
        pipeline = DocumentPipeline(
            privacy_pipeline=corpus_pipeline, ocr_engine=_FakeOCREngine(note["raw_text"])
        )

        result = pipeline.process(tenant_id, document_id, b"fake-image-bytes", "image/png")

        for entity in note["entities"]:
            assert entity["text"] not in result.sanitized_markdown, (
                f"{note['note_id']}: raw entity text leaked into sanitized_markdown"
            )
        for block in result.structured_blocks:
            assert block["sanitized_text"] is None or note["raw_text"] not in block["sanitized_text"]
```

- [ ] **Step 2: Run to verify it fails, or discover an existing recall gap**

Run: `cd backend && pytest tests/privacy_invariants/test_document_never_reaches_provider_raw.py -v`
Expected: initially FAIL — `ModuleNotFoundError` (file doesn't exist until Step 1 lands) or, once it does, potentially reveals the same `KNOWN_RECALL_GAPS`/`KNOWN_GUARD_FALSE_POSITIVES` entries `test_pipeline_corpus.py` already documents (the underlying detector stack is identical). If so, mirror those same documented exclusions here rather than silently weakening the assertion — import and reuse `KNOWN_RECALL_GAPS` from `tests/privacy_invariants/test_pipeline_corpus.py` if any golden-corpus note trips it through this path too.

- [ ] **Step 3: Write the failing fail-closed-per-block test**

Create `backend/tests/privacy_invariants/test_document_fail_closed_per_block.py`:

```python
import uuid

from app.document_gateway.pipeline import DocumentPipeline
from app.document_gateway.rendering.markdown_renderer import BLOCKED_PLACEHOLDER
from tests.privacy_invariants.conftest import corpus_pipeline


class _TwoParagraphOCREngine:
    def run(self, image):
        return (
            "Absatz eins ist völlig unproblematisch und lang genug für den Test.\n\n"
            "Herr Müller",  # a bare surname with no other context -- the same
                             # shape test_pipeline_corpus.py documents as a
                             # real recall/precision edge case for this detector.
            [],
        )


def test_one_ambiguous_block_does_not_abort_the_whole_document(corpus_pipeline):
    pipeline = DocumentPipeline(privacy_pipeline=corpus_pipeline, ocr_engine=_TwoParagraphOCREngine())

    result = pipeline.process(uuid.uuid4(), uuid.uuid4(), b"x", "image/png")

    # The document still "completes" -- DocumentPipeline.process never raises
    # for a block-level failure, and the unaffected block's real content is
    # still present.
    assert "unproblematisch" in result.sanitized_markdown


def test_a_blocked_blocks_placeholder_never_contains_original_text(corpus_pipeline):
    pipeline = DocumentPipeline(privacy_pipeline=corpus_pipeline, ocr_engine=_TwoParagraphOCREngine())
    result = pipeline.process(uuid.uuid4(), uuid.uuid4(), b"x", "image/png")

    blocked_entries = [b for b in result.structured_blocks if b["blocked"]]
    for entry in blocked_entries:
        assert entry["sanitized_text"] is None
    if blocked_entries:
        assert BLOCKED_PLACEHOLDER in result.sanitized_markdown
        assert "Müller" not in result.sanitized_markdown
```

(If this specific two-paragraph fixture doesn't actually trip a block-level failure against the real corpus-derived detector stack, swap `"Herr Müller"` for a span from `test_pipeline_corpus.py`'s own `REJECTED`/`KNOWN_GUARD_FALSE_POSITIVES` set, which is already proven to trip `HighRiskMessageError`/`ResidualPIIError` — import one of those note bodies directly rather than inventing a new untested string.)

- [ ] **Step 4: Run to verify, then full suite + linters**

Run:
```bash
cd backend
pytest tests/privacy_invariants/test_document_never_reaches_provider_raw.py \
  tests/privacy_invariants/test_document_fail_closed_per_block.py -v
ruff check .
lint-imports
pytest -v
```
Expected: all PASS (after resolving Step 2's discovery, if any, the same way `test_pipeline_corpus.py` already does — documented exclusion, never a silently weakened assertion).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/privacy_invariants/test_document_never_reaches_provider_raw.py \
  backend/tests/privacy_invariants/test_document_fail_closed_per_block.py
git commit -m "test: add privacy-invariant coverage for document processing"
```

---

## Part B — Frontend: Documents app

### Task 12: App catalog entry, types, `lib/api/documents.ts`

**Files:**
- Modify: `frontend/lib/appCatalog.ts`
- Modify: `frontend/lib/api/types.ts`
- Create: `frontend/lib/api/documents.ts`
- Create: `frontend/lib/api/documents.test.ts`

**Interfaces:**
- Produces: `APP_CATALOG.documents`; `DocumentSummary`/`DocumentDetail` TS interfaces; `DocumentsApiError extends Error` (status + message, mirroring `ChatApiError`, not the plain-`Error` `conversations.ts` convention — upload/polling need status-aware handling for 413/415/404/422); `uploadDocument`, `listDocuments`, `getDocument`, `getOriginalFileUrl`, `deleteDocument`. Consumed by Tasks 13–16.

- [ ] **Step 1: Add the app-catalog entry**

Edit `frontend/lib/appCatalog.ts`:

```typescript
import { IconApps, IconFileText, IconShieldLock } from "@tabler/icons-react";
...
const APP_CATALOG: Record<string, AppCatalogEntry> = {
  anonymization: { icon: IconShieldLock, color: "teal" },
  documents: { icon: IconFileText, color: "blue" },
};
```

- [ ] **Step 2: Add the TypeScript types**

Edit `frontend/lib/api/types.ts` — add after `AvailableApp`:

```typescript
export interface DocumentSummary {
  id: string;
  filename: string;
  content_type: string;
  document_type: string | null;
  status: "queued" | "processing" | "ready" | "failed";
  error_message: string | null;
  page_count: number | null;
  byte_size: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  sanitized_markdown: string | null;
}
```

- [ ] **Step 3: Write the failing `documents.test.ts`**

Create `frontend/lib/api/documents.test.ts`:

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  DocumentsApiError,
  deleteDocument,
  getDocument,
  getOriginalFileUrl,
  listDocuments,
  uploadDocument,
} from "./documents";

describe("documents API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uploadDocument posts multipart form data without setting Content-Type", async () => {
    const created = { id: "1", filename: "scan.pdf", status: "queued" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => created });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["%PDF-fake"], "scan.pdf", { type: "application/pdf" });

    const result = await uploadDocument("token-123", file);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/api/documents");
    expect(options.method).toBe("POST");
    expect(options.headers).toEqual({ Authorization: "Bearer token-123" });
    expect(options.headers["Content-Type"]).toBeUndefined();
    expect(options.body).toBeInstanceOf(FormData);
    expect(result).toEqual(created);
  });

  it("uploadDocument throws a DocumentsApiError with the status and server detail on failure", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: "file too large" }) });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["x"], "big.pdf", { type: "application/pdf" });

    await expect(uploadDocument("token-123", file)).rejects.toMatchObject({
      name: "DocumentsApiError",
      status: 422,
      message: "file too large",
    });
  });

  it("listDocuments returns the parsed list", async () => {
    const documents = [{ id: "1", filename: "a.pdf", status: "ready" }];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => documents });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listDocuments("token-123");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/documents", {
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(documents);
  });

  it("getDocument fetches a single document by id", async () => {
    const document = { id: "1", filename: "a.pdf", status: "ready", sanitized_markdown: "# a" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => document });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getDocument("token-123", "1");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/documents/1", {
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(document);
  });

  it("getDocument throws DocumentsApiError with status 404 when not found", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({ detail: "document not found" }) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(getDocument("token-123", "missing")).rejects.toMatchObject({ status: 404 });
  });

  it("getOriginalFileUrl builds the owner-only original endpoint URL", () => {
    expect(getOriginalFileUrl("42")).toBe("http://localhost:8000/api/documents/42/original");
  });

  it("deleteDocument sends DELETE and resolves on 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 204 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteDocument("token-123", "5")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/documents/5", {
      method: "DELETE",
      headers: { Authorization: "Bearer token-123" },
    });
  });
});
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd frontend && npx vitest run lib/api/documents.test.ts`
Expected: FAIL — module `./documents` doesn't exist.

- [ ] **Step 5: Create `documents.ts`**

Create `frontend/lib/api/documents.ts`:

```typescript
import type { DocumentDetail, DocumentSummary } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class DocumentsApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "DocumentsApiError";
    this.status = status;
  }
}

async function _errorFromResponse(response: Response): Promise<DocumentsApiError> {
  let message = "Something went wrong. Try again.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    // response body wasn't JSON; keep the generic message
  }
  return new DocumentsApiError(response.status, message);
}

export async function uploadDocument(accessToken: string, file: File): Promise<DocumentSummary> {
  const formData = new FormData();
  formData.append("file", file);
  // Do NOT set Content-Type -- the browser generates the multipart boundary.
  const response = await fetch(`${API_BASE_URL}/api/documents`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: formData,
  });
  if (!response.ok) throw await _errorFromResponse(response);
  return response.json();
}

export async function listDocuments(accessToken: string): Promise<DocumentSummary[]> {
  const response = await fetch(`${API_BASE_URL}/api/documents`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw await _errorFromResponse(response);
  return response.json();
}

export async function getDocument(accessToken: string, documentId: string): Promise<DocumentDetail> {
  const response = await fetch(`${API_BASE_URL}/api/documents/${documentId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw await _errorFromResponse(response);
  return response.json();
}

/** Owner-only; the caller renders this behind a permanent "not anonymized"
 * warning banner (spec §2 decision 3) and must attach the bearer token itself
 * (e.g. via a fetch+blob URL, not a bare <img>/<a> src) -- this only builds
 * the URL. */
export function getOriginalFileUrl(documentId: string): string {
  return `${API_BASE_URL}/api/documents/${documentId}/original`;
}

export async function deleteDocument(accessToken: string, documentId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/documents/${documentId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok && response.status !== 204) throw await _errorFromResponse(response);
}
```

- [ ] **Step 6: Run to verify it passes, then the full frontend suite + lint**

Run: `cd frontend && npm test && npm run lint`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/appCatalog.ts frontend/lib/api/types.ts \
  frontend/lib/api/documents.ts frontend/lib/api/documents.test.ts
git commit -m "feat: add documents app-catalog entry, types, and API client"
```

---

### Task 13: `DocumentSidebar.tsx`

**Files:**
- Create: `frontend/components/DocumentSidebar.tsx`
- Create: `frontend/components/DocumentSidebar.module.css`
- Create: `frontend/components/DocumentSidebar.test.tsx`

**Interfaces:**
- Consumes: `uploadDocument`/`listDocuments`/`deleteDocument` (Task 12), `useMe()` (existing `MeProvider`).
- Produces: `<DocumentSidebar />`. Consumed by Task 14's `apps/documents/layout.tsx`.

- [ ] **Step 1: Write the failing test**

Create `frontend/components/DocumentSidebar.test.tsx` (mirrors `ConversationSidebar.test.tsx`'s mocking pattern):

```typescript
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
  signIn: vi.fn(),
}));

const mockRouterPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
  usePathname: () => "/apps/documents",
}));

const mockUseMe = vi.fn();
vi.mock("@/components/MeProvider", () => ({
  useMe: () => mockUseMe(),
}));

import * as documentsApi from "@/lib/api/documents";
import { DocumentSidebar } from "./DocumentSidebar";

function doc(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "1", filename: "befund.pdf", content_type: "application/pdf",
    document_type: "pdf_digital", status: "ready", error_message: null,
    page_count: 1, byte_size: 1024, created_at: "x", updated_at: "x",
    ...overrides,
  };
}

describe("DocumentSidebar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockRouterPush.mockReset();
    mockUseMe.mockReturnValue({ permissions: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("lists documents on mount with a status indicator", async () => {
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([doc()]);
    render(<DocumentSidebar />);
    expect(await screen.findByText("befund.pdf")).toBeInTheDocument();
  });

  it("uploads a file and navigates to the new document", async () => {
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([]);
    vi.spyOn(documentsApi, "uploadDocument").mockResolvedValue(doc({ id: "new-1", status: "queued" }));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<DocumentSidebar />);
    const file = new File(["%PDF-x"], "scan.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/dokument hochladen/i, { selector: "input" });
    await user.upload(input, file);

    await waitFor(() => expect(documentsApi.uploadDocument).toHaveBeenCalledWith("token-123", file));
    await waitFor(() => expect(mockRouterPush).toHaveBeenCalledWith("/apps/documents/d/new-1"));
  });

  it("re-fetches the list on an interval while a document is queued or processing", async () => {
    const listSpy = vi
      .spyOn(documentsApi, "listDocuments")
      .mockResolvedValueOnce([doc({ status: "processing" })])
      .mockResolvedValueOnce([doc({ status: "ready" })]);

    render(<DocumentSidebar />);
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(1));

    vi.advanceTimersByTime(5000);
    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(2));

    // Once every document is ready, polling stops -- no third call.
    vi.advanceTimersByTime(10000);
    await waitFor(() => expect(listSpy.mock.calls.length).toBeLessThanOrEqual(2));
  });

  it("removes a document from the list after deleting it", async () => {
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([doc()]);
    vi.spyOn(documentsApi, "deleteDocument").mockResolvedValue(undefined);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<DocumentSidebar />);
    await screen.findByText("befund.pdf");
    await user.click(screen.getByLabelText("Delete befund.pdf"));

    await waitFor(() => expect(screen.queryByText("befund.pdf")).not.toBeInTheDocument());
  });

  it("shows a failed-status indicator with the error message on hover/title", async () => {
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([
      doc({ status: "failed", error_message: "Document could not be processed." }),
    ]);
    render(<DocumentSidebar />);
    const row = await screen.findByText("befund.pdf");
    expect(row.closest("[data-status]")).toHaveAttribute("data-status", "failed");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run components/DocumentSidebar.test.tsx`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `DocumentSidebar.tsx`**

Create `frontend/components/DocumentSidebar.tsx`:

```typescript
"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useSession } from "next-auth/react";
import { usePathname, useRouter } from "next/navigation";
import { IconFileText, IconTrash, IconUpload } from "@tabler/icons-react";
import { deleteDocument, listDocuments, uploadDocument } from "@/lib/api/documents";
import type { DocumentSummary } from "@/lib/api/types";
import styles from "./DocumentSidebar.module.css";

const POLL_INTERVAL_MS = 5000;
const PENDING_STATUSES = new Set(["queued", "processing"]);

function statusLabel(status: DocumentSummary["status"]): string {
  switch (status) {
    case "queued":
      return "Wartet";
    case "processing":
      return "Wird verarbeitet";
    case "ready":
      return "Bereit";
    case "failed":
      return "Fehlgeschlagen";
    default:
      return status;
  }
}

export function DocumentSidebar() {
  const { data: session } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeDocumentId = pathname.match(/^\/apps\/documents\/d\/([^/]+)/)?.[1];

  function refresh() {
    if (!session?.accessToken) return;
    listDocuments(session.accessToken)
      .then((result) => {
        setDocuments(result);
        setLoadError(false);
      })
      .catch(() => setLoadError(true));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(refresh, [session?.accessToken]);

  useEffect(() => {
    if (!documents?.some((d) => PENDING_STATUSES.has(d.status))) return;
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents, session?.accessToken]);

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !session?.accessToken) return;
    setUploading(true);
    try {
      const created = await uploadDocument(session.accessToken, file);
      setDocuments((current) => [created, ...(current ?? [])]);
      router.push(`/apps/documents/d/${created.id}`);
    } catch {
      setLoadError(true);
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(documentId: string, filename: string) {
    if (!session?.accessToken) return;
    try {
      await deleteDocument(session.accessToken, documentId);
      setDocuments((current) => current?.filter((d) => d.id !== documentId) ?? null);
      if (activeDocumentId === documentId) {
        router.push("/apps/documents");
      }
    } catch {
      setLoadError(true);
    }
  }

  return (
    <nav className={styles.sidebar}>
      <label className={styles.uploadButton}>
        <IconUpload size={16} stroke={2.5} />
        {uploading ? "Wird hochgeladen…" : "Dokument hochladen"}
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf,image/jpeg,image/png,image/tiff"
          aria-label="Dokument hochladen"
          className={styles.hiddenInput}
          disabled={uploading}
          onChange={handleUpload}
        />
      </label>

      {loadError && (
        <div className={styles.loadError}>
          <span>Documents could not be loaded.</span>
          <button type="button" className={styles.retryButton} onClick={refresh}>
            Try again
          </button>
        </div>
      )}

      {documents === null && !loadError && (
        <div className={styles.skeletonList} aria-hidden="true">
          <div className={styles.skeletonItem} />
          <div className={styles.skeletonItem} />
        </div>
      )}

      {documents !== null && documents.length === 0 && !loadError && (
        <div className={styles.emptyState}>
          <IconFileText size={28} stroke={1.5} />
          <p>Noch keine Dokumente.</p>
          <span>Mit &quot;Dokument hochladen&quot; loslegen.</span>
        </div>
      )}

      {documents?.map((document) => {
        const isActive = document.id === activeDocumentId;
        return (
          <div
            key={document.id}
            role="button"
            tabIndex={0}
            data-status={document.status}
            aria-current={isActive || undefined}
            className={isActive ? `${styles.documentItem} ${styles.documentItemActive}` : styles.documentItem}
            onClick={() => router.push(`/apps/documents/d/${document.id}`)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                router.push(`/apps/documents/d/${document.id}`);
              }
            }}
          >
            <span className={styles.documentTitle}>{document.filename}</span>
            <span
              className={`${styles.statusBadge} ${styles[`status_${document.status}`] ?? ""}`}
              title={document.status === "failed" ? document.error_message ?? undefined : undefined}
            >
              {statusLabel(document.status)}
            </span>
            <button
              type="button"
              aria-label={`Delete ${document.filename}`}
              className={styles.deleteButton}
              onClick={(event) => {
                event.stopPropagation();
                handleDelete(document.id, document.filename);
              }}
            >
              <IconTrash size={14} stroke={1.75} />
            </button>
          </div>
        );
      })}
    </nav>
  );
}
```

Create `frontend/components/DocumentSidebar.module.css` by copying `ConversationSidebar.module.css` verbatim and renaming `.conversationItem`/`.conversationItemActive`/`.conversationTitle` → `.documentItem`/`.documentItemActive`/`.documentTitle`, `.newButton` → `.uploadButton` (add `cursor: pointer` and a `.hiddenInput { display: none; }` rule since the upload control is a `<label>` wrapping a hidden `<input type="file">`, not a `<button>`), and add:

```css
.statusBadge {
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  padding: 2px 6px;
  font-size: 11px;
  white-space: nowrap;
  background: var(--color-accent-subtle);
  color: var(--color-text-muted);
}

.status_ready { background: var(--color-success-bg, var(--color-accent-subtle)); color: var(--color-success-text, var(--color-text)); }
.status_failed { background: var(--color-error-bg); color: var(--color-error-text); }
.status_queued,
.status_processing { background: var(--color-accent-subtle); color: var(--color-text-muted); }
```

(Reuse whatever success-color CSS variables already exist elsewhere in the codebase — check `frontend/app/globals.css` for `--color-success-*`; if none exist, define a local fallback exactly as above rather than inventing new global tokens in this task.)

- [ ] **Step 4: Run to verify it passes, then full frontend suite + lint**

Run: `cd frontend && npm test && npm run lint`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/DocumentSidebar.tsx frontend/components/DocumentSidebar.module.css \
  frontend/components/DocumentSidebar.test.tsx
git commit -m "feat: add DocumentSidebar with upload and status polling"
```

---

### Task 14: `apps/documents/` route tree — layout, empty state, detail page

**Files:**
- Create: `frontend/app/(shell)/apps/documents/layout.tsx`
- Create: `frontend/app/(shell)/apps/documents/layout.module.css`
- Create: `frontend/app/(shell)/apps/documents/page.tsx`
- Create: `frontend/app/(shell)/apps/documents/empty-state.module.css`
- Create: `frontend/app/(shell)/apps/documents/d/[documentId]/page.tsx`
- Create: `frontend/app/(shell)/apps/documents/d/[documentId]/page.module.css`
- Create: `frontend/app/(shell)/apps/documents/d/[documentId]/page.test.tsx`

**Interfaces:**
- Consumes: `<DocumentSidebar />` (Task 13), `getDocument` (Task 12).
- Produces: `/apps/documents`, `/apps/documents/d/[documentId]` routes.

- [ ] **Step 1: Layout and empty state (no tests needed — trivial, mirrors `apps/anonymization/layout.tsx`/`page.tsx` exactly)**

Create `frontend/app/(shell)/apps/documents/layout.tsx`:

```typescript
import type { ReactNode } from "react";
import { DocumentSidebar } from "@/components/DocumentSidebar";
import styles from "./layout.module.css";

export default function DocumentsLayout({ children }: { children: ReactNode }) {
  return (
    <div className={styles.shell}>
      <DocumentSidebar />
      <main className={styles.main}>{children}</main>
    </div>
  );
}
```

Copy `frontend/app/(shell)/apps/anonymization/layout.module.css` to `frontend/app/(shell)/apps/documents/layout.module.css` verbatim (identical `.shell`/`.main` flex layout).

Create `frontend/app/(shell)/apps/documents/page.tsx`:

```typescript
import { IconFileText } from "@tabler/icons-react";
import styles from "./empty-state.module.css";

export default function DocumentsEmptyState() {
  return (
    <div className={styles.emptyState}>
      <IconFileText size={40} stroke={1.5} />
      <p>Kein Dokument ausgewählt</p>
      <span>Wählen Sie links ein Dokument aus oder laden Sie ein neues hoch.</span>
    </div>
  );
}
```

Copy `frontend/app/(shell)/apps/anonymization/empty-state.module.css` to `frontend/app/(shell)/apps/documents/empty-state.module.css` verbatim.

- [ ] **Step 2: Write the failing detail-page test**

Create `frontend/app/(shell)/apps/documents/d/[documentId]/page.test.tsx`:

```typescript
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ documentId: "1" }),
}));

import * as documentsApi from "@/lib/api/documents";
import DocumentPage from "./page";

function detail(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "1", filename: "befund.pdf", content_type: "application/pdf",
    document_type: "pdf_digital", status: "ready", error_message: null,
    page_count: 2, byte_size: 2048, created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z", sanitized_markdown: "## Befund\n\nAlles unauffällig.",
    ...overrides,
  };
}

describe("DocumentPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => vi.useRealTimers());

  it("renders the sanitized markdown once ready", async () => {
    vi.spyOn(documentsApi, "getDocument").mockResolvedValue(detail());
    render(<DocumentPage />);
    expect(await screen.findByText(/Alles unauffällig/)).toBeInTheDocument();
  });

  it("polls while queued/processing and stops once ready", async () => {
    const spy = vi
      .spyOn(documentsApi, "getDocument")
      .mockResolvedValueOnce(detail({ status: "processing", sanitized_markdown: null }))
      .mockResolvedValueOnce(detail({ status: "ready" }));

    render(<DocumentPage />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/wird verarbeitet/i)).toBeInTheDocument();

    vi.advanceTimersByTime(5000);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await screen.findByText(/Alles unauffällig/);

    vi.advanceTimersByTime(10000);
    expect(spy.mock.calls.length).toBeLessThanOrEqual(2);
  });

  it("shows the failed state with the error message", async () => {
    vi.spyOn(documentsApi, "getDocument").mockResolvedValue(
      detail({ status: "failed", error_message: "Document could not be processed.", sanitized_markdown: null })
    );
    render(<DocumentPage />);
    expect(await screen.findByText("Document could not be processed.")).toBeInTheDocument();
  });

  it("shows a permanent, non-dismissable warning banner when the original file is toggled on", async () => {
    vi.spyOn(documentsApi, "getDocument").mockResolvedValue(detail());
    const user = (await import("@testing-library/user-event")).default.setup({
      advanceTimers: vi.advanceTimersByTime,
    });
    render(<DocumentPage />);
    await screen.findByText(/Alles unauffällig/);

    await user.click(screen.getByRole("button", { name: /original/i }));

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/nicht anonymisiert/i);
    // No dismiss/close control anywhere near the banner.
    expect(screen.queryByLabelText(/schließen|dismiss|close/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && npx vitest run "app/(shell)/apps/documents/d/[documentId]/page.test.tsx"`
Expected: FAIL — module doesn't exist.

- [ ] **Step 4: Create the detail page**

Create `frontend/app/(shell)/apps/documents/d/[documentId]/page.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { IconAlertTriangle, IconFileText } from "@tabler/icons-react";
import { getDocument, getOriginalFileUrl } from "@/lib/api/documents";
import type { DocumentDetail } from "@/lib/api/types";
import styles from "./page.module.css";

const POLL_INTERVAL_MS = 5000;
const PENDING_STATUSES = new Set(["queued", "processing"]);

const STATUS_LABEL: Record<DocumentDetail["status"], string> = {
  queued: "Wartet in der Warteschlange…",
  processing: "Wird verarbeitet…",
  ready: "Bereit",
  failed: "Fehlgeschlagen",
};

const TYPE_LABEL: Record<string, string> = {
  pdf_digital: "Digitales PDF",
  pdf_scanned: "Gescanntes PDF",
  image: "Bild",
};

export default function DocumentPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const { data: session } = useSession();
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const [originalBlobUrl, setOriginalBlobUrl] = useState<string | null>(null);

  function refresh() {
    if (!session?.accessToken) return;
    getDocument(session.accessToken, documentId)
      .then((result) => {
        setDocument(result);
        setLoadError(false);
      })
      .catch(() => setLoadError(true));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(refresh, [session?.accessToken, documentId]);

  useEffect(() => {
    if (!document || !PENDING_STATUSES.has(document.status)) return;
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document, session?.accessToken, documentId]);

  // Owner-only original-file bytes require the bearer token, which an
  // <img src>/<a href> cannot attach -- fetch client-side and render via an
  // object URL instead of a bare getOriginalFileUrl(...) reference.
  useEffect(() => {
    if (!showOriginal || !session?.accessToken || !document) return;
    let objectUrl: string | null = null;
    fetch(getOriginalFileUrl(document.id), {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then((response) => response.blob())
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setOriginalBlobUrl(objectUrl);
      });
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [showOriginal, session?.accessToken, document]);

  if (loadError) {
    return (
      <div className={styles.error} role="alert">
        Document could not be loaded.
        <button type="button" onClick={refresh}>Try again</button>
      </div>
    );
  }

  if (!document) return null;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <IconFileText size={22} />
        <h1>{document.filename}</h1>
        {document.document_type && (
          <span className={styles.typeBadge}>{TYPE_LABEL[document.document_type] ?? document.document_type}</span>
        )}
      </header>

      <dl className={styles.meta}>
        <div><dt>Status</dt><dd>{STATUS_LABEL[document.status]}</dd></div>
        {document.page_count != null && <div><dt>Seiten</dt><dd>{document.page_count}</dd></div>}
        <div><dt>Hochgeladen</dt><dd>{new Date(document.created_at).toLocaleString("de-DE")}</dd></div>
      </dl>

      {document.status === "failed" && (
        <p className={styles.failedMessage}>{document.error_message}</p>
      )}

      {document.status === "ready" && document.sanitized_markdown && (
        <article className={styles.markdown}>
          {/* A real Markdown renderer (e.g. a dependency already used elsewhere
              in the frontend, or a minimal wrapper) belongs here -- this plan
              deliberately does not introduce a new dependency choice; render
              the raw string in a <pre> until one is selected, matching what
              the test above asserts (substring presence, not markup shape). */}
          <pre className={styles.markdownBody}>{document.sanitized_markdown}</pre>
        </article>
      )}

      <button
        type="button"
        className={styles.originalToggle}
        onClick={() => setShowOriginal((current) => !current)}
      >
        {showOriginal ? "Original ausblenden" : "Original anzeigen"}
      </button>

      {showOriginal && (
        <div className={styles.originalPanel}>
          <div className={styles.warningBanner} role="alert">
            <IconAlertTriangle size={16} />
            Diese Datei wurde nicht anonymisiert — nicht weitergeben.
          </div>
          {originalBlobUrl && document.content_type.startsWith("image/") && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={originalBlobUrl} alt={`Original: ${document.filename}`} />
          )}
          {originalBlobUrl && !document.content_type.startsWith("image/") && (
            <a href={originalBlobUrl} target="_blank" rel="noreferrer">
              Original in neuem Tab öffnen
            </a>
          )}
        </div>
      )}
    </div>
  );
}
```

Create `frontend/app/(shell)/apps/documents/d/[documentId]/page.module.css` with reasonable structural classes (`.page`, `.header`, `.typeBadge`, `.meta`, `.failedMessage`, `.markdown`, `.markdownBody`, `.originalToggle`, `.originalPanel`, `.warningBanner`, `.error`) — follow the visual language of `frontend/app/(shell)/apps/anonymization/c/[conversationId]/page.module.css` (spacing scale, `var(--color-*)` tokens, `var(--radius-sm)`), with `.warningBanner` styled prominently (e.g. `background: var(--color-error-bg); border: 1px solid var(--color-error-border); color: var(--color-error-text);`) since it must never look dismissable or secondary.

- [ ] **Step 5: Run to verify it passes, then full frontend suite + lint**

Run: `cd frontend && npm test && npm run lint`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add "frontend/app/(shell)/apps/documents"
git commit -m "feat: add the documents app route tree (layout, empty state, detail page)"
```

---

### Task 15: `Composer.tsx` — document attach picker

**Files:**
- Modify: `frontend/lib/api/chat.ts`
- Modify: `frontend/lib/api/chat.test.ts`
- Modify: `frontend/components/Composer.tsx`
- Modify: `frontend/components/Composer.module.css`
- Modify: `frontend/components/Composer.test.tsx`
- Modify: `frontend/app/(shell)/apps/anonymization/c/[conversationId]/page.tsx`

**Interfaces:**
- Consumes: `useAvailableApps()` (existing `AvailableAppsProvider`), `listDocuments` (Task 12).
- Produces: `Composer({ onSend: (content: string, documentIds: string[], documentFilenames: string[]) => void, disabled })` (three-argument `onSend` from the start — `documentFilenames` lets Task 16 show a chip on the optimistic message without re-deriving filenames from ids); `sendMessage(..., documentIds?: string[])` gaining a 5th argument.

- [ ] **Step 1: Extend `sendMessage` in `lib/api/chat.ts` to accept `documentIds`**

This is a small, mechanical addition ahead of the UI work: `sendMessage`'s request body needs `document_ids` threaded through when non-empty, matching backend `SendMessageIn.document_ids` (Task 8). Add to `frontend/lib/api/chat.test.ts` a case asserting the POST body includes `document_ids` when passed, and asserting it's omitted (or empty) when not passed — then extend `sendMessage`'s signature and its `JSON.stringify` body construction in `frontend/lib/api/chat.ts` accordingly, following the file's existing conventions (accessToken-first, `ChatApiError` on non-ok).

- [ ] **Step 2: Write the failing Composer test**

Add to `frontend/components/Composer.test.tsx` (create if it doesn't already exist, following the existing mocking pattern used by `ConversationSidebar.test.tsx`/other component tests: `vi.mock("next-auth/react")`, `vi.mock("@/components/AvailableAppsProvider")`):

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Mock } from "vitest";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
}));

vi.mock("@/components/AvailableAppsProvider", () => ({
  useAvailableApps: vi.fn(),
}));

import * as documentsApi from "@/lib/api/documents";
import { useAvailableApps } from "@/components/AvailableAppsProvider";
import { Composer } from "./Composer";

describe("Composer", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    (useAvailableApps as unknown as Mock).mockReturnValue({ apps: [], loading: false, error: false });
  });

  it("does not render an attach control when the documents app is not entitled", () => {
    render(<Composer onSend={vi.fn()} disabled={false} />);
    expect(screen.queryByLabelText(/dokument anhängen/i)).not.toBeInTheDocument();
  });

  it("renders an attach control when the documents app is entitled", async () => {
    (useAvailableApps as unknown as Mock).mockReturnValue({
      apps: [{ key: "documents", name: "Dokumente", description: null }], loading: false, error: false,
    });
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([]);
    render(<Composer onSend={vi.fn()} disabled={false} />);
    expect(screen.getByLabelText(/dokument anhängen/i)).toBeInTheDocument();
  });

  it("opening the picker lists only ready documents", async () => {
    (useAvailableApps as unknown as Mock).mockReturnValue({
      apps: [{ key: "documents", name: "Dokumente", description: null }], loading: false, error: false,
    });
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([
      { id: "1", filename: "befund.pdf", status: "ready", content_type: "application/pdf",
        document_type: "pdf_digital", error_message: null, page_count: 1, byte_size: 1,
        created_at: "x", updated_at: "x" },
      { id: "2", filename: "still-processing.pdf", status: "processing", content_type: "application/pdf",
        document_type: null, error_message: null, page_count: null, byte_size: 1,
        created_at: "x", updated_at: "x" },
    ]);

    render(<Composer onSend={vi.fn()} disabled={false} />);

    const attachButton = await screen.findByLabelText(/dokument anhängen/i);
    const user = userEvent.setup();
    await user.click(attachButton);

    // Only the ready document appears in the picker -- the processing one is filtered out.
    expect(await screen.findByText("befund.pdf")).toBeInTheDocument();
    expect(screen.queryByText("still-processing.pdf")).not.toBeInTheDocument();
  });

  it("calls onSend with the selected documentIds/filenames and renders a removable chip", async () => {
    (useAvailableApps as unknown as Mock).mockReturnValue({
      apps: [{ key: "documents", name: "Dokumente", description: null }], loading: false, error: false,
    });
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([
      { id: "1", filename: "befund.pdf", status: "ready", content_type: "application/pdf",
        document_type: "pdf_digital", error_message: null, page_count: 1, byte_size: 1,
        created_at: "x", updated_at: "x" },
    ]);
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    await user.click(await screen.findByLabelText(/dokument anhängen/i));
    await user.click(await screen.findByText("befund.pdf"));
    expect(await screen.findByText("befund.pdf", { selector: "[data-chip]" })).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Nachricht eingeben…"), "Fasse zusammen");
    await user.click(screen.getByLabelText("Senden"));

    expect(onSend).toHaveBeenCalledWith("Fasse zusammen", ["1"], ["befund.pdf"]);
  });

  it("removing a chip drops it from the next onSend call", async () => {
    (useAvailableApps as unknown as Mock).mockReturnValue({
      apps: [{ key: "documents", name: "Dokumente", description: null }], loading: false, error: false,
    });
    vi.spyOn(documentsApi, "listDocuments").mockResolvedValue([
      { id: "1", filename: "befund.pdf", status: "ready", content_type: "application/pdf",
        document_type: "pdf_digital", error_message: null, page_count: 1, byte_size: 1,
        created_at: "x", updated_at: "x" },
    ]);
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    await user.click(await screen.findByLabelText(/dokument anhängen/i));
    await user.click(await screen.findByText("befund.pdf"));
    await user.click(screen.getByLabelText(/entfernen: befund.pdf/i));
    await user.type(screen.getByPlaceholderText("Nachricht eingeben…"), "Hallo");
    await user.click(screen.getByLabelText("Senden"));

    expect(onSend).toHaveBeenCalledWith("Hallo", [], []);
  });
});
```

Run: `cd frontend && npx vitest run components/Composer.test.tsx` → FAIL (`onSend` still called with one argument, no attach control exists).

- [ ] **Step 3: Extend `Composer.tsx`**

Edit `frontend/components/Composer.tsx`:

```typescript
"use client";

import { useEffect, useState } from "react";
import type { KeyboardEvent } from "react";
import { useSession } from "next-auth/react";
import { IconFileText, IconSend2, IconX } from "@tabler/icons-react";
import { useAvailableApps } from "@/components/AvailableAppsProvider";
import { listDocuments } from "@/lib/api/documents";
import type { DocumentSummary } from "@/lib/api/types";
import styles from "./Composer.module.css";

export function Composer({
  onSend,
  disabled,
}: {
  onSend: (content: string, documentIds: string[], documentFilenames: string[]) => void;
  disabled: boolean;
}) {
  const { data: session } = useSession();
  const { apps } = useAvailableApps();
  const documentsEntitled = apps.some((app) => app.key === "documents");
  const [value, setValue] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [readyDocuments, setReadyDocuments] = useState<DocumentSummary[]>([]);
  const [selected, setSelected] = useState<DocumentSummary[]>([]);

  useEffect(() => {
    if (!documentsEntitled || !pickerOpen || !session?.accessToken) return;
    listDocuments(session.accessToken)
      .then((docs) => setReadyDocuments(docs.filter((d) => d.status === "ready")))
      .catch(() => setReadyDocuments([]));
  }, [documentsEntitled, pickerOpen, session?.accessToken]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed, selected.map((d) => d.id), selected.map((d) => d.filename));
    setValue("");
    setSelected([]);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className={styles.composerWrapper}>
      {selected.length > 0 && (
        <div className={styles.chipRow}>
          {selected.map((doc) => (
            <span key={doc.id} className={styles.chip} data-chip>
              {doc.filename}
              <button
                type="button"
                aria-label={`Entfernen: ${doc.filename}`}
                onClick={() => setSelected((current) => current.filter((d) => d.id !== doc.id))}
              >
                <IconX size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {pickerOpen && (
        <div className={styles.picker}>
          {readyDocuments.length === 0 && <span className={styles.pickerEmpty}>Keine bereiten Dokumente.</span>}
          {readyDocuments.map((doc) => (
            <button
              key={doc.id}
              type="button"
              className={styles.pickerItem}
              onClick={() => {
                setSelected((current) => (current.some((d) => d.id === doc.id) ? current : [...current, doc]));
                setPickerOpen(false);
              }}
            >
              {doc.filename}
            </button>
          ))}
        </div>
      )}

      <div className={styles.composer}>
        {documentsEntitled && (
          <button
            type="button"
            aria-label="Dokument anhängen"
            className={styles.attachButton}
            disabled={disabled}
            onClick={() => setPickerOpen((current) => !current)}
          >
            <IconFileText size={18} />
          </button>
        )}
        <textarea
          className={styles.textarea}
          placeholder="Nachricht eingeben…"
          value={value}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className={styles.sendButton}
          disabled={disabled || !value.trim()}
          onClick={submit}
          aria-label="Senden"
        >
          <IconSend2 size={18} />
        </button>
      </div>
    </div>
  );
}
```

Add matching classes to `frontend/components/Composer.module.css` (`.composerWrapper`, `.chipRow`, `.chip`, `.picker`, `.pickerEmpty`, `.pickerItem`, `.attachButton`) — follow the existing `.composer`/`.textarea`/`.sendButton` visual language (same border/radius/color tokens); `.attachButton` styled like a smaller, secondary sibling of `.sendButton`.

- [ ] **Step 4: Update the one call site**

Edit `frontend/app/(shell)/apps/anonymization/c/[conversationId]/page.tsx`:

- `handleSend(content: string)` → `handleSend(content: string, documentIds: string[] = [], documentFilenames: string[] = [])`.
- Its `sendMessage(...)` call gains the 5th argument (`documentIds`), from Task 15 Step 1's extension.
- The two retry call sites (`onRetry={() => handleSend(item.retryContent)}`) stay as-is — they already satisfy the new default-`[]`/`[]` signature, so retried messages never re-attach documents (acceptable: `retryContent` never carried document identity to begin with, matching the "content-only" `DisplayItem.retryContent` shape already in this file).
- `<Composer onSend={handleSend} disabled={sending} />` — unchanged at the call site; `handleSend`'s new signature satisfies `Composer`'s new `onSend` type automatically.

- [ ] **Step 5: Run to verify everything passes, then full frontend suite + lint**

Run: `cd frontend && npm test && npm run lint`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/api/chat.ts frontend/lib/api/chat.test.ts \
  frontend/components/Composer.tsx frontend/components/Composer.module.css \
  frontend/components/Composer.test.tsx \
  "frontend/app/(shell)/apps/anonymization/c/[conversationId]/page.tsx"
git commit -m "feat: add document attach picker to Composer and thread documentIds through sendMessage"
```

---

### Task 16: `MessageBubble.tsx` — optimistic pending-document chip

**Files:**
- Modify: `frontend/components/MessageBubble.tsx`
- Modify: `frontend/components/MessageBubble.module.css`
- Modify: `frontend/components/MessageBubble.test.tsx`
- Modify: `frontend/app/(shell)/apps/anonymization/c/[conversationId]/page.tsx`

**Interfaces:**
- Consumes: nothing new from earlier tasks — this is local plumbing on the existing synthetic `pending-${Date.now()}` message object built in `handleSend`.
- Produces: `MessageBubble({ ..., pendingDocuments?: { filename: string }[] })`.

- [ ] **Step 1: Write the failing test**

Add to `frontend/components/MessageBubble.test.tsx`:

```typescript
it("shows a document chip on a message when pendingDocuments is provided", () => {
  render(
    <MessageBubble
      message={{ id: "pending-1", role: "user", content: "Fasse zusammen", created_at: "" }}
      pendingDocuments={[{ filename: "befund.pdf" }]}
    />
  );
  expect(screen.getByText("befund.pdf")).toBeInTheDocument();
});

it("renders no chip when pendingDocuments is omitted", () => {
  render(<MessageBubble message={{ id: "m1", role: "user", content: "Hallo", created_at: "" }} />);
  expect(screen.queryByText(/\.pdf$/)).not.toBeInTheDocument();
});
```

Run: `cd frontend && npx vitest run components/MessageBubble.test.tsx` → FAIL (`pendingDocuments` prop unused, chip not rendered).

- [ ] **Step 2: Extend `MessageBubble.tsx`**

Add `pendingDocuments?: { filename: string }[]` to `MessageBubbleProps` and render it only inside the `message` branch, only when present:

```typescript
interface MessageBubbleProps {
  message?: MessageOut;
  error?: MessageErrorProps;
  aborted?: MessageAbortedProps;
  streaming?: boolean;
  /** Filenames attached at send time on the synthetic optimistic user bubble
   * only -- MessageOut (the persisted shape) carries no attachment metadata
   * in this slice (spec §1: not a persisted message-level attachment record),
   * so a reload's real message shows the delimited document text inline as
   * plain prose instead, with no chip. */
  pendingDocuments?: { filename: string }[];
  onRetry?: () => void;
}

export function MessageBubble({ message, error, aborted, streaming, pendingDocuments, onRetry }: MessageBubbleProps) {
  // ... error/aborted branches unchanged ...

  if (!message) return null;

  const isUser = message.role === "user";
  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : styles.rowAssistant}`}>
      <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAssistant}`}>
        {pendingDocuments && pendingDocuments.length > 0 && (
          <div className={styles.documentChips}>
            {pendingDocuments.map((doc) => (
              <span key={doc.filename} className={styles.documentChip}>
                📄 {doc.filename}
              </span>
            ))}
          </div>
        )}
        {message.content}
        {streaming && <span className={styles.cursor} aria-hidden="true" />}
      </div>
    </div>
  );
}
```

Add `.documentChips`/`.documentChip` to `MessageBubble.module.css` (small pill, consistent with `.bubbleUser`'s color scheme).

- [ ] **Step 3: Run to verify it passes**

Run: `cd frontend && npx vitest run components/MessageBubble.test.tsx` → PASS.

- [ ] **Step 4: Wire it from `handleSend`**

Edit `frontend/app/(shell)/apps/anonymization/c/[conversationId]/page.tsx`'s `handleSend` (now `handleSend(content, documentIds = [], documentFilenames = [])` from Task 15 Step 4):

```typescript
async function handleSend(content: string, documentIds: string[] = [], documentFilenames: string[] = []) {
  if (!session?.accessToken) return;
  setItems((current) => [
    ...current,
    {
      kind: "message",
      message: { id: `pending-${Date.now()}`, role: "user", content, created_at: "" },
      pendingDocuments: documentFilenames.map((filename) => ({ filename })),
    },
  ]);
  // ... rest unchanged, sendMessage(..., documentIds) as wired in Task 15 ...
}
```

`DisplayItem`'s `{ kind: "message"; message: MessageOut }` variant gains an optional `pendingDocuments?: { filename: string }[]` field, and the render branch passes it through: `<MessageBubble key={item.message.id} message={item.message} pendingDocuments={item.pendingDocuments} />`.

- [ ] **Step 5: Run the full frontend suite + lint**

Run: `cd frontend && npm test && npm run lint`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/MessageBubble.tsx frontend/components/MessageBubble.module.css \
  frontend/components/MessageBubble.test.tsx frontend/components/Composer.tsx \
  frontend/components/Composer.test.tsx \
  "frontend/app/(shell)/apps/anonymization/c/[conversationId]/page.tsx"
git commit -m "feat: show an optimistic document chip on the pending chat message"
```

---

## What this plan does not cover

Per spec §1, explicitly out of scope for this slice (Phase 2 candidates):

- Anonymized PDF/DOCX export (returning a redacted PDF/DOCX rather than markdown).
- Pixel-level image redaction (blacking out a name directly on x-ray/scan pixels).
- Manual/search-based redaction UI.
- Rich table reconstruction and multi-column reading-order detection.
- Cross-document/patient-level linking.
- Tenant-wide document sharing/visibility.
- A persisted message-level attachment record (attached document content is folded into the message's `content`/`sanitized_content`, not tracked separately).
- SSE/real-time push for processing status — polling only, as implemented.
- The full conversation-history context-window trimming design (referenced but not yet implemented anywhere in this codebase) — Task 10 adds only a minimal, explicit character-count guard scoped to the document-attach path as a stand-in, not a reuse of that design.

## Verification

1. `cd backend && pytest tests/unit tests/integration tests/privacy_invariants` — all new and existing tests pass; `lint-imports` confirms all three import-linter contracts (including the new `document_gateway` one) hold.
2. `docker build -t chatgpt-proxy-backend ./backend` — builds cleanly with Tesseract (`deu` pack) and poppler baked in.
3. `alembic upgrade head` against a fresh dev DB — no new migrations in this plan; confirms migrations 0008/0009 (already merged) still apply cleanly ahead of this plan's code.
4. Manual, via `docker compose up`: grant a dev tenant the `"documents"` entitlement via `ops_admin`; upload a scanned image containing a fictitious name; confirm it reaches `status="ready"` with the name replaced by a token in the markdown preview; confirm the original-file view shows the permanent warning banner; attach the document into a chat message and confirm the assistant's context includes the document content per the streamed response.
5. `cd frontend && npm test && npm run lint` — all new/updated Vitest suites pass; manual check that the "attach document" control in `Composer` is absent for a tenant without the `"documents"` entitlement.

### Critical Files for Implementation
- `backend/app/document_gateway/pipeline.py`
- `backend/app/api/documents.py`
- `backend/app/api/chat.py`
- `backend/app/privacy_gateway/token_vault/vault.py`
- `frontend/components/Composer.tsx`
- `frontend/components/DocumentSidebar.tsx`
- `frontend/app/(shell)/apps/documents/d/[documentId]/page.tsx`
