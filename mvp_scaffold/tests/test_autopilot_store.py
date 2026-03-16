from pathlib import Path

from src.autopilot_store import AutopilotStore
from src.db import Database


def _setup_store(tmp_path: Path) -> tuple[Database, AutopilotStore]:
    repo_root = Path(__file__).resolve().parents[2]
    schema_path = repo_root / "schema.sql"
    migrations_dir = repo_root / "mvp_scaffold" / "migrations"
    db_path = tmp_path / "app.db"

    db = Database(path=db_path, schema_path=schema_path, migrations_dir=migrations_dir)
    db.connect()
    db.initialize_schema()

    connection = db.get_connection()
    connection.execute(
        """
        INSERT INTO users (id, telegram_user_id, telegram_username, display_name)
        VALUES (1, '123', 'tester', 'Tester')
        """
    )
    connection.execute(
        """
        INSERT INTO projects (id, project_key, name, path)
        VALUES (1, 'demo', 'Demo', '/tmp')
        """
    )
    connection.commit()
    return db, AutopilotStore(db)


def test_create_and_update_autopilot_run(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)

    run_id = store.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复任务",
    )
    updated = store.update_run(
        run_id=run_id,
        status="running_worker",
        supervisor_session_id="sess-supervisor-1",
        worker_session_id="sess-worker-1",
        current_phase="worker",
        cycle_count=1,
        last_decision="continue",
        last_worker_summary="完成第一轮修复",
    )

    assert updated is True

    row = db.get_connection().execute(
        """
        SELECT status, supervisor_session_id, worker_session_id, current_phase, cycle_count, last_decision
        FROM autopilot_runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "running_worker"
    assert row["supervisor_session_id"] == "sess-supervisor-1"
    assert row["worker_session_id"] == "sess-worker-1"
    assert row["current_phase"] == "worker"
    assert int(row["cycle_count"]) == 1
    assert row["last_decision"] == "continue"


def test_autopilot_events_round_trip_payload(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    run_id = store.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="推进支付修复",
    )

    event_id = store.append_event(
        run_id=run_id,
        cycle_no=1,
        actor="worker",
        event_type="stage_completed",
        summary="修复支付回调逻辑",
        payload={"progress_made": True, "task_complete": False},
    )

    events = store.list_events(run_id=run_id)

    assert len(events) == 1
    assert events[0].id == event_id
    assert events[0].actor == "worker"
    assert events[0].payload == {"progress_made": True, "task_complete": False}


def test_list_runs_for_project_orders_latest_first(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    older_id = store.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="older",
    )
    newer_id = store.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="newer",
    )

    runs = store.list_runs_for_project(project_id=1)

    assert runs[0].id == newer_id
    assert runs[1].id == older_id


def test_autopilot_stream_chunks_round_trip(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    run_id = store.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="推进信息收集",
    )

    chunk_id = store.append_stream_chunk(
        run_id=run_id,
        cycle_no=1,
        actor="worker",
        channel="stderr",
        content="mcp github starting",
    )

    chunks = store.list_stream_chunks(run_id=run_id)

    assert len(chunks) == 1
    assert chunks[0].id == chunk_id
    assert chunks[0].cycle_no == 1
    assert chunks[0].actor == "worker"
    assert chunks[0].channel == "stderr"
    assert chunks[0].content == "mcp github starting"
