"""Real backup/restore integration; skipped only when restic is not installed."""

import json
import os
import shutil
import subprocess

import pytest

from recovery_assurance.core import capture, run_drill
from recovery_assurance.reports import html_report
from recovery_assurance.storage import Store


@pytest.mark.skipif(shutil.which("restic") is None, reason="Install restic to run integration")
@pytest.mark.parametrize("selector", ["full", "prefix", "latest"])
def test_actual_restic_snapshot_restores_and_verifies_without_changing_source(
    tmp_path, monkeypatch, selector
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
    if selector == "latest":
        # Distinct content proves latest resolves the newer of two backups.
        previous_id = snapshot_id
        (source / "new-record.txt").write_text("newer snapshot marker", encoding="utf-8")
        manifest = capture(source)
        restic("backup", str(source))
        snapshot_id = next(
            item["id"]
            for item in json.loads(restic("snapshots", "--json").stdout)
            if item["id"] != previous_id
        )
    requested = {"full": snapshot_id, "prefix": snapshot_id[:8], "latest": "latest"}[selector]
    subtree = source.as_posix()
    if os.name == "nt":
        subtree = "/" + subtree.replace(":", "", 1)
    report = run_drill(
        manifest,
        repository,
        Store(tmp_path / "runs.db"),
        backend="restic",
        snapshot=f"{requested}:{subtree}",
        rto_seconds=120,
    )
    assert report["status"] == "passed", report
    assert report["comparison"]["matched"] == len(manifest["files"])
    assert report["snapshot"] == requested
    assert report["resolved_snapshot"] == snapshot_id
    assert "Resolved Snapshot" in html_report(report)
    assert snapshot_id in html_report(report)
    assert (source / "record.txt").read_text() == "a real backed-up record"
    assert "synthetic-test-password" not in json.dumps(report)
