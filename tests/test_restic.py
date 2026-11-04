"""Real backup/restore integration; skipped only when restic is not installed."""

import json
import os
import shutil
import subprocess

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store


@pytest.mark.skipif(shutil.which("restic") is None, reason="Install restic to run integration")
def test_actual_restic_snapshot_restores_and_verifies_without_changing_source(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "record.txt").write_text("a real backed-up record", encoding="utf-8")
    manifest = capture(source)
    repository = tmp_path / "repository"
    monkeypatch.setenv("RESTIC_PASSWORD", "synthetic-test-password-not-a-real-credential")

    def restic(*args):
        return subprocess.run(
            [shutil.which("restic"), "-r", str(repository), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )

    restic("init")
    restic("backup", str(source))
    snapshot_id = json.loads(restic("snapshots", "--json").stdout)[0]["id"]
    subtree = source.as_posix()
    if os.name == "nt":
        subtree = "/" + subtree.replace(":", "", 1)
    report = run_drill(
        manifest,
        repository,
        Store(tmp_path / "runs.db"),
        backend="restic",
        snapshot=f"{snapshot_id}:{subtree}",
        rto_seconds=120,
    )
    assert report["status"] == "passed", report
    assert report["comparison"]["matched"] == 1
    assert report["snapshot"] == snapshot_id
    assert (source / "record.txt").read_text() == "a real backed-up record"
    assert "synthetic-test-password" not in json.dumps(report)
