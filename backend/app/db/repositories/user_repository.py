import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import User


class UserRepository(BaseRepository):
    def create(self, tenant_id: uuid.UUID, keycloak_subject: str, email: str, role: str) -> User:
        user = User(tenant_id=tenant_id, keycloak_subject=keycloak_subject, email=email, role=role)
        self.session.add(user)
        self.session.flush()
        return user

    def get_by_keycloak_subject(self, tenant_id: uuid.UUID, keycloak_subject: str) -> User | None:
        stmt = sa.select(User).where(
            User.tenant_id == tenant_id,
            User.keycloak_subject == keycloak_subject,
        )
        return self.session.execute(stmt).scalar_one_or_none()
