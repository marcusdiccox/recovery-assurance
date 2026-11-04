import json

import pytest

from recovery_assurance.core import capture, compare, load_manifest, run_drill, save_manifest
from recovery_assurance.storage import Store


@pytest.fixture
def files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "team.csv").write_text("abc", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "empty.txt").write_bytes(b"")
    return source


def test_capture_hashes_real_bytes_and_keeps_relative_paths(files):
    manifest = capture(files)
    assert manifest["files"]["team.csv"] == {
        "size": 3,
        "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    }
    assert set(manifest["files"]) == {"team.csv", "nested/empty.txt"}
    assert str(files) not in json.dumps(manifest)


def test_compare_detects_same_size_corruption_missing_and_unexpected(files, tmp_path):
    baseline = capture(files)
    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "team.csv").write_text("xyz", encoding="utf-8")
    (restored / "extra.txt").write_text("extra", encoding="utf-8")
    result = compare(baseline, restored)
    assert result == {
        "matched": 0,
        "changed": ["team.csv"],
        "missing": ["nested/empty.txt"],
        "unexpected": ["extra.txt"],
        "expected_files": 2,
        "restored_files": 2,
        "expected_bytes": 3,
    }


def test_empty_source_cannot_produce_passing_evidence(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        capture(tmp_path)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/secrets", "a\\b", "a/../b"])
def test_manifest_rejects_unsafe_paths(files, tmp_path, name):
    manifest = capture(files)
    manifest["files"][name] = manifest["files"].pop("team.csv")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(path)


def test_manifest_round_trip(files, tmp_path):
    manifest = capture(files)
    path = tmp_path / "baseline.json"
    save_manifest(manifest, path)
    assert load_manifest(path) == manifest


def test_manifest_cannot_overwrite_existing_evidence(files, tmp_path):
    path = tmp_path / "baseline.json"
    save_manifest(capture(files), path)
    with pytest.raises(FileExistsError):
        save_manifest(capture(files), path)


def test_symlink_is_rejected_instead_of_followed(files):
    try:
        (files / "link").symlink_to(files / "team.csv")
    except OSError:
        pytest.skip("OS requires symlink privilege")
    with pytest.raises(ValueError, match="link"):
        capture(files)


def test_successful_drill_restores_real_files_and_persists(files, tmp_path):
    store = Store(tmp_path / "runs.db")
    report = run_drill(capture(files), files, store, name="Finance restore", rto_seconds=60)
    assert report["status"] == "passed"
    assert report["integrity_ok"] is True
    assert report["rto_met"] is True
    assert report["comparison"]["matched"] == 2
    assert report["total_seconds"] >= report["restore_seconds"] >= 0
    assert store.get(report["id"]) == report
    assert store.list() == [report]
    assert (files / "team.csv").read_text() == "abc"


def test_zero_rto_fails_even_when_integrity_passes(files, tmp_path):
    report = run_drill(capture(files), files, Store(tmp_path / "runs.db"), rto_seconds=0)
    assert report["status"] == "failed"
    assert report["integrity_ok"] is True
    assert report["rto_met"] is False


def test_restore_failure_is_recorded_as_error_not_empty_success(files, tmp_path):
    store = Store(tmp_path / "runs.db")
    report = run_drill(capture(files), tmp_path / "missing-backup", store)
    assert report["status"] == "error"
    assert report["integrity_ok"] is False
    assert report["comparison"] is None
    assert store.get(report["id"])["status"] == "error"


@pytest.mark.parametrize("rto", [-1, float("nan"), float("inf")])
def test_invalid_rto_is_rejected_before_running(files, tmp_path, rto):
    with pytest.raises(ValueError):
        run_drill(capture(files), files, Store(tmp_path / "runs.db"), rto_seconds=rto)


def test_snapshot_options_cannot_be_injected(files, tmp_path):
    with pytest.raises(ValueError):
        run_drill(
            capture(files), files, Store(tmp_path / "runs.db"), backend="restic", snapshot="--help"
        )


def test_store_unknown_id_returns_none(tmp_path):
    assert Store(tmp_path / "runs.db").get("unknown") is None


def test_database_connections_close_after_each_operation(tmp_path, monkeypatch):
    import sqlite3

    original = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr("recovery_assurance.storage.sqlite3.connect", connect)
    store = Store(tmp_path / "runs.db")
    store.list()
    store.get("missing")
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_slow_cleanup_cannot_make_reported_time_disagree_with_target(files, tmp_path, monkeypatch):
    import tempfile
    import time

    original = tempfile.TemporaryDirectory

    class SlowCleanup(original):
        def __exit__(self, *args):
            time.sleep(0.05)
            return super().__exit__(*args)

    monkeypatch.setattr("recovery_assurance.core.tempfile.TemporaryDirectory", SlowCleanup)
    report = run_drill(capture(files), files, Store(tmp_path / "runs.db"), rto_seconds=0.03)
    assert report["rto_met"] == (report["total_seconds"] <= report["rto_seconds"])


def test_cloud_repository_uri_is_not_rewritten_as_local_path(files, tmp_path, monkeypatch):
    import shutil

    def restore(backend, backup, target, snapshot, timeout):
        assert backup == "azure:recovery:/snapshots"
        shutil.copytree(files, target, dirs_exist_ok=True)

    monkeypatch.setattr("recovery_assurance.core._restore", restore)
    report = run_drill(
        capture(files), "azure:recovery:/snapshots", Store(tmp_path / "runs.db"), backend="restic"
    )
    assert report["status"] == "passed"


def test_reports_identify_the_exact_baseline(files, tmp_path):
    manifest = capture(files)
    report = run_drill(manifest, files, Store(tmp_path / "runs.db"))
    assert len(report["baseline_sha256"]) == 64
    assert report["snapshot"] is None


def test_restore_destination_inside_backup_is_rejected_without_copying(
    files, tmp_path, monkeypatch
):
    import shutil

    original = shutil.copytree

    def guarded_copy(source, target, **kwargs):
        if target.resolve().is_relative_to(source.resolve()):
            raise AssertionError("Recursive copy must be rejected before touching data")
        return original(source, target, **kwargs)

    monkeypatch.setattr("recovery_assurance.core.shutil.copytree", guarded_copy)
    monkeypatch.setattr("recovery_assurance.core.tempfile.tempdir", str(files))
    report = run_drill(capture(files), files, Store(tmp_path / "runs.db"))
    assert report["status"] == "error"
    assert report["comparison"] is None
    assert (files / "team.csv").read_text() == "abc"
