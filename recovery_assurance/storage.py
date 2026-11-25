"""Append-only run storage. Each operation owns its SQLite connection."""

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import quote


class HistoryCursorNotFound(ValueError):
    """The requested pagination anchor does not exist in this history."""


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

    def backup_to(self, output: Path) -> None:
        """Create a new standalone history copy without changing the source."""
        output = Path(output).absolute()
        source_path = self.path.resolve()
        suffixes = ("-journal", "-wal", "-shm")
        overlaps = any(
            Path(output.name) == Path(source_path.name + suffix)
            or Path(source_path.name) == Path(output.name + suffix)
            for suffix in suffixes
        )
        if overlaps and output.parent.samefile(source_path.parent):
            raise ValueError("Source and output SQLite sidecar paths must not overlap")
        for suffix in suffixes:
            companion = output.with_name(output.name + suffix)
            if companion.exists() or companion.is_symlink():
                raise FileExistsError("Output has an existing SQLite sidecar")
        with self._connect() as source:
            source.execute("SELECT id, started_at, report FROM runs LIMIT 0").close()
            with output.open("xb"):
                pass
            try:
                deadline = time.monotonic() + 15

                def progress(_status, _remaining, _total):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("History backup exceeded the 15-second copy budget")

                with closing(sqlite3.connect(output, timeout=15)) as destination:
                    source.backup(destination, pages=128, progress=progress, sleep=0.05)
                    mode = destination.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                    if mode != "delete":
                        raise sqlite3.OperationalError(
                            "Unable to finalize standalone history backup"
                        )
            except BaseException:
                output.unlink()
                raise

    def list(
        self, limit: int = 200, *, dataset: str | None = None, before: str | None = None
    ) -> list[dict]:
        query = "SELECT report FROM runs"
        predicates = []
        parameters = []
        if dataset is not None:
            predicates.append("json_extract(report, '$.dataset') = ?")
            parameters.append(dataset)
        with self._connect() as connection:
            if before is not None:
                if before == "":
                    raise HistoryCursorNotFound("History cursor run not found")
                cursor = connection.execute(
                    "SELECT started_at, id FROM runs WHERE id = ?", (before,)
                ).fetchone()
                if cursor is None:
                    raise HistoryCursorNotFound("History cursor run not found")
                predicates.append("(started_at, id) < (?, ?)")
                parameters.extend(cursor)
            if predicates:
                query += " WHERE " + " AND ".join(predicates)
            query += " ORDER BY started_at DESC, id DESC LIMIT ?"
            parameters.append(limit)
            rows = connection.execute(query, parameters).fetchall()
        return [json.loads(row[0]) for row in rows]

    def dataset_inventory(self) -> "list[dict]":
        """Summarize all real labelled evidence without the history page limit."""
        inventory = {}
        with self._connect() as connection:
            for run_id, started_at, encoded in connection.execute(
                "SELECT id, started_at, report FROM runs"
            ):
                if not isinstance(encoded, (str, bytes, bytearray)):
                    raise ValueError("History contains an invalid report")
                report = json.loads(encoded)
                if not isinstance(report, dict):
                    raise ValueError("History report must be an object")
                dataset = report.get("dataset")
                if (
                    report.get("sample") is not False
                    or not isinstance(dataset, str)
                    or "\x00" in dataset
                ):
                    continue
                if not isinstance(run_id, str) or not isinstance(started_at, str):
                    raise ValueError("History contains invalid run ordering metadata")
                if dataset not in inventory:
                    inventory[dataset] = {
                        "dataset": dataset,
                        "run_count": 0,
                        "latest_run_id": run_id,
                        "latest_started_at": started_at,
                        "latest_status": report.get("status"),
                    }
                item = inventory[dataset]
                item["run_count"] += 1
                if (started_at, run_id) > (item["latest_started_at"], item["latest_run_id"]):
                    item.update(
                        latest_run_id=run_id,
                        latest_started_at=started_at,
                        latest_status=report.get("status"),
                    )
        return [inventory[dataset] for dataset in sorted(inventory)]

    def latest_real_run(self, dataset: str) -> dict | None:
        """Find real evidence before limiting, using the same ordering as history."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT report FROM runs WHERE json_extract(report, '$.dataset') = ? "
                "AND json_type(report, '$.sample') = 'false' "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (dataset,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, run_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT report FROM runs WHERE id = ?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None
