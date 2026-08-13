import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().app_database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def tenant_scoped_session(tenant_id: uuid.UUID) -> Iterator[Session]:
    """Open a session whose every transaction carries `app.current_tenant_id`.

    `set_config(..., true)` has `SET LOCAL` semantics: the value lives only for the
    current transaction. Setting it once at session open would therefore be silently
    lost the moment the caller committed or rolled back mid-block — every subsequent
    statement in the same `with` block would run with no tenant context (and, on a
    reused pooled connection, with a stale `''` left behind by a previous request).
    Binding it to the session's `after_begin` event instead re-applies it at the start
    of *every* transaction this session opens, so the tenant context cannot be dropped
    by anything the caller does inside the block.
    """

    with SessionLocal() as session:

        def _set_tenant_context(
            session: Session, transaction: SessionTransaction, connection: Connection
        ) -> None:
            connection.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )

        # Registered on this Session instance only (the listener closes over this
        # call's tenant_id), and removed in `finally` so the registration cannot
        # outlive the block even if the Session object itself is kept alive.
        event.listen(session, "after_begin", _set_tenant_context)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            event.remove(session, "after_begin", _set_tenant_context)
