"""Demo/dev seed data (no real personal data)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .domain.enums import AccessMode, McpAuthType, McpTransport, Role
from .domain.errors import ValidationFailed

PASSWORD = "demo-password"
USERS = [("Ada Admin", "admin@example.test", [Role.ADMIN]),
         ("Olga Owner", "owner@example.test", [Role.CYCLE_OWNER]),
         ("Alice Respondent", "alice@example.test", [Role.RESPONDENT]),
         ("Bob Respondent", "bob@example.test", [Role.RESPONDENT]),
         ("Vera Viewer", "viewer@example.test", [Role.VIEWER])]


def seed(app: Any, tenant: str = "default", fixture_url: str | None = None) -> Iterator[str]:
    s = app.services
    try:
        t = s.tenancy.create_tenant(tenant)
    except ValidationFailed:
        yield f"tenant {tenant} exists; skipping"
        return
    users = {}
    for name, email, roles in USERS:
        users[email] = s.tenancy.bootstrap_user(t.tenant_id, name, email, PASSWORD, roles)
        yield f"user {email} / {PASSWORD}"
    owner = s.auth.actor_for_user(t.tenant_id, users["owner@example.test"].user_id)
    admin = s.auth.actor_for_user(t.tenant_id, users["admin@example.test"].user_id)
    cycle = s.cycles.create_cycle(owner, "app-inventory-2026-Q4", "application", scope="All business applications")
    for name, assignee in (("CRM", "alice@example.test"), ("ERP", "bob@example.test"),
                           ("Billing", "alice@example.test")):
        s.cycles.add_subject(owner, cycle.cycle_id, name, users[assignee].user_id)
    s.cycles.start_cycle(owner, cycle.cycle_id)
    yield f"cycle {cycle.name} started with 3 jobs"
    if fixture_url:
        base = fixture_url.rstrip("/")
        s.connections.create_server(admin, "Asset Registry", McpTransport.STREAMABLE_HTTP, f"{base}/registry/mcp",
                                    auth_type=McpAuthType.API_KEY, max_access_mode=AccessMode.READ,
                                    description="Read-only asset registry (demo keys: key-alice, key-bob)")
        s.connections.create_server(admin, "Tickets", McpTransport.STREAMABLE_HTTP, f"{base}/tickets/mcp",
                                    auth_type=McpAuthType.OAUTH2, max_access_mode=AccessMode.READ_WRITE,
                                    oauth={"authorize_url": f"{base}/as/authorize", "token_url": f"{base}/as/token",
                                           "client_id": "dpac-demo"},
                                    description="Ticket system with write tools (OAuth)")
        yield f"MCP fixture servers registered at {base}"
    app.drain()
