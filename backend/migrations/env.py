from alembic import context
from sqlalchemy import engine_from_config, pool

from dp_agentic_collector.adapters.sql.schema import metadata
from dp_agentic_collector.config import Settings

config = context.config
url = config.attributes.get("url") or Settings().database_url


def run_offline() -> None:
    context.configure(url=url, target_metadata=metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        engine = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool)
        with engine.connect() as conn:
            context.configure(connection=conn, target_metadata=metadata, render_as_batch=True)
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connectable, target_metadata=metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
