"""Read/write models for tables Alembic owns in the main chatgpt-proxy backend.

CRITICAL: every model below is `managed = False`. Django's migration
framework must never generate a migration for these -- Alembic
(backend/alembic/versions/) remains the single schema source of truth for
`tenants`, `apps`, and `tenant_app_entitlements`.

Do NOT run `python manage.py makemigrations catalog` for this app. If Django
ever proposes one, that means a model here has drifted from the real schema
-- fix the model (or the Alembic migration), never generate a migration to
paper over it. `catalog/migrations/` is intentionally empty of operations.

The `db_table = 'public"."<table>"'`-style values below are a standard Django
trick for referencing a schema-qualified table on PostgreSQL: Django's
`quote_name` wraps the whole string in double quotes unless it already starts
with one, so `'public"."apps'` becomes `"public"."apps"` -- a valid
schema.table reference -- regardless of this service's own `search_path`
(which points elsewhere, at the `ops` schema, for Django's own built-in
tables; see config/settings.py).
"""
import uuid

from django.db import models


class Tenant(models.Model):
    """Read-only from this service -- used only to label tenants in the
    entitlement-grant admin form. Provisioning tenants happens in the main
    backend, never here."""

    id = models.UUIDField(primary_key=True)
    name = models.CharField(max_length=255)
    keycloak_realm = models.CharField(max_length=255)

    class Meta:
        managed = False
        db_table = 'public"."tenants'

    def __str__(self) -> str:
        return self.name


class App(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    key = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'public"."apps'

    def __str__(self) -> str:
        return self.name


class TenantAppEntitlement(models.Model):
    """Ops-controlled subscription grant. `revoked_at` blank/null = active;
    revoking is just setting it, re-granting is just clearing it -- both are
    ordinary edits on the Django admin change form, no custom action needed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant = models.ForeignKey(Tenant, on_delete=models.DO_NOTHING, db_column="tenant_id")
    app = models.ForeignKey(App, on_delete=models.DO_NOTHING, db_column="app_id")
    granted_by = models.CharField(max_length=255, editable=False)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'public"."tenant_app_entitlements'
        unique_together = ("tenant", "app")

    def __str__(self) -> str:
        return f"{self.tenant} -> {self.app}"
