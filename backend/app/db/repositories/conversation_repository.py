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
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Conversation]:
        stmt = sa.select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.user_id == user_id,
        )
        return list(self.session.execute(stmt).scalars().all())
