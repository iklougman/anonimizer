from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import AppAssignmentIn, AppAssignmentOut, AppOut
from app.auth.dependencies import get_db_session, require_permission
from app.auth.permissions import Permission
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.app_entitlement_repository import AppEntitlementRepository
from app.db.repositories.branch_repository import BranchRepository

router = APIRouter(prefix="/api/admin/apps", tags=["admin-apps"])


def _to_app_out(repo: AppEntitlementRepository, branch_repo: BranchRepository, tenant_id: uuid.UUID, app) -> AppOut:
    assignments = []
    for assignment in repo.list_assignments_for_tenant(tenant_id, app.id):
        branch = branch_repo.get(tenant_id, assignment.branch_id) if assignment.branch_id else None
        assignments.append(
            AppAssignmentOut(
                branch_id=assignment.branch_id,
                branch_name=branch.name if branch else None,
                is_enabled=assignment.is_enabled,
            )
        )
    return AppOut(
        id=app.id,
        key=app.key,
        name=app.name,
        description=app.description,
        is_entitled=repo.is_entitled(tenant_id, app.key),
        assignments=assignments,
    )


@router.get("", response_model=list[AppOut])
def list_apps(
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_APPS_MANAGE)),
    session: Session = Depends(get_db_session),
) -> list[AppOut]:
    repo = AppEntitlementRepository(session)
    branch_repo = BranchRepository(session)
    return [_to_app_out(repo, branch_repo, user.tenant_id, app) for app in repo.list_catalog()]


@router.put("/{app_id}/assignment", response_model=AppAssignmentOut)
def put_app_assignment(
    app_id: uuid.UUID,
    body: AppAssignmentIn,
    user: AuthenticatedUser = Depends(require_permission(Permission.ADMIN_APPS_MANAGE)),
    session: Session = Depends(get_db_session),
) -> AppAssignmentOut:
    repo = AppEntitlementRepository(session)
    app = repo.get(app_id)
    if app is None or not app.is_active:
        raise HTTPException(status_code=404, detail="app not found")
    if not repo.is_entitled(user.tenant_id, app.key):
        # A tenant admin cannot enable an app ops hasn't granted the tenant.
        raise HTTPException(status_code=409, detail="tenant is not entitled to this app")

    branch_repo = BranchRepository(session)
    if body.branch_id is not None and branch_repo.get(user.tenant_id, body.branch_id) is None:
        raise HTTPException(status_code=422, detail="unknown branch_id")

    assignment = repo.upsert_assignment(
        user.tenant_id, app_id, body.branch_id, body.is_enabled, assigned_by=user.user_id
    )
    branch = branch_repo.get(user.tenant_id, assignment.branch_id) if assignment.branch_id else None
    return AppAssignmentOut(
        branch_id=assignment.branch_id,
        branch_name=branch.name if branch else None,
        is_enabled=assignment.is_enabled,
    )
