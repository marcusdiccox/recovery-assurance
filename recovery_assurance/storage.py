"""Append-only run storage. Each operation owns its SQLite connection."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


class Store:
    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs "
                "(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, report TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self):
        if self.read_only:
            # Preserve native UNC/extended paths without introducing a URI authority.
            uri = "file:" + quote(str(self.path.resolve()), safe="/:") + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=15)
        else:
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

    def list(self, limit: int = 200, *, dataset: str | None = None) -> list[dict]:
        query = "SELECT report FROM runs"
        parameters = []
        if dataset is not None:
            query += " WHERE json_extract(report, '$.dataset') = ?"
            parameters.append(dataset)
        query += " ORDER BY started_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, run_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT report FROM runs WHERE id = ?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None
