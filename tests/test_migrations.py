"""Data-migration logic that a fresh-database `alembic upgrade head` (CI) cannot exercise."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

MIGRATION = next(
    Path("alembic/versions").glob("*_message_idempotency_and_terminal_failure.py")
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("idempotency_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dedupe_keeps_the_newest_row_of_each_duplicate_group_and_allows_the_constraint() -> None:
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE messages (id TEXT PRIMARY KEY, project_id TEXT, "
                "broker_message_id TEXT, received_at TEXT)"
            )
        )
        rows = [
            # task t1 of project p1 was inserted once per retry (pre-#35 behaviour)
            ("a", "p1", "t1", "2026-09-01 10:00:00"),
            ("b", "p1", "t1", "2026-09-01 10:01:00"),
            ("c", "p1", "t1", "2026-09-01 10:02:00"),
            ("d", "p1", "t2", "2026-09-01 11:00:00"),  # unique: untouched
            ("e", "p2", "t1", "2026-09-01 12:00:00"),  # same task id, other project: untouched
            ("f", "p1", None, "2026-09-01 13:00:00"),  # no key: untouched
        ]
        for row in rows:
            conn.execute(
                sa.text("INSERT INTO messages VALUES (:id, :p, :b, :r)"),
                {"id": row[0], "p": row[1], "b": row[2], "r": row[3]},
            )

        conn.execute(sa.text(migration.DEDUPE_SQL))

        kept = {
            row[0]: row[1]
            for row in conn.execute(sa.text("SELECT id, broker_message_id FROM messages"))
        }
        assert kept == {"a": None, "b": None, "c": "t1", "d": "t2", "e": "t1", "f": None}
        # no row was deleted, and the constraint can now be created
        conn.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq ON messages (project_id, broker_message_id)"
            )
        )
