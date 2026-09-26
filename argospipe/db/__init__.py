import re
import sqlite3
from importlib.resources import files
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(\d+)_.+\.sql$")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrations() -> list[tuple[int, str]]:
    found = []
    for entry in files("argospipe.db").joinpath("migrations").iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.read_text(encoding="utf-8")))
    return sorted(found)


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection) -> int:
    version = current_version(conn)
    for number, sql in _migrations():
        if number <= version:
            continue
        conn.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version VALUES ({number});\nCOMMIT;")
        version = number
    return version
