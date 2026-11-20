"""``dpac`` command line: server, worker, migrations, admin tasks, demo data and the CLI channel."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from .config import Settings

app = typer.Typer(help="DP Agentic Collector", no_args_is_help=True)
db_app = typer.Typer(help="Database migrations")
admin_app = typer.Typer(help="Administration")
demo_app = typer.Typer(help="Demo data")
app.add_typer(db_app, name="db")
app.add_typer(admin_app, name="admin")
app.add_typer(demo_app, name="demo")


def _collector(create_schema: bool = True):  # type: ignore[no-untyped-def]
    from .container import Collector

    return Collector.create(Settings(), create_schema=create_schema)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, worker: Annotated[bool | None, typer.Option()] = None) -> None:
    """Run the HTTP API (and, by default, the in-process worker)."""
    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(_collector(), start_worker=worker), host=host, port=port)


@app.command()
def worker(tick_seconds: float = 300) -> None:
    """Run only the worker (outbox consumer + scheduler)."""
    import signal
    import threading

    from .observability import configure

    c = _collector()
    configure(c.settings.otlp_endpoint, c.settings.log_json)
    c.queue.start()
    c.orchestrator.start_scheduler(tick_seconds)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    c.queue.stop()


@app.command()
def drain() -> None:
    """Process all pending outbox events once."""
    typer.echo(f"processed {_collector().drain()} events")


@app.command()
def openapi(output: Path | None = None) -> None:
    """Print (or write) the OpenAPI contract."""
    from .api.app import create_app

    spec = json.dumps(create_app(_collector(), start_worker=False).openapi(), indent=2, sort_keys=True)
    if output:
        output.write_text(spec + "\n")
    else:
        sys.stdout.write(spec + "\n")


@app.command()
def plugins() -> None:
    """List registered extensions."""
    for row in _collector().registries.describe():
        typer.echo(f"{row['point']:<24} {row['name']:<20} v{row['interface_version']}  ({row['origin']})")


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    from .container import migrate

    migrate(Settings().database_url, revision)
    typer.echo("database upgraded")


@admin_app.command("create-tenant")
def create_tenant(name: str) -> None:
    t = _collector().services.tenancy.create_tenant(name)
    typer.echo(t.tenant_id)


@admin_app.command("create-user")
def create_user(tenant: str, email: str, name: str, password: str, role: list[str] = typer.Option(["respondent"]),
                locale: str = "en") -> None:
    from .domain.enums import Role

    c = _collector()
    t = c.services.tenancy.tenant_by_name(tenant)
    u = c.services.tenancy.bootstrap_user(t.tenant_id, name, email, password, [Role(r) for r in role], locale)
    typer.echo(u.user_id)


@admin_app.command("erase-user")
def erase_user(tenant: str, admin_email: str, user_id: str) -> None:
    """Erase a user's personal data (ADR-0011)."""
    c = _collector()
    from .domain.entities import User

    t = c.services.tenancy.tenant_by_name(tenant)
    with c.db.uow(t.tenant_id) as uow:
        admin = uow.repo(User).first(email=admin_email.lower())
    if admin is None:
        raise typer.Exit(1)
    c.services.tenancy.erase_user(c.services.auth.actor_for_user(t.tenant_id, admin.user_id), user_id)
    typer.echo("erased")


@demo_app.command("seed")
def demo_seed(fixture_url: str = "", tenant: str = "default") -> None:
    """Create a demo tenant with users (password = 'demo-password'), a Cycle and MCP fixture servers."""
    from .demo import seed

    for line in seed(_collector(), tenant=tenant, fixture_url=fixture_url or None):
        typer.echo(line)


@app.command()
def chat(job_id: str, email: str, password: str = typer.Option(..., prompt=True, hide_input=True),
         tenant: str = "default") -> None:
    """CLI channel: talk to the collector agent for one of your jobs."""
    c = _collector()
    res = c.services.auth.login(tenant, email, password)
    actor, _ = c.services.auth.resolve_session(res.session_id)
    seen: set[str] = set()

    def show() -> None:
        for m in c.services.conversations.messages(actor, job_id):
            if m.message_id not in seen:
                seen.add(m.message_id)
                who = "you" if m.sender.ref == actor.user_id else m.sender.display or m.sender.ref
                typer.echo(f"[{who}] {m.text}")
        for p in c.services.conversations.proposals(actor, job_id, status="proposed"):
            typer.echo(f"  proposal {p.proposal_id}: {p.path} = {json.dumps(p.value)}")

    show()
    typer.echo("Commands: /confirm <id|all>, /reject <id>, /submit, /quit")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if line in ("/quit", "/exit"):
            break
        if line.startswith("/confirm"):
            arg = line.split(maxsplit=1)[1] if " " in line else "all"
            ids = [p.proposal_id for p in c.services.conversations.proposals(actor, job_id, status="proposed")] \
                if arg == "all" else [arg]
            for pid in ids:
                c.services.conversations.decide_proposal(actor, pid, "confirm")
        elif line.startswith("/reject"):
            c.services.conversations.decide_proposal(actor, line.split()[1], "reject")
        elif line == "/submit":
            r = c.services.artifacts.submit(actor, job_id)
            typer.echo("conflict! resolve in the web UI" if r.conflict else f"submitted v{r.version.header.version}")
        elif line:
            c.services.conversations.post_user_message(actor, job_id, line)
        c.drain()
        show()


if __name__ == "__main__":  # pragma: no cover
    app()
