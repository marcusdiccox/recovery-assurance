"""Optional entry annotations must not change supported size/checksum comparisons."""

import hashlib
import json
import subprocess
import sys

import pytest

from recovery_assurance.core import capture, compare, load_manifest, save_manifest
from recovery_assurance.storage import Store


@pytest.fixture
def annotated_snapshot(tmp_path):
    source = tmp_path / "snapshot"
    source.mkdir()
    (source / "record.txt").write_bytes(b"abc")
    manifest = capture(source)
    manifest["files"]["record.txt"].update(
        note={"comment": "synthetic operator annotation"}, owner="example-operator"
    )
    baseline = tmp_path / "baseline.json"
    save_manifest(manifest, baseline)
    assert load_manifest(baseline) == manifest
    return source, manifest, baseline


def test_unchanged_content_matches_annotated_baseline_without_mutating_it(annotated_snapshot):
    source, manifest, baseline = annotated_snapshot
    original = baseline.read_bytes()
    result = compare(manifest, source)
    assert result["matched"] == 1
    assert result["changed"] == []
    assert result["missing"] == result["unexpected"] == []
    assert result["expected_bytes"] == 3
    assert baseline.read_bytes() == original
    assert manifest == json.loads(original)


@pytest.mark.parametrize("command", ["drill", "run"])
def test_cli_passes_annotated_content_and_fingerprints_the_full_baseline(
    annotated_snapshot, tmp_path, command
):
    source, manifest, baseline = annotated_snapshot
    original = baseline.read_bytes()
    database = tmp_path / "runs.db"
    if command == "drill":
        arguments = ["drill", "--manifest", baseline, "--backup", source, "--dataset", "synthetic"]
    else:
        config = tmp_path / "datasets.json"
        config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "datasets": {"synthetic": {"manifest": baseline.name, "backup": source.name}},
                }
            ),
            encoding="utf-8",
        )
        arguments = ["run", config, "--dataset", "synthetic"]
    result = subprocess.run(
        [sys.executable, "-m", "recovery_assurance", *map(str, arguments), "--db", str(database)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed"
    assert report["integrity_ok"] is True
    assert report["comparison"]["matched"] == 1
    fingerprint = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert report["baseline_sha256"] == fingerprint
    assert Store(database, read_only=True).get(report["id"]) == report
    assert baseline.read_bytes() == original
    assert result.stderr == ""


@pytest.mark.parametrize("field", ["size", "sha256"])
def test_annotations_do_not_override_required_content_mismatches(annotated_snapshot, field):
    source, manifest, _ = annotated_snapshot
    manifest["files"]["record.txt"][field] = 4 if field == "size" else "0" * 64
    result = compare(manifest, source)
    assert result["changed"] == ["record.txt"]
    assert result["matched"] == 0
