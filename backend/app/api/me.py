from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import MeOut
from app.auth.dependencies import get_current_user, get_db_session
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.branch_repository import BranchRepository
from app.db.session import SessionLocal
from app.models import Tenant

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("", response_model=MeOut)
def get_me(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> MeOut:
    # tenants has no tenant_isolation RLS policy (it is the parent table), so a
    # plain session reads it — same pattern as resolve_authenticated_user.
    with SessionLocal() as plain_session:
        tenant = plain_session.get(Tenant, user.tenant_id)

    branch_name: str | None = None
    if user.branch_id is not None:
        branch = BranchRepository(session).get(user.tenant_id, user.branch_id)
        branch_name = branch.name if branch is not None else None

    return MeOut(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        tenant_name=tenant.name if tenant is not None else "",
        email=user.email,
        role=user.role,
        branch_id=user.branch_id,
        branch_name=branch_name,
        permissions=sorted(str(p) for p in user.permissions),
    )
