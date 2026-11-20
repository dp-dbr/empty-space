"""Starts a self-contained demo/e2e environment: MCP fixture servers + Collector (FakeLlm) + built UI.

    python -m dp_agentic_collector.testing.e2e_server --port 8765 --web-dist ../frontend/dist
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet

from ..api.app import create_app
from ..config import Settings
from ..container import Collector
from ..demo import seed
from .mcp_fixtures import RunningFixture


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--web-dist", type=Path, default=None)
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="dpac-e2e-"))
    fixture = RunningFixture().__enter__()
    base = f"http://127.0.0.1:{args.port}"
    settings = Settings(database_url=f"sqlite:///{tmp}/e2e.db", data_dir=tmp / "data", vault_keys=Fernet.generate_key().decode(),
                        default_llm_provider="fake", run_worker=True, worker_poll_seconds=0.2, public_base_url=base,
                        web_dist=args.web_dist, log_json=False, _env_file=None)  # type: ignore[call-arg]
    app = Collector.create(settings)
    for line in seed(app, fixture_url=fixture.base_url):
        print(line, flush=True)
    uvicorn.run(create_app(app), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
