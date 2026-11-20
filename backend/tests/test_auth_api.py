import pytest
from fastapi.testclient import TestClient

from dp_agentic_collector.api.app import create_app
from dp_agentic_collector.domain.errors import NotFound, PermissionDenied, Unauthenticated


@pytest.fixture
def client(app, world):
    return TestClient(create_app(app, start_worker=False))


def login(client, email, pw, tenant="default"):
    r = client.post("/api/v1/auth/login", json={"tenant": tenant, "email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def test_login_me_logout_and_csrf(client, world):
    csrf = login(client, "admin@default.test", "pw-admin-123")
    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["roles"] == ["admin"]
    assert "password_hash" not in me["user"]
    # unsafe method without CSRF header is rejected
    r = client.post("/api/v1/users", json={"name": "C", "email": "c@x.test"})
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"
    r = client.post("/api/v1/users", json={"name": "C", "email": "c@x.test"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    assert r.headers["x-trace-id"]
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert client.get("/api/v1/auth/me").status_code == 401


def test_wrong_password_and_rate_limit(client, app):
    app.settings.login_rate_limit = 3
    for _ in range(3):
        r = client.post("/api/v1/auth/login", json={"email": "alice@default.test", "password": "nope"})
        assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "alice@default.test", "password": "pw-alice-123"})
    assert r.status_code == 429


def test_rbac_respondent_cannot_admin(client):
    csrf = login(client, "alice@default.test", "pw-alice-123")
    r = client.post("/api/v1/users", json={"name": "C", "email": "c@x.test"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    assert client.get("/api/v1/audit").status_code == 403
    assert client.get("/api/v1/audit?mine=true").status_code == 200


def test_bearer_token_scopes(client, app, world):
    actor = world.actor(world.alice)
    read = app.services.auth.personal_token(actor, ["dpac.read"])
    h = {"Authorization": f"Bearer {read.access_token}"}
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    r = client.patch(f"/api/v1/users/{world.alice.user_id}", json={"locale": "de"}, headers=h)
    assert r.status_code == 403  # write requires dpac.write


def test_tenant_isolation_of_users(app, world):
    from tests.conftest import World

    other = World(app, "other")
    users_a = {u.user_id for u in app.services.tenancy.list_users(world.actor(world.admin))}
    users_b = {u.user_id for u in app.services.tenancy.list_users(other.actor(other.admin))}
    assert not users_a & users_b
    with pytest.raises(NotFound):
        app.services.tenancy.get_user(other.actor(other.admin), world.alice.user_id)


def test_vault_encrypts_and_isolates(app, world):
    from sqlalchemy import select

    from dp_agentic_collector.adapters.sql.schema import credential_secrets

    ref = app.vault.put(world.tenant.tenant_id, world.alice.user_id, {"access_token": "secret-token-123"})
    assert app.vault.get(world.tenant.tenant_id, ref)["access_token"] == "secret-token-123"
    with app.db.engine.connect() as c:
        raw = c.execute(select(credential_secrets.c.ciphertext)).scalar_one()
    assert "secret-token-123" not in raw
    with pytest.raises(NotFound):
        app.vault.get("other-tenant", ref)


def test_inactive_user_cannot_act(app, world):
    app.services.tenancy.update_user(world.actor(world.admin), world.bob.user_id, active=False)
    with pytest.raises(Unauthenticated):
        world.actor(world.bob)
    with pytest.raises(PermissionDenied):
        app.services.tenancy.update_user(world.actor(world.alice), world.bob.user_id, active=True)


def test_log_redaction():
    from dp_agentic_collector.observability import redact

    assert "abc123" not in redact("Authorization: Bearer abc123")
    assert "s3cr3t" not in redact('{"access_token": "s3cr3t"}')
