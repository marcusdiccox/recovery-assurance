"""Stored evidence can be collected safely from the command line."""

import json
import subprocess
import sys

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
def evidence(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    file = source / "record.txt"
    file.write_text("synthetic", encoding="utf-8")
    manifest = capture(source)
    store = Store(tmp_path / "runs.db")
    reports = {}
    for outcome in ("passed", "failed", "error"):
        report = run_drill(
            manifest,
            source if outcome != "error" else tmp_path / "absent",
            store,
            name="<script>Évidence</script>",
            dataset="operations",
            rto_seconds=0 if outcome == "failed" else 60,
        )
        assert report["status"] == outcome
        reports[outcome] = report
    file.unlink()
    source.rmdir()
    return store, reports


@pytest.mark.parametrize("format", ["json", "html"])
@pytest.mark.parametrize("outcome", ["passed", "failed", "error"])
def test_cli_exports_saved_evidence_without_rerunning_or_changing_history(
    evidence, tmp_path, format, outcome
):
    store, reports = evidence
    before = store.list()
    report = reports[outcome]
    output = tmp_path / f"saved evidence.{format}"
    result = cli("export", report["id"], "--format", format, "--output", output, "--db", store.path)
    assert result.returncode == 0, result.stderr
    document = output.read_text(encoding="utf-8")
    if format == "json":
        assert json.loads(document) == report
    else:
        assert '<html lang="en">' in document
        assert report["id"] in document
        assert report["baseline_sha256"] in document
        assert f"<td>{outcome}</td>" in document
        assert "operations" in document
        assert "&lt;script&gt;Évidence&lt;/script&gt;" in document
        assert "<script>" not in document
    assert Store(store.path).list() == before


def test_cli_export_defaults_to_json_regardless_of_filename(evidence, tmp_path):
    store, reports = evidence
    output = tmp_path / "evidence.html"
    result = cli("export", reports["passed"]["id"], "--output", output, "--db", store.path)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == reports["passed"]


@pytest.mark.parametrize("destination", ["file", "database"])
def test_cli_export_never_overwrites_existing_files_or_its_database(
    evidence, tmp_path, destination
):
    store, reports = evidence
    before = store.list()
    output = store.path if destination == "database" else tmp_path / "existing.json"
    if destination == "file":
        output.write_bytes(b"keep existing evidence")
    result = cli("export", reports["passed"]["id"], "--output", output, "--db", store.path)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    if destination == "file":
        assert output.read_bytes() == b"keep existing evidence"
    assert Store(store.path).list() == before


def test_cli_export_unknown_run_does_not_create_output(evidence, tmp_path):
    store, _ = evidence
    output = tmp_path / "unknown.json"
    result = cli("export", "unknown", "--output", output, "--db", store.path)
    assert result.returncode == 2
    assert "Run not found" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_cli_export_missing_database_does_not_create_it(tmp_path):
    database = tmp_path / "not-created" / "runs.db"
    output = tmp_path / "evidence.json"
    result = cli("export", "unknown", "--output", output, "--db", database)
    assert result.returncode == 2
    assert "Database does not exist" in result.stderr
    assert "Traceback" not in result.stderr
    assert not database.parent.exists()
    assert not output.exists()


def test_cli_export_invalid_database_reports_error_without_creating_output(tmp_path):
    database = tmp_path / "invalid.db"
    database.write_bytes(b"not a SQLite database")
    output = tmp_path / "evidence.json"
    result = cli("export", "unknown", "--output", output, "--db", database)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert database.read_bytes() == b"not a SQLite database"
    assert not output.exists()


def test_cli_export_missing_output_parent_does_not_change_history(evidence, tmp_path):
    store, reports = evidence
    before = store.list()
    output = tmp_path / "absent-parent" / "evidence.json"
    result = cli("export", reports["passed"]["id"], "--output", output, "--db", store.path)
    assert result.returncode == 2
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert not output.parent.exists()
    assert Store(store.path).list() == before
