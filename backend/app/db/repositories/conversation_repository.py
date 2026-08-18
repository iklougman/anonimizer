import uuid
from typing import Literal

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import Conversation, User


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

    def get_with_owner(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> tuple[Conversation, User] | None:
        stmt = (
            sa.select(Conversation, User)
            .join(User, sa.and_(User.tenant_id == Conversation.tenant_id, User.id == Conversation.user_id))
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
        )
        row = self.session.execute(stmt).one_or_none()
        return (row[0], row[1]) if row is not None else None

    def list_visible(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        branch_id: uuid.UUID | None,
        scope: Literal["own", "branch", "tenant"],
    ) -> list[tuple[Conversation, User]]:
        """Own conversations always included; `scope` widens what else is visible.

        `branch` uses `is_not_distinct_from` (SQL NULL-safe equality, unlike `==`)
        so a single-location praxis -- every user's branch_id NULL -- correctly
        degenerates a branch grant to praxis-wide visibility.
        """
        conditions = [User.id == user_id]
        if scope == "tenant":
            conditions = []  # every conversation in the tenant is visible
        elif scope == "branch":
            conditions.append(User.branch_id.is_not_distinct_from(branch_id))

        stmt = (
            sa.select(Conversation, User)
            .join(User, sa.and_(User.tenant_id == Conversation.tenant_id, User.id == Conversation.user_id))
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.deleted_at.is_(None),
                sa.or_(*conditions) if conditions else sa.true(),
            )
            .order_by(Conversation.updated_at.desc())
        )
        return [(row[0], row[1]) for row in self.session.execute(stmt).all()]
