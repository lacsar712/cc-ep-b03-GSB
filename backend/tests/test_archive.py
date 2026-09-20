import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cqrs import (
    ConflictError,
    DomainError,
    abort_run,
    archive_run,
    complete_run,
    list_events,
    rebuild_projection_from_events,
    start_run,
)
from app.database import Base
from app.models import RunProjection


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(_type, compiler, **kw):
        return "JSON"

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _completed_run(db, name="n1"):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name=name,
        dataset_content_sha256=sha(f"ds-{name}"),
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


def test_archive_completed_run(db):
    run = _completed_run(db)
    assert run.archived_at is None
    prev_version = run.version

    archived = archive_run(db, run_id=run.id, actor="researcher", expected_version=prev_version)
    assert archived.archived_at is not None
    assert archived.archived_by == "researcher"
    assert archived.version == prev_version + 1
    # 归档不改变终态本身
    assert archived.status == "completed"

    # 事件流水只增不减：RunArchived 追加在末尾
    events = list_events(db, run.id)
    assert [e.event_type for e in events] == ["RunStarted", "RunCompleted", "RunArchived"]
    assert events[-1].actor == "researcher"


def test_archive_running_run_rejected(db):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="running-one",
        dataset_content_sha256=sha("ds-running"),
        code_commit_sha="abc1234",
        description=None,
    )
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)


def test_archive_aborted_run_rejected(db):
    run = start_run(
        db,
        actor="researcher",
        project="p1",
        name="aborted-one",
        dataset_content_sha256=sha("ds-aborted"),
        code_commit_sha="abc1234",
        description=None,
    )
    run = abort_run(db, run_id=run.id, actor="researcher", reason="OOM", expected_version=1)
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)


def test_archive_twice_rejected(db):
    run = _completed_run(db, name="twice")
    archived = archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=archived.version)


def test_archive_optimistic_lock(db):
    run = _completed_run(db, name="lock")
    with pytest.raises(ConflictError):
        archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version + 1)


def test_archive_missing_run(db):
    from uuid import uuid4

    with pytest.raises(DomainError):
        archive_run(db, run_id=uuid4(), actor="researcher", expected_version=1)


def test_archive_survives_event_replay(db):
    run = _completed_run(db, name="replay")
    archived = archive_run(db, run_id=run.id, actor="researcher", expected_version=run.version)

    rebuilt = rebuild_projection_from_events(db, run.id)
    assert rebuilt is not None
    assert rebuilt.status == archived.status
    assert rebuilt.version == archived.version
    assert rebuilt.archived_by == "researcher"
    assert rebuilt.archived_at is not None


def test_default_list_hides_archived(db):
    visible = _completed_run(db, name="visible")
    hidden = _completed_run(db, name="hidden")
    archive_run(db, run_id=hidden.id, actor="researcher", expected_version=hidden.version)

    default_rows = list(
        db.scalars(
            select(RunProjection).where(RunProjection.archived_at.is_(None))
        ).all()
    )
    default_ids = {r.id for r in default_rows}
    assert visible.id in default_ids
    assert hidden.id not in default_ids

    all_rows = list(db.scalars(select(RunProjection)).all())
    assert {r.id for r in all_rows} == {visible.id, hidden.id}
