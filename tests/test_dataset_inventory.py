"""Dataset discovery reads complete real history without initializing or changing it."""

import json
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store


@pytest.fixture
def history(tmp_path):
    source = tmp_path / "synthetic"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic")
    template = run_drill(capture(source), source, Store(tmp_path / "seed.db"), sample=True)
    directory = tmp_path / "archivé #100% space"
    directory.mkdir()
    return Store(directory / "runs.db"), template


def record(template, identifier, *, dataset="finance", started="2026-01-01", **fields):
    return {
        **template,
        "id": identifier,
        "dataset": dataset,
        "sample": False,
        "started_at": started,
        **fields,
    }


def cli(database):
    return subprocess.run(
        [sys.executable, "-m", "recovery_assurance", "datasets", "--db", str(database)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


def test_inventory_counts_full_history_and_selects_latest_stored_tuple(history):
    store, template = history
    for index in range(202):
        store.add(record(template, f"old-{index:03}"))
    store.add(record(template, "a-current", started="2026-01-02"))
    latest = record(template, "z-current", started="2026-01-02", status="error")
    store.add(latest)
    # The stored ordering columns remain authoritative, as in history pagination.
    with closing(sqlite3.connect(store.path)) as connection:
        with connection:
            connection.execute(
                "UPDATE runs SET report = ? WHERE id = ?",
                (json.dumps({**latest, "started_at": "2000-01-01"}), latest["id"]),
            )
    labels = ["Finance", "équipe", "finance' OR 1=1 --", ""]
    for index, label in enumerate(labels):
        store.add(record(template, f"label-{index}", dataset=label))
    for index in range(205):
        store.add(record(template, f"sample-{index:03}", started="2026-01-03", sample=True))
    expected = [
        {
            "dataset": label,
            "run_count": 1,
            "latest_run_id": f"label-{index}",
            "latest_started_at": "2026-01-01",
            "latest_status": "passed",
        }
        for index, label in enumerate(labels)
    ] + [
        {
            "dataset": "finance",
            "run_count": 204,
            "latest_run_id": "z-current",
            "latest_started_at": "2026-01-02",
            "latest_status": "error",
        }
    ]
    expected.sort(key=lambda item: item["dataset"])
    assert Store(store.path, read_only=True).dataset_inventory() == expected
    result = cli(store.path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert result.stderr == ""


def test_inventory_excludes_samples_unmarked_legacy_and_invalid_labels(history):
    store, template = history
    store.add(record(template, "real"))
    for index, marker in enumerate([True, 0, "false", None]):
        store.add(record(template, f"marker-{index}", sample=marker))
    legacy = record(template, "legacy")
    del legacy["sample"]
    store.add(legacy)
    for index, label in enumerate([None, 42, [], "bad\x00label"]):
        store.add(record(template, f"invalid-{index}", dataset=label))
    unlabelled = record(template, "unlabelled")
    del unlabelled["dataset"]
    store.add(unlabelled)
    result = cli(store.path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["dataset"] == "finance"
    assert data[0]["run_count"] == 1
    assert data[0]["latest_run_id"] == "real"


@pytest.mark.parametrize("protected", [False, True])
def test_inventory_preserves_archive_bytes_and_directory_contents(history, protected):
    store, template = history
    store.add(record(template, "real"))
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    original = store.path.read_bytes()
    contents = sorted(store.path.parent.iterdir())
    permissions = stat.S_IMODE(store.path.stat().st_mode)
    if protected:
        store.path.chmod(stat.S_IREAD)
    try:
        result = cli(store.path)
    finally:
        store.path.chmod(permissions)
    assert result.returncode == 0, result.stderr
    assert store.path.read_bytes() == original
    assert sorted(store.path.parent.iterdir()) == contents


@pytest.mark.parametrize("kind", ["empty", "samples-only"])
def test_readable_history_without_real_labels_returns_empty_inventory(history, kind):
    store, template = history
    if kind == "samples-only":
        store.add(record(template, "sample", sample=True))
    result = cli(store.path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


@pytest.mark.parametrize("kind", ["missing", "empty-file", "unrelated", "corrupt"])
def test_unusable_inventory_source_returns_error_without_initialization(tmp_path, kind):
    database = tmp_path / "private" / "runs.db"
    if kind != "missing":
        database.parent.mkdir()
        if kind == "corrupt":
            database.write_bytes(b"not SQLite")
        elif kind == "empty-file":
            database.touch()
        else:
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
    original = database.read_bytes() if database.exists() else None
    result = cli(database)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    if original is None:
        assert not database.parent.exists()
    else:
        assert database.read_bytes() == original


@pytest.mark.parametrize("raw", ["{invalid JSON", "[]"])
def test_unusable_history_report_fails_clearly(history, raw):
    store, template = history
    store.add(record(template, "real"))
    with closing(sqlite3.connect(store.path)) as connection:
        with connection:
            connection.execute("UPDATE runs SET report = ?", (raw,))
    result = cli(store.path)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("column", ["id", "started_at"])
def test_invalid_stored_ordering_types_return_a_clear_error(history, column):
    store, template = history
    store.add(record(template, "real"))
    with closing(sqlite3.connect(store.path)) as connection:
        with connection:
            # Column names are test constants, never user input.
            connection.execute(f"UPDATE runs SET {column} = ?", (b"invalid BLOB",))
    result = cli(store.path)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("field", ["dataset", "sample"])
def test_inventory_uses_one_json_interpretation_for_grouping(history, field):
    store, template = history
    actual = record(template, "ambiguous", **{field: "operations" if field == "dataset" else True})
    prefix = json.dumps({field: "finance" if field == "dataset" else False})[1:-1]
    raw = "{" + prefix + "," + json.dumps(actual)[1:]
    with closing(sqlite3.connect(store.path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?)", (actual["id"], actual["started_at"], raw)
            )
    result = cli(store.path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [row["dataset"] for row in data] == (["operations"] if field == "dataset" else [])
