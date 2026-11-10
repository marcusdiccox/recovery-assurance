"""Stored evidence can be collected safely from the command line."""

import json
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

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


@pytest.fixture
def archived_evidence(evidence, tmp_path):
    _, reports = evidence
    report = reports["passed"]
    directory = tmp_path / "archivé #100% space"
    directory.mkdir()
    database = directory / "history.db"
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE runs "
                "(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, report TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?)",
                (report["id"], report["started_at"], json.dumps(report)),
            )
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    return database, report


def test_export_preserves_archive_bytes_and_journal_mode(archived_evidence, tmp_path):
    database, report = archived_evidence
    original = database.read_bytes()
    output = tmp_path / "archive-report.json"
    result = cli("export", report["id"], "--db", database, "--output", output)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert database.read_bytes() == original
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert list(database.parent.iterdir()) == [database]


@pytest.mark.parametrize("format", ["json", "html"])
def test_export_can_read_a_read_only_archive(archived_evidence, tmp_path, format):
    database, report = archived_evidence
    original = database.read_bytes()
    permissions = stat.S_IMODE(database.stat().st_mode)
    database.chmod(stat.S_IREAD)
    try:
        output = tmp_path / f"read-only-evidence.{format}"
        result = cli(
            "export", report["id"], "--format", format, "--db", database, "--output", output
        )
    finally:
        database.chmod(permissions)
    assert result.returncode == 0, result.stderr
    document = output.read_text(encoding="utf-8")
    if format == "json":
        assert json.loads(document) == report
    else:
        assert report["id"] in document
        assert report["baseline_sha256"] in document
    assert database.read_bytes() == original


def test_read_only_store_can_query_but_cannot_append(archived_evidence):
    database, report = archived_evidence
    original = database.read_bytes()
    store = Store(database, read_only=True)
    assert store.get(report["id"]) == report
    assert store.list(dataset="operations") == [report]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store.add({**report, "id": "unexpected-write"})
    assert store.list() == [report]
    assert database.read_bytes() == original


def test_read_only_store_does_not_create_a_missing_database(tmp_path):
    directory = tmp_path / "absent-history"
    with pytest.raises(sqlite3.OperationalError):
        Store(directory / "runs.db", read_only=True).list()
    assert not directory.exists()


def test_export_reads_committed_wal_records_while_a_writer_is_open(evidence, tmp_path):
    store, reports = evidence
    report = {**reports["passed"], "id": "committed-in-wal"}
    with closing(sqlite3.connect(store.path)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO runs VALUES (?, ?, ?)",
            (report["id"], report["started_at"], json.dumps(report)),
        )
        writer.commit()
        assert store.path.with_name("runs.db-wal").stat().st_size > 0
        output = tmp_path / "wal-evidence.json"
        result = cli("export", report["id"], "--db", store.path, "--output", output)
        assert result.returncode == 0, result.stderr
        assert json.loads(output.read_text(encoding="utf-8")) == report


@pytest.mark.skipif(sys.platform != "win32", reason="Windows extended-path syntax")
def test_export_supports_existing_windows_extended_paths(archived_evidence, tmp_path):
    database, report = archived_evidence
    original = database.read_bytes()
    extended = Path("\\\\?\\" + str(database.resolve()))
    with closing(sqlite3.connect(extended)) as connection:
        assert connection.execute("SELECT id FROM runs").fetchone() == (report["id"],)
    output = tmp_path / "extended-path-evidence.json"
    result = cli("export", report["id"], "--db", extended, "--output", output)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert database.read_bytes() == original


def test_read_only_store_encodes_unc_as_a_native_path_without_uri_authority(
    archived_evidence, monkeypatch
):
    database, report = archived_evidence
    store = Store(database, read_only=True)
    network_path = PureWindowsPath(r"\\recovery-host\archivé share\history #100%.db")
    monkeypatch.setattr(store, "path", SimpleNamespace(resolve=lambda: network_path))
    connect = sqlite3.connect
    opened = []

    def local_share_connection(filename, **options):
        # An actual SMB share is unavailable; inspect our URI boundary while querying real data.
        opened.append((filename, options))
        return connect(database, timeout=options["timeout"])

    monkeypatch.setattr(sqlite3, "connect", local_share_connection)
    assert store.get(report["id"]) == report
    uri, options = opened[0]
    parsed = urlsplit(uri)
    assert options["uri"] is True
    assert parsed.scheme == "file"
    assert parsed.netloc == ""
    assert unquote(parsed.path) == str(network_path)
    assert parsed.query == "mode=ro"
    assert parsed.fragment == ""
