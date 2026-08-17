import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import Conversation


class ConversationRepository(BaseRepository):
    def create(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
        conversation = Conversation(tenant_id=tenant_id, user_id=user_id)
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation | None:
        stmt = sa.select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Conversation]:
        stmt = (
            sa.select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Conversation.updated_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def set_title(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, title: str) -> None:
        stmt = (
            sa.update(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.id == conversation_id)
            .values(title=title)
        )
        self.session.execute(stmt)

    def touch(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        stmt = (
            sa.update(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.id == conversation_id)
            .values(updated_at=sa.func.now())
        )
        self.session.execute(stmt)

    def soft_delete(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        stmt = (
            sa.update(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.id == conversation_id)
            .values(deleted_at=sa.func.now())
        )
        self.session.execute(stmt)
