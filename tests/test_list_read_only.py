"""Listing archived evidence must not initialize or rewrite an existing database."""

import json
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store


def cli_list(database, *arguments):
    return subprocess.run(
        [sys.executable, "-m", "recovery_assurance", "list", "--db", str(database), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture
def archive(tmp_path):
    source = tmp_path / "synthetic"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic")
    template = run_drill(capture(source), source, Store(tmp_path / "fixture.db"), sample=True)
    directory = tmp_path / "archivé #100% space"
    directory.mkdir()
    path = directory / "history.db"
    reports = [
        {**template, "id": "r3", "started_at": "2026-01-03", "dataset": "finance"},
        {**template, "id": "r2", "started_at": "2026-01-02", "dataset": "operations"},
        {**template, "id": "r1", "started_at": "2026-01-01", "dataset": "finance"},
        {**template, "id": "r0", "started_at": "2025-12-31"},
    ]
    del reports[-1]["dataset"]
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE runs "
                "(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, report TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO runs VALUES (?, ?, ?)",
                [(r["id"], r["started_at"], json.dumps(r)) for r in reports],
            )
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    return path, reports


@pytest.mark.parametrize("write_protected", [False, True])
@pytest.mark.parametrize(
    ("arguments", "ids"),
    [
        ([], ["r3", "r2", "r1", "r0"]),
        (["--dataset", "finance"], ["r3", "r1"]),
        (["--dataset", "finance", "--before", "r3"], ["r1"]),
    ],
    ids=["combined", "filtered", "paged"],
)
def test_existing_history_is_listed_without_archive_changes(
    archive, write_protected, arguments, ids
):
    path, reports = archive
    original = path.read_bytes()
    permissions = stat.S_IMODE(path.stat().st_mode)
    if write_protected:
        path.chmod(stat.S_IREAD)
    try:
        result = cli_list(path, *arguments)
    finally:
        path.chmod(permissions)
    assert result.returncode == 0, result.stderr
    expected = [report for report in reports if report["id"] in ids]
    assert json.loads(result.stdout) == expected
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("cursor", ["unknown", ""])
def test_rejected_list_cursor_does_not_change_archive(archive, cursor):
    path, _ = archive
    original = path.read_bytes()
    result = cli_list(path, "--before", cursor)
    assert result.returncode == 2
    assert "History cursor run not found" in result.stderr
    assert result.stdout == ""
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["unrelated", "empty-file"])
def test_existing_nonhistory_file_is_not_initialized_by_list(tmp_path, kind):
    path = tmp_path / "unrelated.db"
    if kind == "empty-file":
        path.write_bytes(b"")
    else:
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
    original = path.read_bytes()
    result = cli_list(path)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert path.read_bytes() == original


def test_first_list_still_initializes_a_missing_history_path(tmp_path):
    path = tmp_path / "new-history" / "runs.db"
    result = cli_list(path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
    assert Store(path, read_only=True).list() == []
