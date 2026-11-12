"""Consistent history archives protect evidence without altering the source."""

import json
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import closing

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "recovery_assurance", *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture
def history(tmp_path):
    source = tmp_path / "synthetic-files"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic")
    store = Store(tmp_path / "runs.db")
    report = run_drill(capture(source), source, store, dataset="synthetic", sample=True)
    return store, report


def test_history_backup_includes_live_wal_and_all_rows_and_exports_from_copy(history, tmp_path):
    store, report = history
    directory = tmp_path / "archivé #100% space"
    directory.mkdir()
    output = directory / "history.db"
    with closing(sqlite3.connect(store.path)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        rows = [
            (
                f"stored-{index}",
                report["started_at"],
                json.dumps({**report, "id": f"stored-{index}"}),
            )
            for index in range(205)
        ]
        writer.executemany("INSERT INTO runs VALUES (?, ?, ?)", rows)
        writer.commit()
        assert store.path.with_name("runs.db-wal").stat().st_size > 0
        original = writer.execute("SELECT * FROM runs ORDER BY id").fetchall()
        result = cli("backup-history", "--db", store.path, "--output", output)
        assert result.returncode == 0, result.stderr
        with closing(sqlite3.connect(output)) as archive:
            assert archive.execute("SELECT * FROM runs ORDER BY id").fetchall() == original
            assert archive.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            assert archive.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert writer.execute("SELECT * FROM runs ORDER BY id").fetchall() == original
        assert writer.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert len(Store(output, read_only=True).list(1000)) == 206
    exported = tmp_path / "report.json"
    result = cli("export", report["id"], "--db", output, "--output", exported)
    assert result.returncode == 0, result.stderr
    assert json.loads(exported.read_text(encoding="utf-8")) == report
    assert list(directory.iterdir()) == [output]


@pytest.mark.parametrize("destination", ["existing", "source", "symlink"])
def test_history_backup_never_overwrites_existing_destinations(history, tmp_path, destination):
    store, report = history
    protected = tmp_path / "keep.db"
    protected.write_bytes(b"keep existing evidence")
    output = protected
    if destination == "source":
        output = store.path
    elif destination == "symlink":
        output = tmp_path / "linked.db"
        try:
            output.symlink_to(protected)
        except OSError:
            pytest.skip("OS requires symlink privilege")
    result = cli("backup-history", "--db", store.path, "--output", output)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert protected.read_bytes() == b"keep existing evidence"
    assert Store(store.path, read_only=True).list() == [report]
    if destination == "symlink":
        assert output.is_symlink()


@pytest.mark.parametrize("kind", ["missing", "corrupt", "unrelated"])
def test_history_backup_rejects_invalid_sources_without_creating_output(tmp_path, kind):
    source = tmp_path / "source.db"
    if kind == "missing":
        source = tmp_path / "not-created" / "source.db"
    elif kind == "corrupt":
        source.write_bytes(b"not a database")
    else:
        with closing(sqlite3.connect(source)) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
    original = source.read_bytes() if source.exists() else None
    output = tmp_path / "copy.db"
    result = cli("backup-history", "--db", source, "--output", output)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert not output.exists()
    if original is None:
        assert not source.parent.exists()
    else:
        assert source.read_bytes() == original


def test_history_backup_reads_a_write_protected_prepared_archive(history, tmp_path):
    store, report = history
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    original = store.path.read_bytes()
    permissions = stat.S_IMODE(store.path.stat().st_mode)
    store.path.chmod(stat.S_IREAD)
    try:
        output = tmp_path / "readonly-copy.db"
        result = cli("backup-history", "--db", store.path, "--output", output)
    finally:
        store.path.chmod(permissions)
    assert result.returncode == 0, result.stderr
    assert store.path.read_bytes() == original
    assert Store(output, read_only=True).get(report["id"]) == report


def test_history_backup_requires_an_existing_output_parent(history, tmp_path):
    store, report = history
    output = tmp_path / "absent-parent" / "copy.db"
    result = cli("backup-history", "--db", store.path, "--output", output)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert not output.parent.exists()
    assert Store(store.path, read_only=True).list() == [report]


def test_history_backup_supports_empty_initialized_history(tmp_path):
    source = Store(tmp_path / "empty.db")
    output = tmp_path / "empty-copy.db"
    result = cli("backup-history", "--db", source.path, "--output", output)
    assert result.returncode == 0, result.stderr
    assert Store(output, read_only=True).list() == []


def test_history_backup_copy_budget_removes_only_its_partial_output(history, tmp_path, monkeypatch):
    store, report = history
    directory = tmp_path / "copy-output"
    directory.mkdir()
    output = directory / "partial.db"
    elapsed = 0

    def advancing_clock():
        nonlocal elapsed
        elapsed += 20
        return elapsed

    monkeypatch.setattr(time, "monotonic", advancing_clock)
    with pytest.raises(TimeoutError, match="15-second"):
        Store(store.path, read_only=True).backup_to(output)
    assert list(directory.iterdir()) == []
    assert Store(store.path, read_only=True).get(report["id"]) == report


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
@pytest.mark.parametrize("direction", ["output-is-source-sidecar", "source-is-output-sidecar"])
def test_history_backup_rejects_source_destination_sidecar_overlap(
    history, tmp_path, suffix, direction
):
    store, report = history
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    if direction == "output-is-source-sidecar":
        source = store.path
        output = source.with_name(source.name + suffix)
    else:
        output = tmp_path / "archive.db"
        source = output.with_name(output.name + suffix)
        source.write_bytes(store.path.read_bytes())
    original = source.read_bytes()
    result = cli("backup-history", "--db", source, "--output", output)
    assert source.read_bytes() == original
    assert result.returncode == 2
    assert "sidecar" in result.stderr
    assert not output.exists()
    assert Store(source, read_only=True).get(report["id"]) == report


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
@pytest.mark.parametrize("kind", ["file", "dangling-link"])
def test_history_backup_preserves_preexisting_destination_sidecars(history, tmp_path, suffix, kind):
    store, report = history
    output = tmp_path / "archive.db"
    sidecar = output.with_name(output.name + suffix)
    if kind == "file":
        sidecar.write_bytes(b"preexisting private content")
    else:
        try:
            sidecar.symlink_to(tmp_path / "absent-target")
        except OSError:
            pytest.skip("OS requires symlink privilege")
    result = cli("backup-history", "--db", store.path, "--output", output)
    if kind == "file":
        assert sidecar.read_bytes() == b"preexisting private content"
    else:
        assert sidecar.is_symlink()
    assert result.returncode == 2
    assert "sidecar" in result.stderr
    assert not output.exists()
    assert Store(store.path, read_only=True).get(report["id"]) == report
