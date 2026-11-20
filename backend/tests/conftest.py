from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from dp_agentic_collector.config import Settings
from dp_agentic_collector.container import Collector
from dp_agentic_collector.domain.enums import Role


def make_settings(tmp_path, **kw) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path}/t.db", data_dir=tmp_path / "data",
                    vault_keys=Fernet.generate_key().decode(), run_worker=False, log_json=False,
                    _env_file=None, **kw)


@pytest.fixture
def app(tmp_path) -> Collector:
    return Collector.create(make_settings(tmp_path))


class World:
    """Seeded tenant with users of every role."""

    def __init__(self, app: Collector, name: str = "default") -> None:
        self.app = app
        s = app.services
        self.tenant = s.tenancy.create_tenant(name)
        tid = self.tenant.tenant_id
        mk = s.tenancy.bootstrap_user
        self.admin = mk(tid, "Admin", f"admin@{name}.test", "pw-admin-123", [Role.ADMIN])
        self.owner = mk(tid, "Owner", f"owner@{name}.test", "pw-owner-123", [Role.CYCLE_OWNER])
        self.alice = mk(tid, "Alice", f"alice@{name}.test", "pw-alice-123", [Role.RESPONDENT])
        self.bob = mk(tid, "Bob", f"bob@{name}.test", "pw-bob-123", [Role.RESPONDENT])
        self.viewer = mk(tid, "Viewer", f"viewer@{name}.test", "pw-viewer-123", [Role.VIEWER])

    def actor(self, user):
        return self.app.services.auth.actor_for_user(user.tenant_id, user.user_id)


@pytest.fixture
def world(app) -> World:
    return World(app)
