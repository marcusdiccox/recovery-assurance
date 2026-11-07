"""Dataset history stays queryable independently of display names and recent activity."""

import pytest
from fastapi.testclient import TestClient

from recovery_assurance.core import capture, run_drill
from recovery_assurance.reports import html_report
from recovery_assurance.storage import Store
from recovery_assurance.web import create_app


@pytest.fixture
def backup(tmp_path):
    directory = tmp_path / "backup"
    directory.mkdir()
    (directory / "record.txt").write_text("synthetic", encoding="utf-8")
    return directory


@pytest.mark.parametrize("outcome", ["passed", "failed", "error"])
def test_dataset_identity_persists_with_every_outcome(backup, tmp_path, outcome):
    store = Store(tmp_path / "runs.db")
    report = run_drill(
        capture(backup),
        backup if outcome != "error" else tmp_path / "absent",
        store,
        name="Display name may change",
        dataset="finance",
        rto_seconds=0 if outcome == "failed" else 60,
    )
    assert report["status"] == outcome
    assert report["dataset"] == "finance"
    assert Store(store.path).get(report["id"])["dataset"] == "finance"
    assert len(report["baseline_sha256"]) == 64
    assert "finance" in html_report(report)


def test_dataset_filter_applies_before_history_limit_and_keeps_legacy_records(backup, tmp_path):
    store = Store(tmp_path / "runs.db")
    template = run_drill(capture(backup), backup, store)
    assert template["dataset"] is None
    rows = [
        ("finance-old", "2026-01-01", "finance"),
        ("finance-new", "2026-01-02", "finance"),
        ("other-1", "2026-01-03", "operations"),
        ("other-2", "2026-01-04", "operations"),
        ("other-3", "2026-01-05", "operations"),
    ]
    for identifier, date, dataset in rows:
        store.add({**template, "id": identifier, "started_at": date, "dataset": dataset})
    legacy = {key: value for key, value in template.items() if key != "dataset"}
    legacy["id"] = "legacy"
    store.add(legacy)
    assert [row["id"] for row in store.list(2, dataset="finance")] == ["finance-new", "finance-old"]
    assert store.list(dataset="unknown") == []
    assert store.get("legacy") == legacy
    assert len(store.list()) == 7


@pytest.mark.parametrize("dataset", ["Finance", "", "finance' OR 1=1 --"])
def test_dataset_filters_match_exact_labels_and_use_bound_parameters(backup, tmp_path, dataset):
    store = Store(tmp_path / "runs.db")
    template = run_drill(capture(backup), backup, store, dataset="finance")
    store.add({**template, "id": "literal-label", "dataset": dataset})
    assert [row["id"] for row in store.list(dataset=dataset)] == ["literal-label"]
    with TestClient(create_app(store.path)) as client:
        response = client.get("/api/runs", params={"dataset": dataset})
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == ["literal-label"]
        assert len(client.get("/api/runs").json()) == 2


@pytest.mark.parametrize("dataset", [42, True, [], {}, "finance\x00private"])
def test_invalid_dataset_labels_are_rejected_before_creating_evidence(backup, tmp_path, dataset):
    store = Store(tmp_path / "runs.db")
    with pytest.raises(ValueError, match="Dataset"):
        run_drill(capture(backup), backup, store, dataset=dataset)
    assert store.list() == []
