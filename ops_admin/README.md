# ops_admin

Internal-only Django admin for the app catalog and per-tenant entitlements
(`apps`, `tenant_app_entitlements` in the main chatgpt-proxy Postgres
database). Used by the ops/platform team to decide which apps a tenant is
subscribed to; tenants' own admins manage everything else (which apps are
*enabled* for their org) via the existing Next.js admin UI in `frontend/`.

Never exposed through the public reverse proxy -- reachable only at
`127.0.0.1:8090` on the docker host (see `docker-compose.yml`; this service
has no Traefik labels). Reach it via an SSH tunnel to the docker host, e.g.
`ssh -L 8090:localhost:8090 <host>` then open `http://localhost:8090/admin/`.

Auth is plain Django (`django.contrib.auth`), not Keycloak -- ops identities
and tenant identities are disjoint namespaces. Create the first login with:

```
docker compose exec ops_admin python manage.py createsuperuser
```

## Do not run `makemigrations catalog`

`catalog/models.py` defines `Tenant`, `App`, and `TenantAppEntitlement` as
`managed = False`, pointing at tables owned by the main backend's Alembic
migrations (`backend/alembic/versions/0007_apps_catalog_and_entitlements.py`
and earlier). Alembic is the single schema source of truth for these tables.

If `python manage.py makemigrations catalog` ever proposes a migration, that
means a model here has drifted from the real schema -- fix the model (or the
Alembic migration) instead of generating one. CI should run
`python manage.py makemigrations catalog --check` to guard against this.

Django's own built-in apps (`auth`, `sessions`, `admin`, etc.) get real
migrations as normal, but are written into a separate `ops` Postgres schema
(see `config/settings.py`'s `search_path`), so they never collide with
anything Alembic manages in `public`.
