import json
import subprocess
import sys

from fastapi.testclient import TestClient

from recovery_assurance.demo import seed_demo
from recovery_assurance.storage import Store
from recovery_assurance.web import create_app


def test_demo_runs_real_drills_with_three_distinct_outcomes(tmp_path):
    store = Store(tmp_path / "runs.db")
    seed_demo(store)
    runs = store.list()
    assert len(runs) == 3
    assert all(run["sample"] for run in runs)
    assert {run["status"] for run in runs} == {"passed", "failed"}
    corrupt = next(run for run in runs if run["name"] == "Finance · integrity failure")
    assert len(corrupt["comparison"]["changed"]) == 1
    assert len(corrupt["comparison"]["missing"]) == 1
    assert len(corrupt["comparison"]["unexpected"]) == 1
    assert next(run for run in runs if run["rto_seconds"] == 0)["rto_met"] is False


def test_dashboard_empty_state_and_demo_exports(tmp_path):
    with TestClient(create_app(tmp_path / "runs.db")) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/runs").json() == []
        assert client.get("/").status_code == 200
        assert client.post("/api/demo", headers={"X-Recovery-Action": "demo"}).status_code == 201
        runs = client.get("/api/runs").json()
        assert len(runs) == 3
        report = client.get(f"/api/runs/{runs[0]['id']}/report.json")
        assert report.json() == runs[0]
        html = client.get(f"/api/runs/{runs[0]['id']}/report.html")
        assert html.status_code == 200
        assert "Content-Disposition" in html.headers
        assert client.get("/api/runs/unknown/report.json").status_code == 404


def test_browser_mutations_require_explicit_header_and_same_origin(tmp_path):
    with TestClient(create_app(tmp_path / "runs.db")) as client:
        assert client.post("/api/demo").status_code == 403
        assert (
            client.post(
                "/api/demo",
                headers={
                    "X-Recovery-Action": "demo",
                    "Origin": "https://example.com",
                },
            ).status_code
            == 403
        )
        assert client.get("/api/runs").json() == []


def test_dashboard_rejects_untrusted_host(tmp_path):
    with TestClient(create_app(tmp_path / "runs.db")) as client:
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_report_escapes_untrusted_names(tmp_path):
    store = Store(tmp_path / "runs.db")
    seed_demo(store)
    report = store.list()[0]
    report["id"] = "escape-test"
    report["name"] = '<script>alert("x")</script>'
    report["comparison"]["missing"] = ["<img src=x onerror=alert(1)>"]
    store.add(report)
    with TestClient(create_app(tmp_path / "runs.db")) as client:
        html = client.get("/api/runs/escape-test/report.html").text
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<img src=x" not in html


def test_cli_capture_drill_and_failure_exit_codes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("original")
    manifest = tmp_path / "baseline.json"
    db = tmp_path / "runs.db"

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "recovery_assurance", *map(str, args)],
            capture_output=True,
            text=True,
        )

    assert cli("capture", source, "--manifest", manifest).returncode == 0
    good = cli(
        "drill",
        "--manifest",
        manifest,
        "--backup",
        source,
        "--db",
        db,
        "--dataset",
        "operations",
    )
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["status"] == "passed"
    assert json.loads(good.stdout)["dataset"] == "operations"
    (source / "file.txt").write_text("corrupted")
    bad = cli("drill", "--manifest", manifest, "--backup", source, "--db", db)
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["comparison"]["changed"] == ["file.txt"]
    error = cli("drill", "--manifest", tmp_path / "absent.json", "--backup", source, "--db", db)
    assert error.returncode == 2
    assert "Traceback" not in error.stderr
