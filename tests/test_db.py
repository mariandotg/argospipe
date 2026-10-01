from pathlib import Path

from argospipe import db

TABLES = {"jobs", "job_sources", "runs", "run_jobs", "matches", "schema_version"}


def _tables(path: Path) -> set[str]:
    conn = db.connect(path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_new_db_reaches_latest_version(tmp_path: Path) -> None:
    path = tmp_path / "argospipe.db"
    version = db.migrate(db.connect(path))
    assert version == 1
    assert _tables(path) >= TABLES


def test_migrate_twice_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "argospipe.db"
    db.migrate(db.connect(path))
    conn = db.connect(path)
    assert db.migrate(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
