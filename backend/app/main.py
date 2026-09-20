from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from app.api import router
from app.database import Base, engine


def _ensure_run_archive_columns() -> None:
    """Idempotent migration: add archive columns to pre-existing run_projections."""
    inspector = inspect(engine)
    if "run_projections" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("run_projections")}
    statements = []
    if "archived" not in existing:
        statements.append("ALTER TABLE run_projections ADD COLUMN archived BOOLEAN NOT NULL DEFAULT FALSE")
    if "archived_at" not in existing:
        statements.append("ALTER TABLE run_projections ADD COLUMN archived_at TIMESTAMP WITH TIME ZONE")
    if statements:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_run_archive_columns()
    yield


app = FastAPI(title="Experiment Provenance Workbench", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
