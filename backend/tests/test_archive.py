"""Archive feature: domain rules, event-sourcing guarantees, API filtering & RBAC."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token
from app.cqrs import (
    ConflictError,
    archive_run,
    complete_run,
    list_events,
    rebuild_projection_from_events,
    start_run,
)
from app.database import Base, get_db
from app.main import app
from app.models import EventStore, RunProjection


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, compiler, **kw):
    return "JSON"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _completed_run(db) -> RunProjection:
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="n1",
        dataset_content_sha256=sha("ds-archive"),
        code_commit_sha="abc1234",
        description=None,
    )
    return complete_run(
        db,
        run_id=run.id,
        actor="researcher",
        result_summary="done",
        expected_version=run.version,
    )


def test_archive_completed_run_appends_event(db):
    run = _completed_run(db)
    assert run.status == "completed"
    assert run.archived is False
    completed_version = run.version

    archived = archive_run(
        db, run_id=run.id, actor="researcher", expected_version=completed_version
    )
    assert archived.archived is True
    assert archived.archived_at is not None
    assert archived.version == completed_version + 1

    events = list_events(db, run.id)
    assert [e.event_type for e in events] == [
        "RunStarted",
        "RunCompleted",
        "RunArchived",
    ]
    assert events[-1].actor == "researcher"


def test_archive_running_rejected(db):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="n1",
        dataset_content_sha256=sha("ds-running"),
        code_commit_sha="abc1234",
        description=None,
    )
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)
    # no event appended on rejection
    assert len(list_events(db, run.id)) == 1
    db.refresh(run)
    assert run.archived is False


def test_archive_aborted_rejected(db):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="n1",
        dataset_content_sha256=sha("ds-aborted"),
        code_commit_sha="abc1234",
        description=None,
    )
    from app.cqrs import abort_run

    run = abort_run(
        db, run_id=run.id, actor="researcher", reason="OOM", expected_version=run.version
    )
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)


def test_archive_twice_rejected(db):
    run = _completed_run(db)
    archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)
    db.refresh(run)
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)
    # exactly one RunArchived event — nothing is ever deleted
    events = list_events(db, run.id)
    assert sum(1 for e in events if e.event_type == "RunArchived") == 1


def test_archive_optimistic_lock_conflict(db):
    run = _completed_run(db)
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=999)
    assert len(list_events(db, run.id)) == 2


def test_replay_includes_archive(db):
    run = _completed_run(db)
    archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)

    rebuilt = rebuild_projection_from_events(db, run.id)
    stored = db.get(RunProjection, run.id)
    assert rebuilt is not None
    assert rebuilt.archived is True
    assert rebuilt.archived_at is not None
    assert rebuilt.version == stored.version
    assert rebuilt.status == "completed"


def test_event_store_never_deleted(db):
    run = _completed_run(db)
    before = list(db.scalars(select(EventStore)).all())
    archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)
    after = list(db.scalars(select(EventStore)).all())
    assert len(after) == len(before) + 1
    assert {e.id for e in before} <= {e.id for e in after}


# ---------- API layer ----------


@pytest.fixture()
def client(db):
    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    # NOTE: no context manager -> lifespan (global-engine create_all) is skipped;
    # the in-memory SQLite engine from the db fixture already has all tables.
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def _auth_headers(role: str) -> dict:
    token = create_access_token(role, role)
    return {"Authorization": f"Bearer {token}"}


def test_api_list_hides_archived_by_default(client, db):
    run = _completed_run(db)
    resp = client.get("/api/runs", headers=_auth_headers("researcher"))
    assert resp.status_code == 200
    ids_default = {r["id"] for r in resp.json()}
    assert str(run.id) in ids_default

    archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)

    resp = client.get("/api/runs", headers=_auth_headers("researcher"))
    ids_after = {r["id"] for r in resp.json()}
    assert str(run.id) not in ids_after

    resp = client.get(
        "/api/runs?include_archived=true", headers=_auth_headers("researcher")
    )
    ids_included = {r["id"] for r in resp.json()}
    assert str(run.id) in ids_included
    arch_row = next(r for r in resp.json() if r["id"] == str(run.id))
    assert arch_row["archived"] is True
    assert arch_row["archived_at"] is not None


def test_api_archive_endpoint_researcher_only(client, db):
    run = _completed_run(db)

    # auditor is read-only
    resp = client.post(
        f"/api/runs/{run.id}/archive",
        json={"expected_version": run.version},
        headers=_auth_headers("auditor"),
    )
    assert resp.status_code == 403

    # unauthenticated
    resp = client.post(
        f"/api/runs/{run.id}/archive", json={"expected_version": run.version}
    )
    assert resp.status_code in (401, 403)

    # researcher succeeds
    resp = client.post(
        f"/api/runs/{run.id}/archive",
        json={"expected_version": run.version},
        headers=_auth_headers("researcher"),
    )
    assert resp.status_code == 200
    assert resp.json()["archived"] is True


def test_api_archive_running_returns_conflict(client, db):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="live",
        dataset_content_sha256=sha("ds-live"),
        code_commit_sha="abc1234",
        description=None,
    )
    resp = client.post(
        f"/api/runs/{run.id}/archive",
        json={"expected_version": run.version},
        headers=_auth_headers("researcher"),
    )
    assert resp.status_code == 409


def test_api_archived_run_detail_and_lineage_still_readable(client, db):
    run = _completed_run(db)
    client.post(
        f"/api/runs/{run.id}/archive",
        json={"expected_version": run.version},
        headers=_auth_headers("researcher"),
    )

    detail = client.get(f"/api/runs/{run.id}", headers=_auth_headers("auditor"))
    assert detail.status_code == 200
    assert detail.json()["archived"] is True

    lineage = client.get(f"/api/runs/{run.id}/lineage", headers=_auth_headers("auditor"))
    assert lineage.status_code == 200
    assert lineage.json()["archived"] is True

    events = client.get(f"/api/runs/{run.id}/events", headers=_auth_headers("auditor"))
    assert events.status_code == 200
    assert events.json()[-1]["event_type"] == "RunArchived"
