from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def ensure_schema_upgrades() -> None:
    """Add columns introduced after initial deploys (create_all only creates missing tables)."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE run_projections ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")
        )
        conn.execute(
            text("ALTER TABLE run_projections ADD COLUMN IF NOT EXISTS archived_by VARCHAR(64)")
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
