import uuid
from datetime import datetime, timezone

from app.db.repositories.base import BaseRepository
from app.models import AuditEvent


class AuditEventRepository(BaseRepository):
    def create(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        event_type: str,
        entity_type: str,
        token: str,
        actor: str,
    ) -> AuditEvent:
        event = AuditEvent(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            event_type=event_type,
            entity_type=entity_type,
            token=token,
            actor=actor,
            timestamp=datetime.now(timezone.utc),
        )
        self.session.add(event)
        self.session.flush()
        return event
