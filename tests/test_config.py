"""Named dataset profiles must remain repeatable outside the config directory."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from recovery_assurance.config import load_dataset
from recovery_assurance.core import capture, run_drill, save_manifest
from recovery_assurance.storage import Store


def test_named_dataset_cli_runs_from_another_directory_and_preserves_exit_codes(tmp_path):
    config_dir = tmp_path / "profiles"
    config_dir.mkdir()
    backup = config_dir / "backup"
    backup.mkdir()
    (backup / "record.txt").write_text("original", encoding="utf-8")
    save_manifest(capture(backup), config_dir / "baseline.json")
    config = config_dir / "datasets.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "datasets": {
                    "finance": {
                        "manifest": "baseline.json",
                        "backup": "backup",
                        "name": "Finance recovery",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    other = tmp_path / "scheduler-working-directory"
    other.mkdir()
    database = tmp_path / "runs.db"

    def run(dataset="finance"):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "recovery_assurance",
                "run",
                str(config),
                "--dataset",
                dataset,
                "--db",
                str(database),
            ],
            cwd=other,
            capture_output=True,
            text=True,
            check=False,
        )

    good = run()
    assert good.returncode == 0, good.stderr
    report = json.loads(good.stdout)
    assert report["name"] == "Finance recovery"
    assert report["comparison"]["matched"] == 1
    assert Store(database).get(report["id"]) == report
    (backup / "record.txt").write_text("corrupted", encoding="utf-8")
    failed = run()
    assert failed.returncode == 1, failed.stderr
    assert json.loads(failed.stdout)["comparison"]["changed"] == ["record.txt"]
    (backup / "record.txt").unlink()
    backup.rmdir()
    error = run()
    assert error.returncode == 2
    assert json.loads(error.stdout)["status"] == "error"
    unknown = run("unknown")
    assert unknown.returncode == 2
    assert "Unknown dataset" in unknown.stderr
    assert "Traceback" not in unknown.stderr


@pytest.fixture
def profile_file(tmp_path):
    path = tmp_path / "datasets.json"

    def write(settings=None, *, document=None):
        if document is None:
            document = {
                "schema_version": 1,
                "datasets": {
                    "finance": {
                        "manifest": "baseline.json",
                        "backup": "snapshot",
                        **(settings or {}),
                    }
                },
            }
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    return write


@pytest.mark.parametrize(
    "repository",
    ["azure:recovery:/finance", "s3:https://storage.example/bucket", "sftp:backup@host:/repo"],
)
def test_restic_repository_uris_are_preserved(profile_file, repository):
    config = profile_file({"backend": "restic", "backup": repository, "snapshot": "latest:/data"})
    options = load_dataset(config, "finance")
    assert options["backup"] == repository
    assert options["snapshot"] == "latest:/data"
    assert options["manifest"] == config.parent / "baseline.json"


@pytest.mark.parametrize("backend", ["directory", "restic"])
def test_local_paths_are_config_relative_and_defaults_are_explicit(profile_file, backend):
    config = profile_file({"backend": backend})
    assert load_dataset(config, "finance") == {
        "manifest": config.parent / "baseline.json",
        "backup": config.parent / "snapshot",
        "backend": backend,
        "name": "finance",
        "snapshot": "latest",
        "rto_seconds": 60.0,
        "timeout": 600.0,
    }
    absolute = config.parent / "absolute-backup"
    config = profile_file({"backup": str(absolute), "rto_seconds": 0})
    options = load_dataset(config, "finance")
    assert options["backup"] == absolute
    assert options["rto_seconds"] == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"manifest": None},
        {"backup": ""},
        {"backend": "unknown"},
        {"name": False},
        {"snapshot": 1},
        {"backup": "\x00"},
        {"password": "do-not-accept-credentials"},
        {"rto_seconds": -1},
        {"rto_seconds": True},
        {"rto_seconds": "30"},
        {"rto_seconds": float("nan")},
        {"rto_seconds": 10**400},
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": False},
        {"timeout": float("inf")},
    ],
)
def test_invalid_profile_settings_are_rejected(profile_file, settings):
    with pytest.raises(ValueError):
        load_dataset(profile_file(settings), "finance")


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"schema_version": True, "datasets": {}},
        {"schema_version": 2, "datasets": {}},
        {"schema_version": 1, "datasets": []},
        {"schema_version": 1, "datasets": {}, "extra": "value"},
    ],
)
def test_invalid_config_envelopes_are_rejected(profile_file, document):
    with pytest.raises(ValueError):
        load_dataset(profile_file(document=document), "finance")


def test_windows_bom_encoded_config_is_supported(profile_file):
    config = profile_file()
    config.write_bytes(b"\xef\xbb\xbf" + config.read_bytes())
    assert load_dataset(config, "finance")["name"] == "finance"


def test_duplicate_profile_names_are_rejected(tmp_path):
    config = tmp_path / "datasets.json"
    config.write_text(
        '{"schema_version":1,"datasets":{"finance":{},"finance":{"manifest":"a","backup":"b"}}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_dataset(config, "finance")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-relative paths")
def test_drive_relative_paths_cannot_depend_on_scheduler_working_directory(profile_file):
    drive = Path.cwd().drive
    with pytest.raises(ValueError, match="drive-relative"):
        load_dataset(profile_file({"backup": drive + "snapshot"}), "finance")


def test_oversized_configuration_is_rejected_before_parsing(tmp_path):
    config = tmp_path / "oversized.json"
    config.write_text(" " * (1024 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="1 MiB"):
        load_dataset(config, "finance")


def test_configured_directory_keeps_symlink_rejection(profile_file, tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "record.txt").write_text("synthetic", encoding="utf-8")
    baseline = capture(backup)
    link = tmp_path / "backup-link"
    try:
        link.symlink_to(backup, target_is_directory=True)
    except OSError:
        pytest.skip("OS requires symlink privilege")
    config = profile_file({"backup": str(link)})
    options = load_dataset(config, "finance")
    options.pop("manifest")
    report = run_drill(baseline, store=Store(tmp_path / "runs.db"), **options)
    assert report["status"] == "error"
    assert report["comparison"] is None
