from django.contrib import admin

from .models import App, Tenant, TenantAppEntitlement


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """Registered only so `autocomplete_fields` on TenantAppEntitlementAdmin
    works (Django requires the related model be registered with
    search_fields). Tenants are provisioned in the main backend, never here --
    this admin is read-only by construction."""

    list_display = ("name", "keycloak_realm")
    search_fields = ("name",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "is_active")
    search_fields = ("key", "name")


@admin.register(TenantAppEntitlement)
class TenantAppEntitlementAdmin(admin.ModelAdmin):
    list_display = ("tenant", "app", "granted_by", "granted_at", "revoked_at")
    list_filter = ("app", "revoked_at")
    autocomplete_fields = ("tenant", "app")
    readonly_fields = ("granted_by", "granted_at")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.granted_by = request.user.get_username()
        super().save_model(request, obj, form, change)
