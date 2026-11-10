"""Ambiguous baseline documents must never produce passing recovery evidence."""

import json
import subprocess
import sys

import pytest

from recovery_assurance.core import capture, load_manifest, run_drill
from recovery_assurance.storage import Store

SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
ENTRY = json.dumps({"size": 3, "sha256": SHA256})
WRONG_ENTRY = json.dumps({"size": 3, "sha256": "0" * 64})
FILES = '{"record.txt":' + ENTRY + "}"
AMBIGUOUS_FILES = '{"record.txt":' + WRONG_ENTRY + ',"record.txt":' + ENTRY + "}"
AMBIGUOUS_BASELINE = '{"schema_version":1,"files":' + AMBIGUOUS_FILES + "}"


@pytest.fixture
def backup(tmp_path):
    source = tmp_path / "backup"
    source.mkdir()
    (source / "record.txt").write_bytes(b"abc")
    return source


@pytest.mark.parametrize(
    "document",
    [
        '{"schema_version":9,"schema_version":1,"files":' + FILES + "}",
        '{"schema_version":1,"files":{},"files":' + FILES + "}",
        AMBIGUOUS_BASELINE,
        '{"schema_version":1,"files":{"record.txt":'
        + WRONG_ENTRY
        + ',"record\\u002etxt":'
        + ENTRY
        + "}}",
        '{"schema_version":1,"files":{"record.txt":{"size":0,"size":3,"sha256":"' + SHA256 + '"}}}',
        '{"schema_version":1,"files":{"record.txt":{"size":3,"sha256":"'
        + "0" * 64
        + '","sha256":"'
        + SHA256
        + '"}}}',
    ],
    ids=["schema-version", "files", "file-path", "escaped-path", "size", "checksum"],
)
def test_manifest_loader_rejects_duplicate_keys_at_every_level(tmp_path, document):
    path = tmp_path / "ambiguous.json"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate manifest key"):
        load_manifest(path)


@pytest.mark.parametrize("version", [True, 1.0])
def test_manifest_loader_requires_an_integer_schema_version(backup, tmp_path, version):
    manifest = capture(backup)
    manifest["schema_version"] = version
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest schema"):
        load_manifest(path)


@pytest.mark.parametrize("version", [True, 1.0])
def test_direct_drills_reject_noninteger_schema_before_recording_evidence(
    backup, tmp_path, version
):
    manifest = capture(backup)
    manifest["schema_version"] = version
    store = Store(tmp_path / "runs.db")
    with pytest.raises(ValueError, match="manifest schema"):
        run_drill(manifest, backup, store)
    assert store.list() == []


def test_utf8_bom_manifest_preserves_unicode_and_valid_drill_evidence(backup, tmp_path):
    (backup / "évidence.txt").write_text("synthetic", encoding="utf-8")
    manifest = capture(backup)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8-sig")
    loaded = load_manifest(path)
    assert loaded == manifest
    report = run_drill(loaded, backup, Store(tmp_path / "runs.db"))
    assert report["status"] == "passed"
    assert report["comparison"]["matched"] == 2
    assert len(report["baseline_sha256"]) == 64


@pytest.mark.parametrize("command", ["drill", "run"])
def test_cli_rejects_ambiguous_baseline_before_creating_run_evidence(backup, tmp_path, command):
    path = tmp_path / "ambiguous.json"
    path.write_text(AMBIGUOUS_BASELINE, encoding="utf-8")
    database = tmp_path / "runs.db"
    if command == "drill":
        arguments = ["drill", "--manifest", path, "--backup", backup]
    else:
        config = tmp_path / "datasets.json"
        config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "datasets": {"synthetic": {"manifest": path.name, "backup": backup.name}},
                }
            ),
            encoding="utf-8",
        )
        arguments = ["run", config, "--dataset", "synthetic"]
    result = subprocess.run(
        [sys.executable, "-m", "recovery_assurance", *map(str, arguments), "--db", str(database)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Duplicate manifest key" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
    assert Store(database).list() == []
