import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().app_database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def tenant_scoped_session(tenant_id: uuid.UUID) -> Iterator[Session]:
    with SessionLocal() as session:
        session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
