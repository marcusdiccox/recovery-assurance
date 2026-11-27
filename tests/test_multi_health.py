"""A monitoring probe must assess every explicitly required dataset."""

import json
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store


@pytest.fixture
def history(tmp_path):
    source = tmp_path / "synthetic"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic")
    template = run_drill(capture(source), source, Store(tmp_path / "seed.db"), sample=True)
    return Store(tmp_path / "history #100% space.db"), template


def add(store, template, dataset, *, status="passed", age=1, **fields):
    finished = datetime.now(UTC) - timedelta(hours=age)
    store.add(
        {
            **template,
            "id": f"run-{dataset}",
            "dataset": dataset,
            "sample": False,
            "status": status,
            "integrity_ok": status == "passed",
            "rto_met": status == "passed",
            "started_at": (finished - timedelta(minutes=1)).isoformat(),
            "finished_at": finished.isoformat(),
            **fields,
        }
    )


def cli(database, *datasets, hours="24"):
    arguments = [sys.executable, "-m", "recovery_assurance", "check-many"]
    for dataset in datasets:
        arguments.extend(["--dataset", dataset])
    return subprocess.run(
        [*arguments, "--max-age-hours", hours, "--db", str(database)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


def test_all_required_datasets_must_have_fresh_passing_evidence(history):
    store, template = history
    add(store, template, "finance")
    add(store, template, "operations")
    result = cli(store.path, "operations", "finance")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema_version"] == 1
    assert data["healthy"] is True
    assert data["max_age_hours"] == 24
    assert [item["dataset"] for item in data["results"]] == ["operations", "finance"]
    assert all(item["healthy"] and item["reasons"] == [] for item in data["results"])
    assert result.stderr == ""


@pytest.mark.parametrize("problem", ["failed", "error", "stale", "missing", "sample-only"])
def test_one_unhealthy_required_dataset_prevents_overall_success(history, problem):
    store, template = history
    add(store, template, "finance")
    if problem != "missing":
        add(
            store,
            template,
            "operations",
            status=problem if problem in ("failed", "error") else "passed",
            age=25 if problem == "stale" else 1,
            sample=problem == "sample-only",
        )
    result = cli(store.path, "finance", "operations")
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["healthy"] is False
    assert len(data["results"]) == 2
    assert data["results"][0]["healthy"] is True
    second = data["results"][1]
    assert second["healthy"] is False
    expected = {"missing": "no_evidence", "sample-only": "no_evidence"}.get(
        problem, "stale" if problem == "stale" else f"latest_run_{problem}"
    )
    assert second["reasons"] == [expected]


def test_repeated_labels_are_checked_once_without_case_normalization(history):
    store, template = history
    add(store, template, "finance")
    add(store, template, "Finance", status="failed")
    result = cli(store.path, "finance", "finance", "Finance")
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert [item["dataset"] for item in data["results"]] == ["finance", "Finance"]


def test_empty_history_cannot_produce_healthy_success(history):
    store, _ = history
    result = cli(store.path, "finance", "never-recorded")
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["healthy"] is False
    assert [item["reasons"] for item in data["results"]] == [["no_evidence"], ["no_evidence"]]


def test_invalid_later_evidence_returns_error_without_partial_success(history):
    store, template = history
    add(store, template, "finance")
    add(store, template, "operations", finished_at="invalid timestamp")
    original = store.path.read_bytes()
    result = cli(store.path, "finance", "operations")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert store.path.read_bytes() == original


@pytest.mark.parametrize("protected", [False, True])
def test_multi_check_preserves_prepared_archives(history, protected):
    store, template = history
    add(store, template, "finance")
    add(store, template, "operations")
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    original = store.path.read_bytes()
    contents = sorted(store.path.parent.iterdir())
    permissions = stat.S_IMODE(store.path.stat().st_mode)
    if protected:
        store.path.chmod(stat.S_IREAD)
    try:
        result = cli(store.path, "finance", "operations")
    finally:
        store.path.chmod(permissions)
    assert result.returncode == 0, result.stderr
    assert store.path.read_bytes() == original
    assert sorted(store.path.parent.iterdir()) == contents


@pytest.mark.parametrize("kind", ["missing", "empty-file", "unrelated", "corrupt"])
def test_invalid_history_is_not_created_or_initialized(tmp_path, kind):
    path = tmp_path / "private" / "runs.db"
    if kind != "missing":
        path.parent.mkdir()
        if kind == "empty-file":
            path.touch()
        elif kind == "corrupt":
            path.write_bytes(b"not SQLite")
        else:
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
    original = path.read_bytes() if path.exists() else None
    result = cli(path, "finance", "operations")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    if original is None:
        assert not path.parent.exists()
    else:
        assert path.read_bytes() == original


@pytest.mark.parametrize("datasets", [[], ["finance", 42], ["bad\x00label"], "finance"])
def test_api_rejects_empty_or_invalid_selection_before_reading_history(tmp_path, datasets):
    from recovery_assurance.health import check_datasets

    store = Store(tmp_path / "absent" / "runs.db", read_only=True)
    with pytest.raises(ValueError, match="dataset"):
        check_datasets(store, datasets=datasets, max_age_hours=24)
    assert not store.path.parent.exists()


@pytest.mark.parametrize("hours", ["0", "nan"])
def test_multi_check_rejects_invalid_age_policy(history, hours):
    store, template = history
    add(store, template, "finance")
    result = cli(store.path, "finance", "operations", hours=hours)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("Error:")
    assert "finite and positive" in result.stderr
