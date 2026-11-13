"""Older recovery evidence stays reachable through bounded, stable history pages."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from recovery_assurance.core import capture, run_drill
from recovery_assurance.storage import Store
from recovery_assurance.web import create_app


@pytest.fixture
def history(tmp_path):
    source = tmp_path / "synthetic"
    source.mkdir()
    (source / "record.txt").write_bytes(b"synthetic")
    template = run_drill(capture(source), source, Store(tmp_path / "fixture.db"), sample=True)
    store = Store(tmp_path / "history.db")
    reports = []
    for index in range(405):
        report = {
            **template,
            "id": f"r{index:03d}",
            "started_at": "2026-01-01T00:00:00+00:00",
            "dataset": "finance" if index % 2 == 0 else "operations",
        }
        if index == 0:
            del report["dataset"]
        reports.append(report)
    reports.append(
        {
            **template,
            "id": "zz-older",
            "started_at": "2025-12-31T00:00:00+00:00",
            "dataset": "finance",
        }
    )
    with closing(sqlite3.connect(store.path)) as connection:
        with connection:
            connection.executemany(
                "INSERT INTO runs VALUES (?, ?, ?)",
                [(r["id"], r["started_at"], json.dumps(r)) for r in reports],
            )
    return store, template


def cli_page(store, *, before=None, dataset=None):
    arguments = [sys.executable, "-m", "recovery_assurance", "list", "--db", str(store.path)]
    if before is not None:
        arguments.extend(["--before", before])
    if dataset is not None:
        arguments.extend(["--dataset", dataset])
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def page(store, interface, *, before=None, dataset=None):
    if interface == "cli":
        result = cli_page(store, before=before, dataset=dataset)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    params = {}
    if before is not None:
        params["before"] = before
    if dataset is not None:
        params["dataset"] = dataset
    with TestClient(create_app(store.path)) as client:
        response = client.get("/api/runs", params=params)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.mark.parametrize("interface", ["cli", "api"])
def test_all_history_pages_handle_tied_times_and_newer_insertions(history, interface):
    store, template = history
    first = page(store, interface)
    assert [r["id"] for r in first] == [f"r{n:03d}" for n in range(404, 204, -1)]
    store.add({**template, "id": "newer", "started_at": "2026-01-02T00:00:00+00:00"})
    store.add({**template, "id": "z-newer-tie", "started_at": "2026-01-01T00:00:00+00:00"})
    second = page(store, interface, before=first[-1]["id"])
    assert [r["id"] for r in second] == [f"r{n:03d}" for n in range(204, 4, -1)]
    third = page(store, interface, before=second[-1]["id"])
    assert [r["id"] for r in third] == ["r004", "r003", "r002", "r001", "r000", "zz-older"]
    assert page(store, interface, before=third[-1]["id"]) == []
    assert len(store.list(1000)) == 408
    assert "dataset" not in store.get("r000")


@pytest.mark.parametrize("interface", ["cli", "api"])
def test_cursor_and_dataset_filter_apply_before_page_limit(history, interface):
    store, _ = history
    first = page(store, interface, dataset="finance")
    assert [r["id"] for r in first] == [f"r{n:03d}" for n in range(404, 4, -2)]
    second = page(store, interface, dataset="finance", before=first[-1]["id"])
    assert [r["id"] for r in second] == ["r004", "r002", "zz-older"]
    assert page(store, interface, dataset="finance", before=second[-1]["id"]) == []
    assert [r["id"] for r in store.list(2, dataset="finance", before="r009")] == ["r008", "r006"]


@pytest.mark.parametrize("interface", ["cli", "api"])
@pytest.mark.parametrize("cursor", ["not-found", ""])
def test_unknown_cursor_fails_instead_of_restarting_history(history, interface, cursor):
    store, template = history
    store.add({**template, "id": "", "started_at": "2026-01-02T00:00:00+00:00"})
    if interface == "cli":
        result = cli_page(store, before=cursor, dataset="unknown")
        assert result.returncode == 2
        assert "History cursor run not found" in result.stderr
        assert "Traceback" not in result.stderr
        assert result.stdout == ""
    else:
        with TestClient(create_app(store.path)) as client:
            response = client.get("/api/runs", params={"before": cursor, "dataset": "unknown"})
            assert response.status_code == 404
            assert response.json() == {"detail": "History cursor run not found"}


def test_sql_like_cursor_is_literal_and_read_only_store_can_page(history):
    store, template = history
    cursor = "marker' OR 1=1 --"
    for identifier in ("alpha", cursor):
        store.add(
            {
                **template,
                "id": identifier,
                "started_at": "2026-01-01T00:00:00+00:00",
                "dataset": "finance",
            }
        )
    read_only = Store(store.path, read_only=True)
    assert [r["id"] for r in read_only.list(2, dataset="finance", before=cursor)] == [
        "alpha",
        "zz-older",
    ]
    response = page(store, "api", dataset="finance", before=cursor)
    assert [r["id"] for r in response] == ["alpha", "zz-older"]
