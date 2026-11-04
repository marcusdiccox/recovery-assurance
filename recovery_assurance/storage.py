"""Append-only run storage. Each operation owns its SQLite connection."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, report TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, report: dict) -> None:
        encoded = json.dumps(report, allow_nan=False)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?)", (report["id"], report["started_at"], encoded)
            )

    def list(self, limit: int = 200) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT report FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, run_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT report FROM runs WHERE id = ?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None
