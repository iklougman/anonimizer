import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import User


class UserRepository(BaseRepository):
    def create(
        self,
        tenant_id: uuid.UUID,
        keycloak_subject: str,
        email: str,
        role: str,
        branch_id: uuid.UUID | None = None,
        is_active: bool = True,
    ) -> User:
        user = User(
            tenant_id=tenant_id,
            keycloak_subject=keycloak_subject,
            email=email,
            role=role,
            branch_id=branch_id,
            is_active=is_active,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def get(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
        stmt = sa.select(User).where(User.tenant_id == tenant_id, User.id == user_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_keycloak_subject(self, tenant_id: uuid.UUID, keycloak_subject: str) -> User | None:
        stmt = sa.select(User).where(
            User.tenant_id == tenant_id,
            User.keycloak_subject == keycloak_subject,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_tenant(self, tenant_id: uuid.UUID) -> list[User]:
        stmt = sa.select(User).where(User.tenant_id == tenant_id).order_by(User.email)
        return list(self.session.execute(stmt).scalars().all())

    def update(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str | None = None,
        branch_id: uuid.UUID | None | object = ...,
        is_active: bool | None = None,
    ) -> None:
        """Partial update; `branch_id=...` (the Ellipsis default) means "leave
        unchanged", because None is a meaningful value (praxis-wide user)."""
        values: dict[str, object] = {}
        if role is not None:
            values["role"] = role
        if branch_id is not ...:
            values["branch_id"] = branch_id
        if is_active is not None:
            values["is_active"] = is_active
        if not values:
            return
        stmt = (
            sa.update(User)
            .where(User.tenant_id == tenant_id, User.id == user_id)
            .values(**values)
        )
        self.session.execute(stmt)

    def count_active_super_admins(self, tenant_id: uuid.UUID) -> int:
        stmt = sa.select(sa.func.count()).where(
            User.tenant_id == tenant_id,
            User.role == "super_admin",
            User.is_active.is_(True),
        )
        return self.session.execute(stmt).scalar_one()
