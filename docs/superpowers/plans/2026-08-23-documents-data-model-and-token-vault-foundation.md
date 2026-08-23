# Documents Data Model & Token Vault Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `documents` table and generalize the privacy pipeline's token-vault
scoping from conversation-only to `(scope_type, scope_id)`, so document text can be
pseudonymized under the same security boundary conversations already use — the
foundation the rest of the Documents app (Phase 1) builds on.

**Architecture:** A new `documents` table (RLS-protected, same pattern as
`conversations`) plus a `DocumentRepository`. `TokenMapping` gains a `scope_type`
discriminator (`"conversation"` | `"document"`) with `conversation_id` made nullable
and a new nullable `document_id`, enforced by a `CHECK` constraint and two partial
unique indexes. `TokenVault`, `Pseudonymizer`, `OutputGuard`, and `Pipeline` all
change their scoping parameter from a bare `conversation_id` to
`(scope_type, scope_id)`, with the AES-GCM AAD binding `scope_type` into the
ciphertext. Every existing call site (production and test) is updated in the same
change — this plan does not leave the codebase in a state where old- and
new-signature calls coexist.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Postgres (RLS),
`cryptography` (AES-GCM), pytest.

**Spec:** `docs/superpowers/specs/2026-08-23-documents-app-phase1-design.md`
(§4.2 "TokenMapping scope generalization" and §4.1 "documents table" specifically;
this plan implements exactly those two subsections plus the reuse points in §3 that
depend on them).

## Global Constraints

- Every SQL migration follows the exact RLS policy expression already used in
  migration `0007` (`nullif(current_setting('app.current_tenant_id', true), '')::uuid`
  in both `USING` and `WITH CHECK`) — `tests/privacy_invariants/test_rls.py` asserts
  byte-for-byte identical policy expressions across every tenant-scoped table and
  will fail if this plan's migration deviates.
- IDs are generated app-side via `uuid.uuid4()`, never `gen_random_uuid()` — matches
  every existing table (see migration `0007`'s docstring).
- No `--no-verify`, no skipping tests. Run the full backend test suite
  (`pytest tests/unit tests/integration tests/privacy_invariants`) at the end of
  every task, not just the new tests — this plan changes a shared internal
  signature (`Pipeline.sanitize`/`deanonymize`) that dozens of existing tests call
  directly, and a partial update would leave the suite red.
- Never log or include raw PII/extracted text in an exception message, log line, or
  error column — every existing file this plan touches already follows this
  discipline (see `Pipeline.sanitize`'s docstring); preserve it in every edit.

---

### Task 1: `documents` table, `Document` model, `DocumentRepository`, migration 0008

**Files:**
- Create: `backend/app/models/document.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/db/repositories/document_repository.py`
- Create: `backend/alembic/versions/0008_documents_table_and_catalog_entry.py`
- Modify: `backend/tests/unit/test_models.py`
- Create: `backend/tests/integration/test_document_repository.py`

**Interfaces:**
- Produces: `Document` model (`backend/app/models/document.py`) with columns
  `id, tenant_id, user_id, filename, content_type, document_type, byte_size,
  status, error_message, page_count, raw_storage_path, structured_blocks,
  sanitized_markdown, created_at, updated_at, deleted_at`. `DocumentRepository`
  methods: `create(tenant_id, user_id, filename, content_type, byte_size,
  raw_storage_path) -> Document`, `get(tenant_id, document_id) -> Document | None`,
  `list_for_user(tenant_id, user_id) -> list[Document]`, `set_status(tenant_id,
  document_id, status, error_message=None) -> None`, `set_result(tenant_id,
  document_id, structured_blocks, sanitized_markdown, page_count, document_type)
  -> None`, `soft_delete(tenant_id, document_id) -> None`. Both are consumed by
  Task 2 (the `documents` FK target) and by later plans' API layer.

- [ ] **Step 1: Write the `Document` model**

Create `backend/app/models/document.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"
    # See migration 0004's rationale, applied here identically: FK checks bypass
    # RLS, so a single-column FK on user_id would accept a user row owned by
    # another tenant. uq_documents_tenant_id_id exists so this table can itself be
    # the target of a composite FK from token_mappings (see migration 0009).
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_documents_tenant_user",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String, nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_storage_path: Mapped[str] = mapped_column(String, nullable=False)
    structured_blocks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sanitized_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Register the model**

Edit `backend/app/models/__init__.py` — add the import alphabetically after
`Conversation` and add `"Document"` to `__all__` in the same position:

```python
from app.models.app import App
from app.models.audit_event import AuditEvent
from app.models.base import Base
from app.models.branch import Branch
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.llm_request import LLMRequest
from app.models.message import Message
from app.models.tenant import Tenant
from app.models.tenant_app_assignment import TenantAppAssignment
from app.models.tenant_app_entitlement import TenantAppEntitlement
from app.models.tenant_key import TenantKey
from app.models.tenant_role_permission import TenantRolePermission
from app.models.token_mapping import TokenMapping
from app.models.user import User

__all__ = [
    "App",
    "AuditEvent",
    "Base",
    "Branch",
    "Conversation",
    "Document",
    "LLMRequest",
    "Message",
    "Tenant",
    "TenantAppAssignment",
    "TenantAppEntitlement",
    "TenantKey",
    "TenantRolePermission",
    "TokenMapping",
    "User",
]
```

- [ ] **Step 3: Update the model-metadata unit test (write the failing assertions first)**

Edit `backend/tests/unit/test_models.py` — add `"documents"` to both sets:

```python
from app.models import Base


def test_all_tables_registered():
    expected = {
        "tenants",
        "users",
        "branches",
        "tenant_role_permissions",
        "conversations",
        "documents",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
        "apps",
        "tenant_app_entitlements",
        "tenant_app_assignments",
    }
    assert set(Base.metadata.tables.keys()) == expected


def test_tenant_scoped_tables_have_tenant_id_column():
    tenant_scoped = {
        "users",
        "branches",
        "tenant_role_permissions",
        "conversations",
        "documents",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
        "tenant_app_entitlements",
        "tenant_app_assignments",
    }
    for table_name in tenant_scoped:
        table = Base.metadata.tables[table_name]
        assert "tenant_id" in table.columns, f"{table_name} missing tenant_id"
```

Leave `test_token_mappings_unique_scope_constraint` (the third test in this file)
untouched for now — Task 2 replaces it, since it asserts a constraint this task
does not yet change.

- [ ] **Step 4: Run the unit test to verify it fails**

Run: `cd backend && pytest tests/unit/test_models.py -v`
Expected: `test_all_tables_registered` and `test_tenant_scoped_tables_have_tenant_id_column`
FAIL — `Base.metadata.tables` does not yet contain `"documents"` (no migration has
run; the ORM model alone is enough for this test since it reads `Base.metadata`,
not the live database).

Actually — since this test reads Python-side `Base.metadata`, not the database, it
will PASS as soon as Step 1-2 are done, before any migration runs. Run it now and
confirm it passes; the true regression check for the migration is Step 8 below.

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0008_documents_table_and_catalog_entry.py`:

```python
"""add documents table and the documents app catalog entry

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-23

Adds the `documents` table (Phase 1 of the Documents app -- upload, OCR,
markdown conversion, text anonymization) and seeds its app-catalog row.

Deliberately does NOT backfill tenant_app_entitlements/tenant_app_assignments
for existing tenants, unlike migration 0007's anonymization backfill: this is
a new, separately-sold entitlement, not a base capability every tenant already
has -- ops grants it per tenant explicitly via the Django admin. Do not "fix"
this by copying 0007's backfill loop.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOCUMENTS_APP_ID = uuid.uuid4()


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("document_type", sa.String(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("raw_storage_path", sa.String(), nullable=False),
        sa.Column("structured_blocks", postgresql.JSONB(), nullable=True),
        sa.Column("sanitized_markdown", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], name="fk_documents_tenant_user"
        ),
    )

    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON documents
        USING (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_runtime")

    # Seed the catalog row only -- no entitlement/assignment backfill (see module
    # docstring). Ops grants tenant_app_entitlements per tenant explicitly; a
    # tenant's own admin then enables tenant_app_assignments the same way any
    # other app is turned on today (frontend/app/(shell)/admin/apps).
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO apps (id, key, name, description, is_active) "
            "VALUES (:id, :key, :name, :description, true)"
        ),
        {
            "id": DOCUMENTS_APP_ID,
            "key": "documents",
            "name": "Dokumente",
            "description": "Dokumenten-Upload, OCR und Anonymisierung von PDFs und Bildern.",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM tenant_app_assignments WHERE app_id = (SELECT id FROM apps WHERE key = 'documents')"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM tenant_app_entitlements WHERE app_id = (SELECT id FROM apps WHERE key = 'documents')"
        )
    )
    bind.execute(sa.text("DELETE FROM apps WHERE key = 'documents'"))

    op.execute("REVOKE ALL PRIVILEGES ON documents FROM app_runtime")
    op.execute("DROP POLICY tenant_isolation ON documents")
    op.execute("ALTER TABLE documents NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents DISABLE ROW LEVEL SECURITY")
    op.drop_table("documents")
```

- [ ] **Step 6: Apply the migration**

Run: `cd backend && alembic upgrade head`
Expected: applies cleanly, no errors. Verify with
`psql "$DATABASE_URL" -c "\d+ documents"` (or the project's equivalent local psql
invocation) that `Force Row Security: true` is shown.

- [ ] **Step 7: Write the `DocumentRepository`**

Create `backend/app/db/repositories/document_repository.py`:

```python
import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import Document


class DocumentRepository(BaseRepository):
    def create(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        filename: str,
        content_type: str,
        byte_size: int,
        raw_storage_path: str,
    ) -> Document:
        document = Document(
            tenant_id=tenant_id,
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            byte_size=byte_size,
            raw_storage_path=raw_storage_path,
            status="queued",
        )
        self.session.add(document)
        self.session.flush()
        return document

    def get(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> Document | None:
        stmt = sa.select(Document).where(
            Document.tenant_id == tenant_id,
            Document.id == document_id,
            Document.deleted_at.is_(None),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Document]:
        stmt = (
            sa.select(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.user_id == user_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def set_status(
        self,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        status: str,
        error_message: str | None = None,
    ) -> None:
        stmt = (
            sa.update(Document)
            .where(Document.tenant_id == tenant_id, Document.id == document_id)
            .values(status=status, error_message=error_message, updated_at=sa.func.now())
        )
        self.session.execute(stmt)

    def set_result(
        self,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        structured_blocks: list,
        sanitized_markdown: str,
        page_count: int,
        document_type: str,
    ) -> None:
        stmt = (
            sa.update(Document)
            .where(Document.tenant_id == tenant_id, Document.id == document_id)
            .values(
                status="ready",
                structured_blocks=structured_blocks,
                sanitized_markdown=sanitized_markdown,
                page_count=page_count,
                document_type=document_type,
                updated_at=sa.func.now(),
            )
        )
        self.session.execute(stmt)

    def soft_delete(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        stmt = (
            sa.update(Document)
            .where(Document.tenant_id == tenant_id, Document.id == document_id)
            .values(deleted_at=sa.func.now())
        )
        self.session.execute(stmt)
```

- [ ] **Step 8: Write the repository integration test**

Create `backend/tests/integration/test_document_repository.py`:

```python
import uuid

import pytest
import sqlalchemy as sa

from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _create_tenant_and_user(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="doc@example.com", role="doctor"
        )
        user_id = user.id

    return tenant_id, user_id


def test_create_and_get_round_trip(key_provider):
    tenant_id, user_id = _create_tenant_and_user(key_provider)

    with tenant_scoped_session(tenant_id) as session:
        document = DocumentRepository(session).create(
            tenant_id,
            user_id,
            filename="scan.pdf",
            content_type="application/pdf",
            byte_size=1024,
            raw_storage_path=f"/data/documents/{tenant_id}/x/original.pdf",
        )
        document_id = document.id
        assert document.status == "queued"

    with tenant_scoped_session(tenant_id) as session:
        found = DocumentRepository(session).get(tenant_id, document_id)
        assert found is not None
        assert found.filename == "scan.pdf"
        assert found.status == "queued"


def test_get_returns_none_for_another_tenants_document(key_provider):
    tenant_a, user_a = _create_tenant_and_user(key_provider)
    tenant_b, _ = _create_tenant_and_user(key_provider)

    with tenant_scoped_session(tenant_a) as session:
        document_id = DocumentRepository(session).create(
            tenant_a,
            user_a,
            filename="a.pdf",
            content_type="application/pdf",
            byte_size=10,
            raw_storage_path="/data/documents/a/x/original.pdf",
        ).id

    with tenant_scoped_session(tenant_b) as session:
        assert DocumentRepository(session).get(tenant_a, document_id) is None


def test_list_for_user_excludes_other_users_and_soft_deleted(key_provider):
    tenant_id, user_a = _create_tenant_and_user(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        user_b = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="other@example.com", role="doctor"
        ).id

    with tenant_scoped_session(tenant_id) as session:
        repo = DocumentRepository(session)
        doc_a = repo.create(
            tenant_id, user_a, filename="mine.pdf", content_type="application/pdf",
            byte_size=10, raw_storage_path="/data/documents/x/a/original.pdf",
        ).id
        repo.create(
            tenant_id, user_b, filename="theirs.pdf", content_type="application/pdf",
            byte_size=10, raw_storage_path="/data/documents/x/b/original.pdf",
        )
        deleted_doc = repo.create(
            tenant_id, user_a, filename="deleted.pdf", content_type="application/pdf",
            byte_size=10, raw_storage_path="/data/documents/x/c/original.pdf",
        ).id
        repo.soft_delete(tenant_id, deleted_doc)

    with tenant_scoped_session(tenant_id) as session:
        visible = DocumentRepository(session).list_for_user(tenant_id, user_a)
        assert [d.id for d in visible] == [doc_a]


def test_set_status_and_set_result(key_provider):
    tenant_id, user_id = _create_tenant_and_user(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        document_id = DocumentRepository(session).create(
            tenant_id, user_id, filename="scan.png", content_type="image/png",
            byte_size=10, raw_storage_path="/data/documents/x/y/original.png",
        ).id

    with tenant_scoped_session(tenant_id) as session:
        DocumentRepository(session).set_status(tenant_id, document_id, "processing")

    with tenant_scoped_session(tenant_id) as session:
        assert DocumentRepository(session).get(tenant_id, document_id).status == "processing"

    blocks = [
        {
            "page": 1,
            "block_type": "paragraph",
            "order": 0,
            "sanitized_text": "Hallo PATIENT_ABCDE12345",
            "source_bbox": None,
        }
    ]
    with tenant_scoped_session(tenant_id) as session:
        DocumentRepository(session).set_result(
            tenant_id,
            document_id,
            structured_blocks=blocks,
            sanitized_markdown="Hallo PATIENT_ABCDE12345",
            page_count=1,
            document_type="image",
        )

    with tenant_scoped_session(tenant_id) as session:
        document = DocumentRepository(session).get(tenant_id, document_id)
        assert document.status == "ready"
        assert document.structured_blocks == blocks
        assert document.sanitized_markdown == "Hallo PATIENT_ABCDE12345"
        assert document.page_count == 1
        assert document.document_type == "image"


def test_documents_catalog_row_is_seeded_and_active():
    with SessionLocal() as session:
        row = session.execute(
            sa.text("SELECT key, name, is_active FROM apps WHERE key = 'documents'")
        ).one()
        assert row.key == "documents"
        assert row.is_active is True
```

- [ ] **Step 9: Run the new tests**

Run: `cd backend && pytest tests/unit/test_models.py tests/integration/test_document_repository.py -v`
Expected: all PASS.

- [ ] **Step 10: Run the full RLS invariant suite to confirm `documents` is picked up automatically**

Run: `cd backend && pytest tests/privacy_invariants/test_rls.py -v`
Expected: all PASS — `test_rls.py` derives its table list from `Base.metadata`
(any table with a `tenant_id` column, excluding `tenants`), so `documents` is
included automatically and must show RLS enabled, forced, with a policy whose
`USING`/`WITH CHECK` expression is byte-identical to every other table's. If this
fails, the migration's policy expression in Step 5 does not match `0007`'s exactly
— compare character-for-character.

- [ ] **Step 11: Commit**

```bash
git add backend/app/models/document.py backend/app/models/__init__.py \
  backend/app/db/repositories/document_repository.py \
  backend/alembic/versions/0008_documents_table_and_catalog_entry.py \
  backend/tests/unit/test_models.py backend/tests/integration/test_document_repository.py
git commit -m "feat: add documents table, Document model, and DocumentRepository"
```

---

### Task 2: Generalize `TokenMapping` to a `(scope_type, scope_id)` scope

**Files:**
- Modify: `backend/app/models/token_mapping.py`
- Create: `backend/alembic/versions/0009_token_mapping_document_scope.py`
- Modify: `backend/tests/unit/test_models.py`
- Create: `backend/tests/integration/test_token_mapping_scope_schema.py`

**Interfaces:**
- Consumes: `Document` model + `documents` table (Task 1).
- Produces: `TokenMapping` with `scope_type: str`, `conversation_id: uuid.UUID |
  None`, `document_id: uuid.UUID | None` columns, a `ck_token_mappings_scope_consistency`
  CHECK constraint, and `uq_token_mappings_conversation_scope` /
  `uq_token_mappings_document_scope` partial unique indexes. Consumed by Task 3
  (`TokenVault`).

- [ ] **Step 1: Update the `TokenMapping` model**

Edit `backend/app/models/token_mapping.py` — replace the entire file:

```python
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TokenMapping(Base):
    __tablename__ = "token_mappings"
    # Both parent references are composite (see migration 0004): FK checks bypass
    # RLS, so single-column FKs would accept a conversation, document, or DEK
    # owned by another tenant. Exactly one of conversation_id/document_id is
    # populated, selected by scope_type -- see migration 0009 and
    # docs/adr/0023-token-vault-scope-generalization.md.
    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'conversation' AND conversation_id IS NOT NULL AND document_id IS NULL) "
            "OR (scope_type = 'document' AND document_id IS NOT NULL AND conversation_id IS NULL)",
            name="ck_token_mappings_scope_consistency",
        ),
        # A plain UniqueConstraint over columns that are sometimes NULL would not
        # enforce per-scope uniqueness -- Postgres treats every NULL as distinct.
        # These partial indexes are what actually enforce "one token per scope
        # instance", independently for each scope_type.
        Index(
            "uq_token_mappings_conversation_scope",
            "tenant_id",
            "conversation_id",
            "token",
            unique=True,
            postgresql_where=text("scope_type = 'conversation'"),
        ),
        Index(
            "uq_token_mappings_document_scope",
            "tenant_id",
            "document_id",
            "token",
            unique=True,
            postgresql_where=text("scope_type = 'document'"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_token_mappings_tenant_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_token_mappings_tenant_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dek_id"],
            ["tenant_keys.tenant_id", "tenant_keys.id"],
            name="fk_token_mappings_tenant_dek",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    token: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Replace the now-obsolete unique-constraint unit test**

Edit `backend/tests/unit/test_models.py` — replace
`test_token_mappings_unique_scope_constraint` with:

```python
def test_token_mappings_scope_partial_unique_indexes():
    table = Base.metadata.tables["token_mappings"]
    index_names = {index.name for index in table.indexes}
    assert "uq_token_mappings_conversation_scope" in index_names
    assert "uq_token_mappings_document_scope" in index_names


def test_token_mappings_scope_check_constraint():
    table = Base.metadata.tables["token_mappings"]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_token_mappings_scope_consistency" in check_names
```

- [ ] **Step 3: Run the unit test to verify it fails first, then passes**

Run: `cd backend && pytest tests/unit/test_models.py -v`
Expected before Step 1 lands (if run first): FAIL (no such index/constraint names
on the old model). After Step 1: PASS. Since Step 1 is already written above, just
run it now and confirm PASS.

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0009_token_mapping_document_scope.py`:

```python
"""generalize token_mappings scope to conversation or document

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-23

Documents (migration 0008) are not conversations, so a token minted while
anonymizing a document needs a scope that isn't a conversation_id. This adds a
`scope_type` discriminator ('conversation' | 'document'), makes
`conversation_id` nullable, adds a nullable `document_id`, and replaces the
single (tenant_id, conversation_id, token) unique constraint with two partial
unique indexes -- see docs/adr/0023-token-vault-scope-generalization.md.

Existing rows are backfilled to scope_type='conversation' before the CHECK
constraint is added, since every row created before this migration is
conversation-scoped by construction.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("token_mappings", sa.Column("scope_type", sa.String(), nullable=True))
    op.execute("UPDATE token_mappings SET scope_type = 'conversation'")
    op.alter_column("token_mappings", "scope_type", nullable=False)

    op.alter_column("token_mappings", "conversation_id", nullable=True)
    op.add_column(
        "token_mappings", sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True)
    )

    op.execute(
        """
        ALTER TABLE token_mappings ADD CONSTRAINT ck_token_mappings_scope_consistency
        CHECK (
            (scope_type = 'conversation' AND conversation_id IS NOT NULL AND document_id IS NULL)
            OR
            (scope_type = 'document' AND document_id IS NOT NULL AND conversation_id IS NULL)
        )
        """
    )

    op.create_foreign_key(
        "fk_token_mappings_tenant_document",
        "token_mappings",
        "documents",
        ["tenant_id", "document_id"],
        ["tenant_id", "id"],
    )

    # Replace the single scope-agnostic unique constraint with two partial unique
    # indexes -- a plain constraint over sometimes-NULL columns would not enforce
    # per-scope uniqueness, since Postgres treats every NULL as distinct.
    op.drop_constraint("uq_token_mappings_scope", "token_mappings", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_token_mappings_conversation_scope
        ON token_mappings (tenant_id, conversation_id, token) WHERE scope_type = 'conversation'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_token_mappings_document_scope
        ON token_mappings (tenant_id, document_id, token) WHERE scope_type = 'document'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_token_mappings_document_scope")
    op.execute("DROP INDEX IF EXISTS uq_token_mappings_conversation_scope")
    op.create_unique_constraint(
        "uq_token_mappings_scope", "token_mappings", ["tenant_id", "conversation_id", "token"]
    )
    op.drop_constraint("fk_token_mappings_tenant_document", "token_mappings", type_="foreignkey")
    op.execute("ALTER TABLE token_mappings DROP CONSTRAINT ck_token_mappings_scope_consistency")
    op.drop_column("token_mappings", "document_id")
    op.alter_column("token_mappings", "conversation_id", nullable=False)
    op.drop_column("token_mappings", "scope_type")
```

- [ ] **Step 5: Apply the migration**

Run: `cd backend && alembic upgrade head`
Expected: applies cleanly.

- [ ] **Step 6: Write the schema-level integration test**

Create `backend/tests/integration/test_token_mapping_scope_schema.py`:

```python
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Conversation, TenantKey, TokenMapping, User
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _setup(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="doc@example.com", role="doctor"
        )
        document_id = DocumentRepository(session).create(
            tenant_id,
            user.id,
            filename="x.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_storage_path="/data/documents/x/y/original.pdf",
        ).id
        dek_id = session.execute(
            sa.select(TenantKey.id).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()

    return tenant_id, document_id, dek_id


def test_check_constraint_rejects_conversation_scope_with_document_id_set(key_provider):
    tenant_id, document_id, dek_id = _setup(key_provider)
    with pytest.raises(IntegrityError):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="conversation",
                    conversation_id=uuid.uuid4(),
                    document_id=document_id,
                    token="PATIENT_AAAAAAAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"x" * 28,
                    dek_id=dek_id,
                )
            )


def test_check_constraint_rejects_document_scope_with_no_document_id(key_provider):
    tenant_id, document_id, dek_id = _setup(key_provider)
    with pytest.raises(IntegrityError):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="document",
                    conversation_id=None,
                    document_id=None,
                    token="PATIENT_AAAAAAAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"x" * 28,
                    dek_id=dek_id,
                )
            )


def test_same_token_string_allowed_across_a_conversation_and_a_document_scope(key_provider):
    """The two partial unique indexes are scoped independently -- the same token
    string colliding across a conversation and a document is not a uniqueness
    violation, since resolution always requires both scope_type and scope_id."""
    tenant_id, document_id, dek_id = _setup(key_provider)
    conversation_id = uuid.uuid4()

    with tenant_scoped_session(tenant_id) as session:
        user_id = session.execute(sa.select(User.id).limit(1)).scalar_one()
        session.add(Conversation(id=conversation_id, tenant_id=tenant_id, user_id=user_id))
        session.flush()
        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="conversation",
                conversation_id=conversation_id,
                document_id=None,
                token="PATIENT_SAMETOKEN01",
                entity_type="PATIENT",
                encrypted_value=b"x" * 28,
                dek_id=dek_id,
            )
        )
        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="document",
                conversation_id=None,
                document_id=document_id,
                token="PATIENT_SAMETOKEN01",
                entity_type="PATIENT",
                encrypted_value=b"y" * 28,
                dek_id=dek_id,
            )
        )


def test_duplicate_token_within_same_document_scope_is_rejected(key_provider):
    tenant_id, document_id, dek_id = _setup(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="document",
                conversation_id=None,
                document_id=document_id,
                token="PATIENT_DUPDUP0001",
                entity_type="PATIENT",
                encrypted_value=b"x" * 28,
                dek_id=dek_id,
            )
        )

    with pytest.raises(IntegrityError):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="document",
                    conversation_id=None,
                    document_id=document_id,
                    token="PATIENT_DUPDUP0001",
                    entity_type="PATIENT",
                    encrypted_value=b"y" * 28,
                    dek_id=dek_id,
                )
            )
```

- [ ] **Step 7: Run the new tests**

Run: `cd backend && pytest tests/integration/test_token_mapping_scope_schema.py -v`
Expected: all PASS.

- [ ] **Step 8: Confirm existing `token_mappings`-touching tests are (expectedly) still broken**

Run: `cd backend && pytest tests/privacy_invariants/test_token_vault.py -v`
Expected: FAIL — `TokenVault` still calls `TokenMapping(conversation_id=..., ...)`
without `scope_type` (now a required column). This is expected and fixed in
Task 3; do not attempt to fix `vault.py` from this task.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/token_mapping.py backend/tests/unit/test_models.py \
  backend/alembic/versions/0009_token_mapping_document_scope.py \
  backend/tests/integration/test_token_mapping_scope_schema.py
git commit -m "feat: generalize token_mappings to a (scope_type, scope_id) scope"
```

---

### Task 3: Generalize `TokenVault` to `(scope_type, scope_id)`

**Files:**
- Modify: `backend/app/privacy_gateway/token_vault/vault.py`
- Modify: `backend/tests/privacy_invariants/test_token_vault.py`

**Interfaces:**
- Consumes: `TokenMapping.scope_type`/`document_id` (Task 2).
- Produces: `ScopeType = Literal["conversation", "document"]` (exported from
  `app.privacy_gateway.token_vault.vault`, imported by Task 4's `Pseudonymizer`/
  `OutputGuard`/`Pipeline`). `TokenVault.create_mapping(tenant_id, scope_type,
  scope_id, entity_type, original_value) -> str`; `resolve_token(tenant_id,
  scope_type, scope_id, token) -> str | None`; `resolve_tokens(tenant_id,
  scope_type, scope_id, tokens) -> dict[str, str]`; `delete_mapping(tenant_id,
  scope_type, scope_id, token) -> None`; `expire_mapping(tenant_id, scope_type,
  scope_id, token) -> None`.

- [ ] **Step 1: Rewrite `vault.py`**

Replace the full contents of `backend/app/privacy_gateway/token_vault/vault.py`:

```python
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Literal

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.db.session import tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import KeyProvider

_NONCE_LENGTH = 12

ScopeType = Literal["conversation", "document"]


def _associated_data(tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str) -> bytes:
    """AES-GCM associated data binding a ciphertext to the exact row it belongs to.

    The DEK is per-tenant, not per-row, so without AAD a ciphertext blob would be
    interchangeable between any two `token_mappings` rows of the same tenant —
    copying one scope's `encrypted_value` into another scope's row would still
    decrypt cleanly, defeating ADR-0009's scoping at the storage layer. Binding the
    AAD to `(tenant_id, scope_type, scope_id, token)` makes any such move fail
    with `cryptography.exceptions.InvalidTag`.

    `scope_type` is bound in addition to `scope_id` (not just `f"{scope_id}"`) so
    that a document-scoped mapping's ciphertext cannot decrypt under a
    conversation-scoped lookup even in the practically-impossible case of a
    document UUID colliding with a conversation UUID — defense in depth beyond
    what the schema's CHECK constraint and partial unique indexes already
    guarantee. See docs/adr/0023-token-vault-scope-generalization.md.
    """
    return f"{tenant_id}:{scope_type}:{scope_id}:{token}".encode()


class TokenVault:
    def __init__(self, key_provider: KeyProvider) -> None:
        self.key_provider = key_provider

    def create_mapping(
        self,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
        entity_type: str,
        original_value: str,
    ) -> str:
        token = f"{entity_type}_{secrets.token_hex(5).upper()}"

        with tenant_scoped_session(tenant_id) as session:
            dek_row = self._active_dek_row(session, tenant_id)
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = os.urandom(_NONCE_LENGTH)
            aad = _associated_data(tenant_id, scope_type, scope_id, token)
            ciphertext = AESGCM(raw_dek).encrypt(nonce, original_value.encode("utf-8"), aad)

            mapping = TokenMapping(
                tenant_id=tenant_id,
                scope_type=scope_type,
                conversation_id=scope_id if scope_type == "conversation" else None,
                document_id=scope_id if scope_type == "document" else None,
                token=token,
                entity_type=entity_type,
                encrypted_value=nonce + ciphertext,
                dek_id=dek_row.id,
            )
            session.add(mapping)

        return token

    def resolve_token(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str
    ) -> str | None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, scope_type, scope_id, token)
            if mapping is None:
                return None

            dek_row = session.get(TenantKey, mapping.dek_id)
            if dek_row is None:
                raise RuntimeError(
                    f"token_mappings row {mapping.id} references missing tenant_keys row "
                    f"{mapping.dek_id}; its encrypted_value cannot be decrypted"
                )
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = mapping.encrypted_value[:_NONCE_LENGTH]
            ciphertext = mapping.encrypted_value[_NONCE_LENGTH:]
            aad = _associated_data(tenant_id, scope_type, scope_id, token)
            # An InvalidTag here means the ciphertext does not belong to this row —
            # it must propagate, never be swallowed into a None/plaintext result.
            return AESGCM(raw_dek).decrypt(nonce, ciphertext, aad).decode("utf-8")

    def resolve_tokens(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, tokens: list[str]
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for token in tokens:
            value = self.resolve_token(tenant_id, scope_type, scope_id, token)
            if value is not None:
                resolved[token] = value
        return resolved

    def delete_mapping(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str
    ) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, scope_type, scope_id, token)
            if mapping is not None:
                session.delete(mapping)

    def expire_mapping(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str
    ) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, scope_type, scope_id, token)
            if mapping is not None:
                mapping.deleted_at = datetime.now(timezone.utc)

    def _find_mapping(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
        token: str,
    ) -> TokenMapping | None:
        now = datetime.now(timezone.utc)
        scope_column = (
            TokenMapping.conversation_id if scope_type == "conversation" else TokenMapping.document_id
        )
        stmt = sa.select(TokenMapping).where(
            TokenMapping.tenant_id == tenant_id,
            TokenMapping.scope_type == scope_type,
            scope_column == scope_id,
            TokenMapping.token == token,
            TokenMapping.deleted_at.is_(None),
            # Fail-safe by construction: an expired-but-not-yet-reaped mapping is
            # unresolvable even before any retention job (ADR-0019) exists to delete it.
            (TokenMapping.expires_at.is_(None)) | (TokenMapping.expires_at > now),
        )
        return session.execute(stmt).scalar_one_or_none()

    def _active_dek_row(self, session: Session, tenant_id: uuid.UUID) -> TenantKey:
        stmt = (
            sa.select(TenantKey)
            .where(TenantKey.tenant_id == tenant_id)
            .order_by(TenantKey.key_version.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one()
```

- [ ] **Step 2: Rewrite `test_token_vault.py`**

Replace the full contents of `backend/tests/privacy_invariants/test_token_vault.py`
— every existing test gains an explicit `"conversation"` scope-type argument
(preserving exactly what it tested before), plus new tests proving document-scope
isolation, which is the highest-value addition this task makes:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import _NONCE_LENGTH, TokenVault


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _create_tenant_and_conversation(key_provider):
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


def _create_document(key_provider, tenant_id):
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="doc2@example.com", role="doctor"
        )
        document_id = DocumentRepository(session).create(
            tenant_id,
            user.id,
            filename="x.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_storage_path="/data/documents/x/y/original.pdf",
        ).id
    return document_id


def test_create_and_resolve_round_trip(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    resolved = vault.resolve_token(tenant_id, "conversation", conversation_id, token)

    assert resolved == "Hans Müller"
    assert token.startswith("PATIENT_")


def test_resolve_fails_for_wrong_tenant(key_provider):
    tenant_a, conversation_a = _create_tenant_and_conversation(key_provider)
    tenant_b, _ = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_a, "conversation", conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_b, "conversation", conversation_a, token) is None


def test_resolve_fails_for_wrong_conversation(key_provider):
    tenant_id, conversation_a = _create_tenant_and_conversation(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="doc2@example.com", role="doctor"
        )
        other_conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_b = other_conversation.id

    vault = TokenVault(key_provider)
    token = vault.create_mapping(tenant_id, "conversation", conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_id, "conversation", conversation_b, token) is None


def test_token_uniqueness_within_tenant_and_conversation(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)

    with tenant_scoped_session(tenant_id) as session:
        dek_id = session.execute(
            sa.select(TenantKey.id).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()

        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="conversation",
                conversation_id=conversation_id,
                document_id=None,
                token="PATIENT_AAAAA",
                entity_type="PATIENT",
                encrypted_value=b"x" * 28,
                dek_id=dek_id,
            )
        )

    with pytest.raises(IntegrityError):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="conversation",
                    conversation_id=conversation_id,
                    document_id=None,
                    token="PATIENT_AAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"y" * 28,
                    dek_id=dek_id,
                )
            )


def test_delete_mapping_removes_the_row(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    vault.delete_mapping(tenant_id, "conversation", conversation_id, token)

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one_or_none()
        assert mapping is None


def test_expire_mapping_soft_deletes(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    vault.expire_mapping(tenant_id, "conversation", conversation_id, token)

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        assert mapping.deleted_at is not None


def test_ciphertext_is_bound_to_its_row_by_associated_data(key_provider):
    """A stored ciphertext must not decrypt under any other row's identity.

    The DEK is per-tenant, so without AES-GCM associated data the same blob would
    decrypt fine if it were moved into a different scope's row. The AAD binds it
    to `(tenant_id, scope_type, scope_id, token)`; a mismatch raises InvalidTag.
    """
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        blob = bytes(mapping.encrypted_value)
        dek_row = session.get(TenantKey, mapping.dek_id)
        raw_dek = key_provider.unwrap_dek(dek_row.wrapped_dek)

    nonce, ciphertext = blob[:_NONCE_LENGTH], blob[_NONCE_LENGTH:]

    correct_aad = f"{tenant_id}:conversation:{conversation_id}:{token}".encode()
    assert AESGCM(raw_dek).decrypt(nonce, ciphertext, correct_aad).decode() == "Hans Müller"

    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(
            nonce, ciphertext, f"{tenant_id}:conversation:{conversation_id}:PATIENT_00000".encode()
        )

    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(
            nonce, ciphertext, f"{tenant_id}:conversation:{uuid.uuid4()}:{token}".encode()
        )

    # Same tenant + same scope_id + same token, but scope_type='document' instead
    # of 'conversation' -- the new dimension this task adds.
    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(
            nonce, ciphertext, f"{tenant_id}:document:{conversation_id}:{token}".encode()
        )

    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(nonce, ciphertext, None)


def test_expired_mapping_is_not_resolvable(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) == "Hans Müller"

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        mapping.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None


def test_document_scoped_create_and_resolve_round_trip(key_provider):
    tenant_id, _ = _create_tenant_and_conversation(key_provider)
    document_id = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "document", document_id, "PATIENT", "Anna Weber")
    resolved = vault.resolve_token(tenant_id, "document", document_id, token)

    assert resolved == "Anna Weber"


def test_document_scoped_token_does_not_resolve_under_another_documents_scope(key_provider):
    tenant_id, _ = _create_tenant_and_conversation(key_provider)
    document_a = _create_document(key_provider, tenant_id)
    document_b = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "document", document_a, "PATIENT", "Anna Weber")

    assert vault.resolve_token(tenant_id, "document", document_b, token) is None


def test_document_scoped_token_does_not_resolve_under_a_conversation_scope(key_provider):
    """The core cross-scope isolation guarantee this task exists to add: a token
    minted while anonymizing a document must be structurally unresolvable through
    the conversation-scoped lookup path a chat message would use, even if a
    document's UUID were ever equal to some conversation's UUID."""
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    document_id = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "document", document_id, "PATIENT", "Anna Weber")

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None
    assert vault.resolve_token(tenant_id, "conversation", document_id, token) is None


def test_conversation_scoped_token_does_not_resolve_under_a_document_scope(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    document_id = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Anna Weber")

    assert vault.resolve_token(tenant_id, "document", document_id, token) is None
    assert vault.resolve_token(tenant_id, "document", conversation_id, token) is None
```

- [ ] **Step 3: Run the token vault tests**

Run: `cd backend && pytest tests/privacy_invariants/test_token_vault.py -v`
Expected: all PASS, including the four new document-scope tests.

- [ ] **Step 4: Commit**

```bash
git add backend/app/privacy_gateway/token_vault/vault.py \
  backend/tests/privacy_invariants/test_token_vault.py
git commit -m "feat: generalize TokenVault to a (scope_type, scope_id) scope"
```

---

### Task 4: Generalize `Pseudonymizer`, `OutputGuard`, `Pipeline`; update every call site

**Files:**
- Modify: `backend/app/privacy_gateway/pseudonymization/pseudonymizer.py`
- Modify: `backend/app/privacy_gateway/output_guard/guard.py`
- Modify: `backend/app/privacy_gateway/pipeline.py`
- Modify: `backend/app/api/chat.py`
- Modify: `backend/app/api/conversations.py`
- Modify: `backend/tests/privacy_invariants/test_pseudonymizer.py`
- Modify: `backend/tests/privacy_invariants/test_output_guard.py`
- Modify: `backend/tests/privacy_invariants/test_pipeline.py`
- Modify: `backend/tests/privacy_invariants/test_pipeline_corpus.py`
- Modify: `backend/tests/integration/test_chat_api.py`
- Modify: `backend/tests/integration/test_conversations_api.py`

**Interfaces:**
- Consumes: `TokenVault` with `(scope_type, scope_id)` (Task 3).
- Produces: `Pseudonymizer.apply(tenant_id, scope_type, scope_id, text, spans) ->
  str`; `OutputGuard.restore(tenant_id, scope_type, scope_id, llm_output) -> str`;
  `OutputGuard.restore_unchecked(tenant_id, scope_type, scope_id, llm_output) ->
  str` (`assert_no_raw_pii(text) -> None` is unchanged — it never touches the
  vault); `Pipeline.sanitize(tenant_id, scope_type, scope_id, text) -> str`;
  `Pipeline.deanonymize(tenant_id, scope_type, scope_id, llm_output) -> str`.
  Every existing conversation-flow caller passes `scope_type="conversation"`.
  Consumed by Plan 2 (document processing pipeline), which will pass
  `scope_type="document"`.

- [ ] **Step 1: Rewrite `pseudonymizer.py`**

Replace the full contents of
`backend/app/privacy_gateway/pseudonymization/pseudonymizer.py`:

```python
from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.token_vault.vault import ScopeType, TokenVault


class Pseudonymizer:
    """Replaces every tokenize-eligible span with a TokenVault token (design spec §4)."""

    def __init__(self, vault: TokenVault) -> None:
        self._vault = vault

    def apply(
        self,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
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
                    tenant_id, scope_type, scope_id, span.entity_type, original_value
                )
                issued[key] = token
            result = result[: span.start] + token + result[span.end :]
        return result
```

- [ ] **Step 2: Update `test_pseudonymizer.py`**

Every call in this file has the shape `pseudonymizer.apply(tenant_id,
conversation_id, text, spans)` or `vault.resolve_token(tenant_id, conversation_id,
token)`. Read the file, then for every such call insert `"conversation", `
immediately after the first (`tenant_id`-shaped) argument, turning
`apply(tenant_id, conversation_id, ...)` into
`apply(tenant_id, "conversation", conversation_id, ...)` and
`resolve_token(tenant_id, conversation_id, token)` into
`resolve_token(tenant_id, "conversation", conversation_id, token)`. Do not change
any variable names, fixture names, or assertions — only insert the literal
`"conversation", ` string in each of these call sites.

After editing, verify no old-shape call remains:

Run: `cd backend && grep -n '\.apply(\|\.resolve_token(\|\.create_mapping(' tests/privacy_invariants/test_pseudonymizer.py`
Expected: every matched line's argument list reads
`(tenant_id, "conversation", conversation_id, ...)` (or an equivalent tenant/scope
variable pair) — none reads `(tenant_id, conversation_id, ...)` without the
literal `"conversation"` in between.

- [ ] **Step 3: Run the pseudonymizer tests**

Run: `cd backend && pytest tests/privacy_invariants/test_pseudonymizer.py -v`
Expected: all PASS.

- [ ] **Step 4: Update `restore`/`restore_unchecked`/`_resolve` in `guard.py`**

Edit `backend/app/privacy_gateway/output_guard/guard.py`. First, update the
import line:

Old:
```python
from app.privacy_gateway.token_vault.vault import TokenVault
```

New:
```python
from app.privacy_gateway.token_vault.vault import ScopeType, TokenVault
```

Then replace the `restore` method:

Old:
```python
    def restore(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
    ) -> str:
        token_bounds = _token_bounds(llm_output)

        # Step 1: re-run the full detector stack over the output with every token
        # masked out; anything still detected is raw-looking PII the LLM produced or
        # leaked. Masking (rather than detecting on the raw output and then asking
        # whether each span sits inside a token) is what makes this check stable —
        # see _mask_tokens.
        leaked = self._scan(llm_output, token_bounds)
        if leaked:
            raise LeakageDetectedError(
                "raw-looking PII in LLM output: "
                + ", ".join(
                    f"{span.entity_type} at [{span.start}:{span.end}]" for span in leaked
                )
                + "; the response is rejected rather than partially returned",
                entity_types=[span.entity_type for span in leaked],
            )

        return self._resolve(tenant_id, conversation_id, llm_output, token_bounds)
```

New:
```python
    def restore(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
    ) -> str:
        token_bounds = _token_bounds(llm_output)

        # Step 1: re-run the full detector stack over the output with every token
        # masked out; anything still detected is raw-looking PII the LLM produced or
        # leaked. Masking (rather than detecting on the raw output and then asking
        # whether each span sits inside a token) is what makes this check stable —
        # see _mask_tokens.
        leaked = self._scan(llm_output, token_bounds)
        if leaked:
            raise LeakageDetectedError(
                "raw-looking PII in LLM output: "
                + ", ".join(
                    f"{span.entity_type} at [{span.start}:{span.end}]" for span in leaked
                )
                + "; the response is rejected rather than partially returned",
                entity_types=[span.entity_type for span in leaked],
            )

        return self._resolve(tenant_id, scope_type, scope_id, llm_output, token_bounds)
```

Then replace `restore_unchecked` and `_resolve`:

Old:
```python
    def restore_unchecked(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
    ) -> str:
        """Debug path (OUTPUT_GUARD_ENABLED=false): skip the leakage scan but still
        resolve tokens and still fail on unresolved ones. Returns the LLM's raw
        completion with tokens substituted back -- including any real-looking PII
        the model may have hallucinated -- so an operator can inspect what the model
        actually produced. Steps 2-4 of restore() run unchanged; only step 1 is
        bypassed.
        """
        return self._resolve(
            tenant_id, conversation_id, llm_output, _token_bounds(llm_output)
        )

    def _resolve(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        llm_output: str,
        token_bounds: list[tuple[int, int]],
    ) -> str:
        # Steps 2 and 3: extract the token-shaped substrings and let TokenVault apply
        # the (tenant_id, conversation_id) authorization scope.
        tokens = [llm_output[start:end] for start, end in token_bounds]
        resolved = self._vault.resolve_tokens(tenant_id, conversation_id, tokens)
```

New:
```python
    def restore_unchecked(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
    ) -> str:
        """Debug path (OUTPUT_GUARD_ENABLED=false): skip the leakage scan but still
        resolve tokens and still fail on unresolved ones. Returns the LLM's raw
        completion with tokens substituted back -- including any real-looking PII
        the model may have hallucinated -- so an operator can inspect what the model
        actually produced. Steps 2-4 of restore() run unchanged; only step 1 is
        bypassed.
        """
        return self._resolve(
            tenant_id, scope_type, scope_id, llm_output, _token_bounds(llm_output)
        )

    def _resolve(
        self,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
        llm_output: str,
        token_bounds: list[tuple[int, int]],
    ) -> str:
        # Steps 2 and 3: extract the token-shaped substrings and let TokenVault apply
        # the (tenant_id, scope_type, scope_id) authorization scope.
        tokens = [llm_output[start:end] for start, end in token_bounds]
        resolved = self._vault.resolve_tokens(tenant_id, scope_type, scope_id, tokens)
```

`assert_no_raw_pii` and `_scan` are unchanged — leave them exactly as they are;
they never call the vault.

- [ ] **Step 5: Update `test_output_guard.py`**

Same mechanical rule as Step 2: every `guard.restore(tenant_id, conversation_id,
...)`, `guard.restore_unchecked(tenant_id, conversation_id, ...)`, and
`vault.create_mapping(tenant_id, conversation_id, ...)` call gets `"conversation",
` inserted immediately after the first argument. Calls to
`guard.assert_no_raw_pii(...)` are unchanged (single-argument, no scope). Read the
file, apply the insertion to every matching call, then verify:

Run: `cd backend && grep -n '\.restore(\|\.restore_unchecked(\|\.create_mapping(' tests/privacy_invariants/test_output_guard.py`
Expected: every matched call's argument list contains the literal `"conversation"`
as its second argument.

- [ ] **Step 6: Run the output guard tests**

Run: `cd backend && pytest tests/privacy_invariants/test_output_guard.py -v`
Expected: all PASS.

- [ ] **Step 7: Update `pipeline.py`**

Edit `backend/app/privacy_gateway/pipeline.py`. Update the import:

Old:
```python
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault
```

New:
```python
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import ScopeType, TokenVault
```

Replace `sanitize`:

Old:
```python
    def sanitize(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str
    ) -> str:
        """Design spec §4: runs all three detector layers, risk-scores every span,
        raises on HIGH-risk or low-confidence, else returns the fully pseudonymized
        string ready to send to an LLM.

        Every step logs its result at INFO, and every fail-closed rejection logs at
        WARNING before the exception propagates -- entity types, counts, positions,
        and category labels only, never the matched text itself, so the log stream
        stays within the same privacy boundary this pipeline enforces on the LLM.
        """
        spans = self._detector_stack.detect(text)
        span_counts = dict(Counter(span.entity_type for span in spans))
        logger.info(
            "sanitize.detect tenant_id=%s conversation_id=%s spans=%d types=%s",
            tenant_id, conversation_id, len(spans), span_counts,
        )

        try:
            assessment = self._risk_scorer.score(text, spans)
        except (LowConfidenceSpanError, HighRiskMessageError) as exc:
            logger.warning(
                "sanitize.reject tenant_id=%s conversation_id=%s reason=%s: %s",
                tenant_id, conversation_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "sanitize.risk_assessment tenant_id=%s conversation_id=%s "
            "quasi_identifier_categories=%s rare_disease_matches=%d tokens_to_issue=%d",
            tenant_id,
            conversation_id,
            sorted(assessment.quasi_identifier_categories),
            len(assessment.rare_diseases),
            len(assessment.tokenize),
        )

        result = self._pseudonymizer.apply(
            tenant_id, conversation_id, text, assessment.tokenize
        )

        # Pre-send check: verify sanitize()'s own output before it ever reaches an
        # LLM, using the identical mask-then-rescan step the output guard runs on
        # LLM replies. This catches a pseudonymization bug (a detected span that
        # didn't get substituted), not a detector blind spot -- an entity layer 1-3
        # never recognized here was equally invisible to the scan two lines above.
        try:
            self._output_guard.assert_no_raw_pii(result)
        except ResidualPIIError as exc:
            logger.warning(
                "sanitize.residual_pii tenant_id=%s conversation_id=%s reason=%s: %s",
                tenant_id, conversation_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "sanitize.pseudonymize tenant_id=%s conversation_id=%s tokens_issued=%d -> PASS",
            tenant_id, conversation_id, len(assessment.tokenize),
        )
        return result
```

New:
```python
    def sanitize(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, text: str
    ) -> str:
        """Design spec §4: runs all three detector layers, risk-scores every span,
        raises on HIGH-risk or low-confidence, else returns the fully pseudonymized
        string ready to send to an LLM.

        Every step logs its result at INFO, and every fail-closed rejection logs at
        WARNING before the exception propagates -- entity types, counts, positions,
        and category labels only, never the matched text itself, so the log stream
        stays within the same privacy boundary this pipeline enforces on the LLM.
        """
        spans = self._detector_stack.detect(text)
        span_counts = dict(Counter(span.entity_type for span in spans))
        logger.info(
            "sanitize.detect tenant_id=%s scope_type=%s scope_id=%s spans=%d types=%s",
            tenant_id, scope_type, scope_id, len(spans), span_counts,
        )

        try:
            assessment = self._risk_scorer.score(text, spans)
        except (LowConfidenceSpanError, HighRiskMessageError) as exc:
            logger.warning(
                "sanitize.reject tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "sanitize.risk_assessment tenant_id=%s scope_type=%s scope_id=%s "
            "quasi_identifier_categories=%s rare_disease_matches=%d tokens_to_issue=%d",
            tenant_id,
            scope_type,
            scope_id,
            sorted(assessment.quasi_identifier_categories),
            len(assessment.rare_diseases),
            len(assessment.tokenize),
        )

        result = self._pseudonymizer.apply(
            tenant_id, scope_type, scope_id, text, assessment.tokenize
        )

        # Pre-send check: verify sanitize()'s own output before it ever reaches an
        # LLM, using the identical mask-then-rescan step the output guard runs on
        # LLM replies. This catches a pseudonymization bug (a detected span that
        # didn't get substituted), not a detector blind spot -- an entity layer 1-3
        # never recognized here was equally invisible to the scan two lines above.
        try:
            self._output_guard.assert_no_raw_pii(result)
        except ResidualPIIError as exc:
            logger.warning(
                "sanitize.residual_pii tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "sanitize.pseudonymize tenant_id=%s scope_type=%s scope_id=%s tokens_issued=%d -> PASS",
            tenant_id, scope_type, scope_id, len(assessment.tokenize),
        )
        return result
```

Replace `deanonymize`:

Old:
```python
    def deanonymize(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
    ) -> str:
        """Design spec §5: leakage scan, then authorization-checked token resolution.

        When OUTPUT_GUARD_ENABLED=false (debug only), the leakage scan is bypassed
        via OutputGuard.restore_unchecked so the raw LLM completion (tokens
        resolved, no leakage re-scan) is returned for inspection.
        UnresolvedTokenError still raises -- that is a correctness failure, not a
        privacy gate.
        """
        try:
            if self._guard_enabled:
                result = self._output_guard.restore(tenant_id, conversation_id, llm_output)
            else:
                logger.warning(
                    "deanonymize.UNGUARDED tenant_id=%s conversation_id=%s "
                    "output_guard disabled by config; raw LLM output returned",
                    tenant_id, conversation_id,
                )
                result = self._output_guard.restore_unchecked(
                    tenant_id, conversation_id, llm_output
                )
        except UnresolvedTokenError as exc:
            logger.warning(
                "deanonymize.reject tenant_id=%s conversation_id=%s reason=%s: %s",
                tenant_id, conversation_id, type(exc).__name__, exc,
            )
            raise
        except LeakageDetectedError as exc:
            logger.warning(
                "deanonymize.reject tenant_id=%s conversation_id=%s reason=%s: %s",
                tenant_id, conversation_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "deanonymize.restore tenant_id=%s conversation_id=%s -> PASS",
            tenant_id, conversation_id,
        )
        return result
```

New:
```python
    def deanonymize(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
    ) -> str:
        """Design spec §5: leakage scan, then authorization-checked token resolution.

        When OUTPUT_GUARD_ENABLED=false (debug only), the leakage scan is bypassed
        via OutputGuard.restore_unchecked so the raw LLM completion (tokens
        resolved, no leakage re-scan) is returned for inspection.
        UnresolvedTokenError still raises -- that is a correctness failure, not a
        privacy gate.
        """
        try:
            if self._guard_enabled:
                result = self._output_guard.restore(tenant_id, scope_type, scope_id, llm_output)
            else:
                logger.warning(
                    "deanonymize.UNGUARDED tenant_id=%s scope_type=%s scope_id=%s "
                    "output_guard disabled by config; raw LLM output returned",
                    tenant_id, scope_type, scope_id,
                )
                result = self._output_guard.restore_unchecked(
                    tenant_id, scope_type, scope_id, llm_output
                )
        except UnresolvedTokenError as exc:
            logger.warning(
                "deanonymize.reject tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise
        except LeakageDetectedError as exc:
            logger.warning(
                "deanonymize.reject tenant_id=%s scope_type=%s scope_id=%s reason=%s: %s",
                tenant_id, scope_type, scope_id, type(exc).__name__, exc,
            )
            raise

        logger.info(
            "deanonymize.restore tenant_id=%s scope_type=%s scope_id=%s -> PASS",
            tenant_id, scope_type, scope_id,
        )
        return result
```

Finally, replace the module-level convenience functions at the bottom of the file:

Old:
```python
def sanitize(tenant_id: uuid.UUID, conversation_id: uuid.UUID, text: str) -> str:
    return get_pipeline().sanitize(tenant_id, conversation_id, text)


def deanonymize(
    tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
) -> str:
    return get_pipeline().deanonymize(tenant_id, conversation_id, llm_output)
```

New:
```python
def sanitize(tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, text: str) -> str:
    return get_pipeline().sanitize(tenant_id, scope_type, scope_id, text)


def deanonymize(
    tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, llm_output: str
) -> str:
    return get_pipeline().deanonymize(tenant_id, scope_type, scope_id, llm_output)
```

- [ ] **Step 8: Update the production call sites in `chat.py`**

Edit `backend/app/api/chat.py`. In `_stream_and_guard`, replace:

Old:
```python
            for completed in buffer.feed(item.text):
                human_readable = pipeline.deanonymize(tenant_id, conversation_id, completed)
                sanitized_chunks.append(completed)
                yield _sse_token(human_readable)

        remainder = buffer.flush_remainder()
        if remainder is not None:
            human_readable = pipeline.deanonymize(tenant_id, conversation_id, remainder)
            sanitized_chunks.append(remainder)
            yield _sse_token(human_readable)
```

New:
```python
            for completed in buffer.feed(item.text):
                human_readable = pipeline.deanonymize(
                    tenant_id, "conversation", conversation_id, completed
                )
                sanitized_chunks.append(completed)
                yield _sse_token(human_readable)

        remainder = buffer.flush_remainder()
        if remainder is not None:
            human_readable = pipeline.deanonymize(
                tenant_id, "conversation", conversation_id, remainder
            )
            sanitized_chunks.append(remainder)
            yield _sse_token(human_readable)
```

In `send_message`, replace:

Old:
```python
    try:
        sanitized_prompt = pipeline.sanitize(user.tenant_id, conversation_id, body.content)
    except (LowConfidenceSpanError, HighRiskMessageError, ResidualPIIError) as exc:
```

New:
```python
    try:
        sanitized_prompt = pipeline.sanitize(
            user.tenant_id, "conversation", conversation_id, body.content
        )
    except (LowConfidenceSpanError, HighRiskMessageError, ResidualPIIError) as exc:
```

- [ ] **Step 9: Update the production call site in `conversations.py`**

Edit `backend/app/api/conversations.py`. In `get_messages`, replace:

Old:
```python
    messages = MessageRepository(session).list_for_conversation(user.tenant_id, conversation_id)
    return [
        MessageOut(
            id=message.id,
            role=message.role,
            content=pipeline.deanonymize(user.tenant_id, conversation_id, message.sanitized_content),
            created_at=message.created_at,
        )
        for message in messages
    ]
```

New:
```python
    messages = MessageRepository(session).list_for_conversation(user.tenant_id, conversation_id)
    return [
        MessageOut(
            id=message.id,
            role=message.role,
            content=pipeline.deanonymize(
                user.tenant_id, "conversation", conversation_id, message.sanitized_content
            ),
            created_at=message.created_at,
        )
        for message in messages
    ]
```

- [ ] **Step 10: Update `test_pipeline.py`, `test_pipeline_corpus.py`, `test_chat_api.py`, `test_conversations_api.py`**

Same mechanical rule as Steps 2 and 5, applied to all four files: every
`pipeline.sanitize(tenant_id_like, conversation_id_like, ...)`,
`pipeline.deanonymize(tenant_id_like, conversation_id_like, ...)`,
`corpus_pipeline.sanitize(...)`, and `corpus_pipeline.deanonymize(...)` call gets
`"conversation", ` inserted immediately after the first argument. This includes
calls where the identifiers are named differently (e.g. `other_tenant_id,
conversation_id` or `tenant_id, other_conversation_id` in the cross-tenant/
cross-conversation isolation tests already in `test_pipeline.py` and
`test_pipeline_corpus.py`, and `_, _, conversation_id = scope` /
`get_pipeline().sanitize(tenant_id, conversation_id, "Hallo, hier ist Anna
Schmitt.")` in `test_conversations_api.py`) — insert the literal `"conversation"`
as the second positional argument regardless of what the surrounding variables are
named; do not change the variable names themselves.

Read each file fully before editing (some calls span multiple lines), then verify
every file with:

Run: `cd backend && grep -rn '\.sanitize(\|\.deanonymize(' tests/privacy_invariants/test_pipeline.py tests/privacy_invariants/test_pipeline_corpus.py tests/integration/test_chat_api.py tests/integration/test_conversations_api.py`

Expected: every matched call site's second positional argument is the literal
`"conversation"`.

- [ ] **Step 11: Run the full backend test suite**

Run: `cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v`
Expected: all PASS, zero failures, zero errors. This is the real completion gate
for this task — a partial call-site update leaves the suite red, and this plan is
not done until it's fully green.

- [ ] **Step 12: Run ruff and the import-linter**

Run: `cd backend && ruff check . && lint-imports`
Expected: both clean. No new imports were added that violate the existing
`privacy_gateway` import-linter contracts (this task adds no new external
dependencies).

- [ ] **Step 13: Commit**

```bash
git add backend/app/privacy_gateway/pseudonymization/pseudonymizer.py \
  backend/app/privacy_gateway/output_guard/guard.py \
  backend/app/privacy_gateway/pipeline.py \
  backend/app/api/chat.py backend/app/api/conversations.py \
  backend/tests/privacy_invariants/test_pseudonymizer.py \
  backend/tests/privacy_invariants/test_output_guard.py \
  backend/tests/privacy_invariants/test_pipeline.py \
  backend/tests/privacy_invariants/test_pipeline_corpus.py \
  backend/tests/integration/test_chat_api.py \
  backend/tests/integration/test_conversations_api.py
git commit -m "feat: generalize Pseudonymizer/OutputGuard/Pipeline to (scope_type, scope_id) and update every call site"
```

---

### Task 5: Record the security-boundary change as an ADR; final verification

**Files:**
- Create: `docs/adr/0023-token-vault-scope-generalization.md`

**Interfaces:**
- Consumes: nothing new (documents Tasks 1-4).
- Produces: nothing consumed by later plans — this is a documentation-only task,
  but it's a required deliverable of this plan per the spec (§4.2).

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0023-token-vault-scope-generalization.md`:

```markdown
# 0023 — Token Vault Scope Generalization

**Status:** Accepted

## Context

ADR-0008 established an isolated Token Vault and ADR-0009 scoped every token to
`(tenant_id, conversation_id)`. The Documents app (Phase 1) needs to pseudonymize
text extracted from an uploaded document, but a document is not a conversation —
there is no `conversation_id` to scope its tokens to, and minting a synthetic
conversation row purely to hang document tokens off of would blur the two
concepts and complicate the conversation-visibility rules in
`app/auth/permissions.py`.

## Decision

`token_mappings` gains a `scope_type` discriminator (`"conversation"` |
`"document"`), `conversation_id` becomes nullable, and a nullable `document_id` is
added with its own composite FK to the new `documents` table. Exactly one of the
two is populated, per `scope_type`, enforced by a `CHECK` constraint. The single
`(tenant_id, conversation_id, token)` unique constraint is replaced by two partial
unique indexes, one per `scope_type` — a plain constraint over sometimes-NULL
columns would not enforce per-scope uniqueness, since Postgres treats every NULL
as distinct.

`TokenVault`, `Pseudonymizer`, `OutputGuard`, and `Pipeline` all change their
scoping parameter from a bare `conversation_id` to `(scope_type, scope_id)`. The
AES-GCM associated data (AAD, see ADR-0009) changes from
`f"{tenant_id}:{conversation_id}:{token}"` to
`f"{tenant_id}:{scope_type}:{scope_id}:{token}"` — binding `scope_type` into the
ciphertext means a document-scoped mapping's ciphertext structurally cannot
decrypt under a conversation-scoped lookup (an `InvalidTag` at decrypt time),
independent of and in addition to the schema-level CHECK constraint and partial
unique indexes.

This is document-local scoping, not identity-linking: one document has its own
scope, and nothing correlates tokens across two different documents, or between a
document and any conversation. It is explicitly **not** the "per-patient scope,
future phase" alternative ADR-0009 considered and deferred — that would let a
token resolve across multiple conversations for the same patient, which is a
different (and larger) design question this change does not touch.

## Alternatives Considered

- **Mint a synthetic `conversation` row per document:** rejected — reuses the
  conversation concept for something that is not a conversation (no messages, no
  visibility-scope semantics, no chat history), and would require either fake
  `Message` rows or forking `list_for_conversation`'s expectations. A first-class
  `scope_type` is a smaller, more honest change.
- **A separate `document_token_mappings` table with duplicated vault logic:**
  rejected — `TokenVault` is the audited, tested implementation of the encryption
  and per-scope isolation contract (ADR-0008); duplicating it for documents would
  create two overlapping implementations of the same security-critical logic, the
  exact failure mode ADR-0004 already rejected for NeMo vs. `privacy_gateway`.
- **Keep `conversation_id` NOT NULL and give every document a real conversation
  row:** rejected for the same reason as the synthetic-row option above, and
  additionally couples document lifecycle to conversation lifecycle (deleting a
  conversation must never be allowed to cascade into deleting a document's
  tokens, and vice versa).

## Consequences

Every existing caller of `TokenVault`/`Pseudonymizer`/`OutputGuard`/`Pipeline` was
updated in the same change to pass `scope_type="conversation"` explicitly — there
is no default, so a future caller must make an explicit, visible choice rather
than silently inheriting conversation scoping for what might be a new kind of
scope. `backend/tests/privacy_invariants/test_token_vault.py` gained explicit
cross-scope isolation tests (a document-scoped token must not resolve as
conversation-scoped, and vice versa) alongside every pre-existing test, updated to
the new call shape rather than left behind.

## Security Implications

Extends ADR-0008's isolation guarantee to a second scope kind without weakening
it: the CHECK constraint, the two partial unique indexes, and the AAD's
`scope_type` binding are three independent enforcement layers (schema shape,
schema uniqueness, and ciphertext authentication) that all have to agree for a
mapping to be readable at all, and any one of them alone is sufficient to reject a
cross-scope read.

## Privacy Implications

No new data is linked across documents or conversations by this change — if
anything, it narrows the existing conversation scope's blast radius by giving
document text an isolated home that a conversation-scoped bug cannot reach, and
vice versa.

## Reversibility

Medium — reversible at the schema level (migration `0009`'s `downgrade()` exists),
but reverting after real document-scoped tokens exist in production would require
either deleting them or migrating them to a conversation scope first; this is the
same reversibility class as ADR-0008 itself.
```

- [ ] **Step 2: Run the full backend verification suite one more time**

Run: `cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v && ruff check . && lint-imports`
Expected: everything green. This confirms Tasks 1-4's changes are still coherent
after adding no code in this task — a pure documentation change should never
affect test/lint results, and this step exists to catch the case where an earlier
task's commit was accidentally incomplete.

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0023-token-vault-scope-generalization.md
git commit -m "docs: record the token vault scope generalization as ADR-0023"
```

---

## What this plan does not cover

Everything downstream of this foundation — the `document_gateway` OCR/extraction/
markdown pipeline (calling `Pipeline.sanitize(tenant_id, "document", document_id,
block_text)` per block), the `/api/documents/*` endpoints, background processing,
and the frontend Documents app — is separate follow-on work per
`docs/superpowers/specs/2026-08-23-documents-app-phase1-design.md` §5-§8, to be
planned once this foundation has landed and been reviewed.
