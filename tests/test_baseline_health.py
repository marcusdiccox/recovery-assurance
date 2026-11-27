"""A fresh recovery against a different baseline cannot satisfy a pinned policy."""

import json
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from recovery_assurance.core import capture, run_drill, save_manifest
from recovery_assurance.health import check_health
from recovery_assurance.storage import Store


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "synthetic"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic backup")
    manifest = capture(source)
    baseline = tmp_path / "expected baseline.json"
    save_manifest(manifest, baseline)
    store = Store(tmp_path / "history #100% space.db")
    report = run_drill(manifest, source, store, dataset="finance")
    assert report["status"] == "passed"
    return store, baseline, manifest, report


def cli(database, baseline=None):
    arguments = [
        sys.executable,
        "-m",
        "recovery_assurance",
        "check",
        "--dataset",
        "finance",
        "--max-age-hours",
        "24",
        "--db",
        str(database),
    ]
    if baseline is not None:
        arguments.extend(["--manifest", str(baseline)])
    return subprocess.run(
        arguments, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False
    )


def test_matching_baseline_passes_and_exposes_both_fingerprints(evidence):
    store, baseline, _, report = evidence
    result = cli(store.path, baseline)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["healthy"] is True
    assert data["reasons"] == []
    assert data["expected_baseline_sha256"] == report["baseline_sha256"]
    assert data["baseline_sha256"] == report["baseline_sha256"]
    assert data["run_id"] == report["id"]
    assert result.stderr == ""
    assert len(store.list()) == 1


@pytest.mark.parametrize("change", ["content", "annotation", "created_at"])
def test_changed_baseline_cannot_use_a_fresh_passing_run(evidence, change):
    store, baseline, manifest, report = evidence
    if change == "content":
        manifest["files"]["record.txt"]["sha256"] = "a" * 64
    elif change == "annotation":
        manifest["files"]["record.txt"]["note"] = "new baseline revision"
    else:
        manifest["created_at"] = "2026-01-01T00:00:00+00:00"
    baseline.write_text(json.dumps(manifest), encoding="utf-8")
    result = cli(store.path, baseline)
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["healthy"] is False
    assert data["reasons"] == ["baseline_mismatch"]
    assert data["baseline_sha256"] == report["baseline_sha256"]
    assert data["expected_baseline_sha256"] != data["baseline_sha256"]
    assert data["run_id"] == report["id"]


def test_json_formatting_key_order_and_bom_do_not_change_the_fingerprint(evidence):
    store, baseline, manifest, report = evidence
    reordered = dict(reversed(list(manifest.items())))
    reordered["files"]["record.txt"] = dict(
        reversed(list(reordered["files"]["record.txt"].items()))
    )
    baseline.write_text(json.dumps(reordered, indent=4), encoding="utf-8-sig")
    result = cli(store.path, baseline)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["expected_baseline_sha256"] == report["baseline_sha256"]


def test_newest_different_baseline_is_not_replaced_by_older_matching_evidence(evidence):
    store, baseline, _, report = evidence
    later = datetime.fromisoformat(report["started_at"]) + timedelta(microseconds=1)
    store.add(
        {
            **report,
            "id": "newer-baseline",
            "started_at": later.isoformat(),
            "baseline_sha256": "a" * 64,
        }
    )
    result = cli(store.path, baseline)
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["run_id"] == "newer-baseline"
    assert data["reasons"] == ["baseline_mismatch"]


@pytest.mark.parametrize("status", ["passed", "failed", "error"])
@pytest.mark.parametrize("matching", [False, True])
def test_baseline_policy_preserves_age_and_outcome_reasons(evidence, status, matching):
    store, baseline, manifest, report = evidence
    finished = datetime(2026, 1, 2, tzinfo=UTC)
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DELETE FROM runs")
        connection.commit()
    store.add(
        {
            **report,
            "id": "stale",
            "started_at": (finished - timedelta(minutes=1)).isoformat(),
            "finished_at": finished.isoformat(),
            "status": status,
            "integrity_ok": status == "passed",
            "rto_met": status == "passed",
            "baseline_sha256": report["baseline_sha256"] if matching else "a" * 64,
        }
    )
    data = check_health(
        Store(store.path, read_only=True),
        dataset="finance",
        max_age_hours=24,
        now=finished + timedelta(hours=25),
        expected_manifest=manifest,
    )
    assert data["healthy"] is False
    expected = ["stale"] + ([] if status == "passed" else [f"latest_run_{status}"])
    assert data["reasons"] == expected + ([] if matching else ["baseline_mismatch"])


@pytest.mark.parametrize("sample_only", [False, True])
def test_pinning_a_baseline_still_requires_real_evidence(evidence, sample_only):
    store, baseline, _, report = evidence
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DELETE FROM runs")
        connection.commit()
    if sample_only:
        store.add({**report, "sample": True})
    result = cli(store.path, baseline)
    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["reasons"] == ["no_evidence"]
    assert data["baseline_sha256"] is None
    assert data["expected_baseline_sha256"] == report["baseline_sha256"]


@pytest.mark.parametrize("fingerprint", [None, "", "not-a-digest", "A" * 64, 1])
def test_unusable_recorded_fingerprint_is_an_error_when_pinned(evidence, fingerprint):
    store, baseline, _, report = evidence
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DELETE FROM runs")
        connection.commit()
    store.add({**report, "baseline_sha256": fingerprint})
    result = cli(store.path, baseline)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("Error:")
    assert "baseline fingerprint" in result.stderr
    assert "Traceback" not in result.stderr
    # Ordinary checks retain compatibility with legacy records without a digest.
    ordinary = cli(store.path)
    assert ordinary.returncode == 0, ordinary.stderr
    assert "baseline_sha256" not in json.loads(ordinary.stdout)
    assert "expected_baseline_sha256" not in json.loads(ordinary.stdout)


@pytest.mark.parametrize("problem", ["missing", "malformed", "duplicate", "empty-baseline"])
def test_invalid_expected_manifest_cannot_report_success(evidence, problem):
    store, baseline, _, _ = evidence
    if problem == "missing":
        baseline.unlink()
    else:
        document = {
            "malformed": "{",
            "duplicate": '{"schema_version":1,"schema_version":1}',
            "empty-baseline": '{"schema_version":1,"files":{}}',
        }[problem]
        baseline.write_text(document, encoding="utf-8")
    original = store.path.read_bytes()
    result = cli(store.path, baseline)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert store.path.read_bytes() == original
    assert len(store.list()) == 1


@pytest.mark.parametrize("protected", [False, True])
def test_pinned_check_preserves_history_and_expected_baseline(evidence, protected):
    store, baseline, _, _ = evidence
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    originals = {path: path.read_bytes() for path in (store.path, baseline)}
    contents = sorted(store.path.parent.iterdir())
    permissions = {path: stat.S_IMODE(path.stat().st_mode) for path in originals}
    if protected:
        for path in originals:
            path.chmod(stat.S_IREAD)
    try:
        result = cli(store.path, baseline)
    finally:
        for path, mode in permissions.items():
            path.chmod(mode)
    assert result.returncode == 0, result.stderr
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert sorted(store.path.parent.iterdir()) == contents


def test_api_validates_expected_manifest_before_reading_history(tmp_path):
    store = Store(tmp_path / "absent" / "history.db", read_only=True)
    with pytest.raises(ValueError, match="manifest schema"):
        check_health(store, dataset="finance", max_age_hours=24, expected_manifest={})
    assert not store.path.parent.exists()
