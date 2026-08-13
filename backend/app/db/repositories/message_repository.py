import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import Message


class MessageRepository(BaseRepository):
    def create(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, role: str, sanitized_content: str
    ) -> Message:
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role=role,
            sanitized_content=sanitized_content,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def list_for_conversation(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> list[Message]:
        stmt = sa.select(Message).where(
            Message.tenant_id == tenant_id,
            Message.conversation_id == conversation_id,
        )
        return list(self.session.execute(stmt).scalars().all())
