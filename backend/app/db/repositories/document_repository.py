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
                error_message=None,
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
