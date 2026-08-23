from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import AvailableAppOut
from app.auth.dependencies import get_current_user, get_db_session
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.app_entitlement_repository import AppEntitlementRepository

router = APIRouter(prefix="/api/apps", tags=["apps"])


@router.get("", response_model=list[AvailableAppOut])
def list_available_apps(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[AvailableAppOut]:
    """Every role (not just super_admin) can see which apps their own tenant
    has entitled and enabled for them -- unlike GET /api/admin/apps, this
    needs no admin:apps:manage permission, since it's just "what can I use,"
    not entitlement/assignment management."""
    apps = AppEntitlementRepository(session).list_available_for_user(user.tenant_id, user.branch_id)
    return [AvailableAppOut(key=app.key, name=app.name, description=app.description) for app in apps]
