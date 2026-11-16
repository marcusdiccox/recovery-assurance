"""Optional local drill locks coordinate real processes without stale PID files."""

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from recovery_assurance.core import capture, save_manifest
from recovery_assurance.storage import Store

HOLDER = """
import sys
from pathlib import Path
from recovery_assurance.locking import drill_lock
with drill_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text('held', encoding='utf-8')
    sys.stdin.readline()
"""


@contextmanager
def held_lock(lock, tmp_path):
    ready = tmp_path / "holder-ready"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", HOLDER, str(lock), str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), (
            process.stderr.read() if process.poll() is not None else "No lock acquired"
        )
        yield process
    finally:
        try:
            process.communicate("\n" if process.poll() is None else None, timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def cli(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "recovery_assurance", *map(str, arguments)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


@pytest.fixture
def profile(tmp_path):
    directory = tmp_path / "private settings"
    backup = directory / "backup"
    backup.mkdir(parents=True)
    (backup / "record.txt").write_bytes(b"synthetic")
    manifest = directory / "baseline.json"
    save_manifest(capture(backup), manifest)
    config = directory / "datasets.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "datasets": {"finance": {"manifest": manifest.name, "backup": "backup"}},
            }
        ),
        encoding="utf-8",
    )
    return config, backup


@pytest.mark.parametrize("command", ["run", "drill"])
def test_busy_drill_skips_before_database_or_input_validation(tmp_path, command):
    lock = tmp_path / "finance.lock"
    database = tmp_path / "not-created.db"
    arguments = (
        ["run", tmp_path / "absent.json", "--dataset", "finance"]
        if command == "run"
        else ["drill", "--manifest", tmp_path / "absent.json", "--backup", tmp_path / "absent"]
    )
    with held_lock(lock, tmp_path):
        result = cli(*arguments, "--db", database, "--lock-file", lock)
        assert result.returncode == 75, result.stderr
        assert "Another drill holds the requested lock" in result.stderr
        assert result.stdout == ""
        assert not database.exists()
    assert lock.read_bytes() == b""


@pytest.mark.parametrize("outcome", ["passed", "failed", "error"])
def test_lock_releases_after_every_cli_outcome(profile, tmp_path, outcome):
    config, backup = profile
    lock = tmp_path / "locks with spaces" / "finance.lock"
    database = tmp_path / "runs.db"
    if outcome == "failed":
        (backup / "record.txt").write_bytes(b"corrupted")
    key = "absent" if outcome == "error" else "finance"
    result = cli("run", config, "--dataset", key, "--db", database, "--lock-file", lock)
    assert result.returncode == {"passed": 0, "failed": 1, "error": 2}[outcome], result.stderr
    (backup / "record.txt").write_bytes(b"synthetic")
    again = cli("run", config, "--dataset", "finance", "--db", database, "--lock-file", lock)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["status"] == "passed"
    assert len(Store(database, read_only=True).list()) == (1 if outcome == "error" else 2)
    assert lock.read_bytes() == b""


def test_terminated_lock_holder_does_not_leave_a_stale_lock(profile, tmp_path):
    config, _ = profile
    lock = tmp_path / "finance.lock"
    with held_lock(lock, tmp_path) as process:
        process.kill()
        process.wait(timeout=10)
    assert lock.exists()
    result = cli(
        "run", config, "--dataset", "finance", "--db", tmp_path / "runs.db", "--lock-file", lock
    )
    assert result.returncode == 0, result.stderr
    assert lock.read_bytes() == b""


def test_distinct_lock_paths_allow_independent_drills(profile, tmp_path):
    config, _ = profile
    with held_lock(tmp_path / "other.lock", tmp_path):
        result = cli(
            "run",
            config,
            "--dataset",
            "finance",
            "--db",
            tmp_path / "runs.db",
            "--lock-file",
            tmp_path / "finance.lock",
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("kind", ["nonempty", "database", "sidecar", "hardlink", "bad-parent"])
def test_invalid_lock_paths_do_not_modify_inputs_or_start_a_drill(profile, tmp_path, kind):
    config, _ = profile
    database = tmp_path / ("runs.lock" if kind == "database" else "runs.db")
    lock = tmp_path / "finance.lock"
    if kind == "nonempty":
        lock.write_bytes(b"keep private content")
    elif kind == "database":
        lock = database
    elif kind == "sidecar":
        lock = database.with_name(database.name + "-wal")
    elif kind == "hardlink":
        database.write_bytes(b"")
        try:
            os.link(database, lock)
        except OSError:
            pytest.skip("Filesystem does not support hard links")
    else:
        parent = tmp_path / "keep.txt"
        parent.write_bytes(b"preserve")
        lock = parent / "finance.lock"
    result = cli("run", config, "--dataset", "finance", "--db", database, "--lock-file", lock)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    if kind == "nonempty":
        assert lock.read_bytes() == b"keep private content"
    if kind == "hardlink":
        assert database.read_bytes() == b""
    else:
        assert not database.exists()
    if kind == "bad-parent":
        assert parent.read_bytes() == b"preserve"
