from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from dp_agentic_collector.adapters.sql.schema import metadata

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{tmp_path}/m.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == [], diff
