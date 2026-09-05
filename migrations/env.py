from alembic import context

from app.core.config import Settings
from app.db.models import Base
from app.db.session import build_engine

target_metadata = Base.metadata
url = context.config.attributes.get("database_url") or Settings().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = build_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
