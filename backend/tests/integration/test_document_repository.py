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
