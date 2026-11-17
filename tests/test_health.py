"""Saved-evidence monitoring must never mistake samples or old successes for health."""

import json
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store


@pytest.fixture
def history(tmp_path):
    source = tmp_path / "synthetic"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic")
    store = Store(tmp_path / "history #100% space.db")
    template = run_drill(capture(source), source, store, dataset="fixture")
    return store, template


def report(template, *, identifier="real", status="passed", age=1, **fields):
    finished = datetime.now(UTC) - timedelta(hours=age)
    return {
        **template,
        "id": identifier,
        "dataset": "finance",
        "sample": False,
        "status": status,
        "integrity_ok": status == "passed",
        "rto_met": status == "passed",
        "started_at": (finished - timedelta(minutes=1)).isoformat(),
        "finished_at": finished.isoformat(),
        **fields,
    }


def cli(database, *, dataset="finance", hours="24"):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "recovery_assurance",
            "check",
            "--dataset",
            dataset,
            "--max-age-hours",
            hours,
            "--db",
            str(database),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize(
    ("status", "age", "code", "reasons"),
    [
        ("passed", 1, 0, []),
        ("failed", 1, 1, ["latest_run_failed"]),
        ("error", 1, 1, ["latest_run_error"]),
        ("passed", 25, 1, ["stale"]),
    ],
)
def test_check_reports_actual_latest_outcome_and_age(history, status, age, code, reasons):
    store, template = history
    saved = report(template, status=status, age=age)
    store.add(saved)
    result = cli(store.path)
    assert result.returncode == code, result.stderr
    data = json.loads(result.stdout)
    assert data["schema_version"] == 1
    assert data["healthy"] is (code == 0)
    assert data["reasons"] == reasons
    assert data["dataset"] == "finance"
    assert data["run_id"] == saved["id"]
    assert data["run_status"] == status
    assert data["finished_at"] == saved["finished_at"]
    assert age <= data["age_hours"] < age + 0.01
    assert data["max_age_hours"] == 24
    assert result.stderr == ""
    assert len(store.list()) == 2


def test_latest_real_query_filters_before_limit_and_breaks_ties(history):
    store, template = history
    current = report(template, identifier="z-current", status="failed", age=25)
    store.add(report(template, identifier="older-pass", age=48))
    store.add({**current, "id": "a-current", "status": "passed"})
    store.add(current)
    for index in range(206):
        store.add(report(template, identifier=f"sample-{index}", age=0, sample=True))
    store.add(report(template, identifier="other", age=0, dataset="operations"))
    result = cli(store.path)
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["run_id"] == "z-current"
    assert data["reasons"] == ["stale", "latest_run_failed"]


@pytest.mark.parametrize("kind", ["no-matching-dataset", "sample-only", "unmarked-legacy"])
def test_check_requires_explicit_nonsample_evidence(history, kind):
    store, template = history
    if kind != "no-matching-dataset":
        saved = report(template, sample=True)
        if kind == "unmarked-legacy":
            del saved["sample"]
        store.add(saved)
    result = cli(store.path)
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["healthy"] is False
    assert data["reasons"] == ["no_evidence"]
    assert data["run_id"] is None
    assert data["age_hours"] is None


@pytest.mark.parametrize("protected", [False, True])
def test_check_preserves_existing_archive(history, protected):
    store, template = history
    store.add(report(template))
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


@pytest.mark.parametrize("hours", ["0", "-1", "nan", "inf"])
def test_invalid_age_policy_returns_error_without_history_changes(history, hours):
    store, template = history
    store.add(report(template))
    original = store.path.read_bytes()
    result = cli(store.path, hours=hours)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "finite and positive" in result.stderr
    assert result.stdout == ""
    assert store.path.read_bytes() == original


@pytest.mark.parametrize("kind", ["missing", "corrupt", "empty-file", "unrelated"])
def test_unusable_history_returns_error_without_initialization(tmp_path, kind):
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


@pytest.mark.parametrize(
    "kind", ["missing", "naive", "invalid", "future", "reversed", "status", "contradictory"]
)
def test_unusable_latest_evidence_is_not_replaced_by_an_older_success(history, kind):
    store, template = history
    store.add(report(template, identifier="older", age=2))
    latest = report(template)
    if kind == "missing":
        del latest["finished_at"]
    elif kind == "naive":
        latest["finished_at"] = "2026-01-01T00:00:00"
    elif kind == "invalid":
        latest["finished_at"] = "unknown"
    elif kind == "future":
        latest["finished_at"] = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    elif kind == "reversed":
        latest["finished_at"] = (
            datetime.fromisoformat(latest["started_at"]) - timedelta(seconds=1)
        ).isoformat()
    elif kind == "status":
        latest["status"] = "pending"
    else:
        latest["integrity_ok"] = False
    store.add(latest)
    result = cli(store.path)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("dataset", ["Finance", "finance' OR 1=1 --"])
def test_check_uses_exact_bound_dataset_labels(history, dataset):
    store, template = history
    store.add(report(template, identifier="wrong-label", status="failed", age=0.5))
    store.add(report(template, identifier="literal", dataset=dataset))
    result = cli(store.path, dataset=dataset)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["run_id"] == "literal"


@pytest.mark.parametrize("field", ["dataset", "sample"])
def test_ambiguous_selection_metadata_cannot_report_healthy(history, field):
    store, template = history
    store.add(report(template, identifier="older", age=2))
    latest = report(template, **{field: "operations" if field == "dataset" else True})
    # Simulate a hand-edited archive: SQLite and Python resolve duplicate keys differently.
    prefix = json.dumps({field: "finance" if field == "dataset" else False})[1:-1]
    raw = "{" + prefix + "," + json.dumps(latest)[1:]
    with closing(sqlite3.connect(store.path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?)",
                (latest["id"], latest["started_at"], raw),
            )
    original = store.path.read_bytes()
    result = cli(store.path)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert result.stdout == ""
    assert store.path.read_bytes() == original


@pytest.mark.parametrize(("seconds", "healthy"), [(3600, True), (3601, False)])
def test_age_boundary_and_offset_timestamp_are_evaluated_in_utc(history, seconds, healthy):
    from recovery_assurance.health import check_health

    store, template = history
    now = datetime(2026, 1, 2, 12, tzinfo=UTC)
    finished = now - timedelta(seconds=seconds)
    offset = timezone(timedelta(hours=5, minutes=30))
    store.add(
        report(
            template,
            started_at=(finished - timedelta(minutes=1)).isoformat(),
            finished_at=finished.astimezone(offset).isoformat(),
        )
    )
    data = check_health(store, dataset="finance", max_age_hours=1, now=now)
    assert data["healthy"] is healthy
    assert data["reasons"] == ([] if healthy else ["stale"])
    assert data["age_hours"] == seconds / 3600
