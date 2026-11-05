"""Pin selectors at the subprocess boundary; exercise real comparison and persistence."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store

SNAPSHOT_ID = "ab12cd34" * 8


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "record.txt").write_text("synthetic recovery evidence", encoding="utf-8")
    return directory


def install_backend(monkeypatch, source, metadata, *, fail_restore=False):
    calls = []
    monkeypatch.setattr("recovery_assurance.core.shutil.which", lambda name: "restic")

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if "snapshots" in args:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(metadata).encode())
        if fail_restore:
            raise subprocess.CalledProcessError(1, args, stderr=b"private credential detail")
        target = Path(args[args.index("--target") + 1])
        shutil.copytree(source, target, dirs_exist_ok=True)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("recovery_assurance.core.subprocess.run", run)
    return calls


@pytest.mark.parametrize("selector", ["latest", SNAPSHOT_ID[:8], SNAPSHOT_ID])
def test_restore_uses_one_resolved_id_and_preserves_subfolder(
    source, tmp_path, monkeypatch, selector
):
    calls = install_backend(monkeypatch, source, [{"id": SNAPSHOT_ID, "hostname": "private-host"}])
    store = Store(tmp_path / "runs.db")
    report = run_drill(
        capture(source),
        "azure:recovery:/snapshots",
        store,
        backend="restic",
        snapshot=f"{selector}:/dataset/finance",
        timeout=30,
    )
    assert report["status"] == "passed"
    assert report["snapshot"] == selector
    assert report["resolved_snapshot"] == SNAPSHOT_ID
    assert store.get(report["id"])["resolved_snapshot"] == SNAPSHOT_ID
    assert len(calls) == 2
    assert calls[0][0] == [
        "restic",
        "--repo",
        "azure:recovery:/snapshots",
        "snapshots",
        "--json",
        selector,
    ]
    assert calls[1][0][:5] == [
        "restic",
        "--repo",
        "azure:recovery:/snapshots",
        "restore",
        f"{SNAPSHOT_ID}:/dataset/finance",
    ]
    assert 0 < calls[1][1]["timeout"] < calls[0][1]["timeout"] <= 30
    assert "private-host" not in json.dumps(report)


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        [{"id": SNAPSHOT_ID}, {"id": SNAPSHOT_ID}],
        [{}],
        [{"id": "latest"}],
        [{"id": "f" * 63}],
    ],
)
def test_unresolved_or_invalid_snapshot_never_starts_restore(
    source, tmp_path, monkeypatch, metadata
):
    calls = install_backend(monkeypatch, source, metadata)
    store = Store(tmp_path / "runs.db")
    report = run_drill(capture(source), "repository", store, backend="restic")
    assert report["status"] == "error"
    assert report["comparison"] is None
    assert report["resolved_snapshot"] is None
    assert len(calls) == 1
    assert store.get(report["id"]) == report


def test_restore_failure_keeps_the_resolved_id_without_recording_raw_output(
    source, tmp_path, monkeypatch
):
    install_backend(monkeypatch, source, [{"id": SNAPSHOT_ID}], fail_restore=True)
    report = run_drill(capture(source), "repository", Store(tmp_path / "runs.db"), backend="restic")
    assert report["status"] == "error"
    assert report["resolved_snapshot"] == SNAPSHOT_ID
    assert "private credential" not in json.dumps(report)
    assert report["comparison"] is None


def test_resolution_cannot_substitute_an_unrelated_snapshot(source, tmp_path, monkeypatch):
    calls = install_backend(monkeypatch, source, [{"id": "f" * 64}])
    report = run_drill(
        capture(source),
        "repository",
        Store(tmp_path / "runs.db"),
        backend="restic",
        snapshot=SNAPSHOT_ID[:8],
    )
    assert report["status"] == "error"
    assert report["resolved_snapshot"] is None
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["command", "timeout", "json"])
def test_lookup_errors_are_persisted_without_restoring_or_leaking_output(
    source, tmp_path, monkeypatch, failure
):
    calls = install_backend(monkeypatch, source, [])

    def lookup(args, **kwargs):
        calls.append(args)
        if failure == "command":
            raise subprocess.CalledProcessError(12, args, stderr=b"private credential detail")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 1, output=b"private credential detail")
        return subprocess.CompletedProcess(args, 0, stdout=b"private credential detail")

    monkeypatch.setattr("recovery_assurance.core.subprocess.run", lookup)
    store = Store(tmp_path / "runs.db")
    report = run_drill(capture(source), "repository", store, backend="restic")
    assert report["status"] == "error"
    assert report["resolved_snapshot"] is None
    assert report["comparison"] is None
    assert store.get(report["id"]) == report
    assert len(calls) == 1
    assert "private credential" not in json.dumps(report)


def test_exhausted_lookup_budget_never_starts_a_restore(source, tmp_path, monkeypatch):
    calls = install_backend(monkeypatch, source, [{"id": SNAPSHOT_ID}])
    now = [0.0]
    monkeypatch.setattr("recovery_assurance.core.time.perf_counter", lambda: now[0])

    def resolve(*args):
        now[0] = 2.0
        return SNAPSHOT_ID

    monkeypatch.setattr("recovery_assurance.core._resolve_restic_snapshot", resolve)
    report = run_drill(
        capture(source),
        "repository",
        Store(tmp_path / "runs.db"),
        backend="restic",
        timeout=1,
    )
    assert report["status"] == "error"
    assert "TimeoutExpired" in report["error"]
    assert report["resolved_snapshot"] == SNAPSHOT_ID
    assert report["comparison"] is None
    assert not calls
