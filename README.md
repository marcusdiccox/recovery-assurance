# Recovery Assurance

Run isolated file-recovery drills, compare restored content with a saved baseline, and record whether integrity and recovery-time targets passed. Built with Python, FastAPI, SQLite, and plain browser modules.

![Recovery runs and file-level evidence inspector](docs/assets/dashboard.jpg)

The screenshot shows synthetic sample drills with measured restore and verification timings.

## What works today

- **Real restore drills:** directory snapshots and a restic adapter, including snapshot subfolders.
- **Content integrity:** relative paths, byte sizes, and streaming SHA-256; catches same-size corruption, missing files, and unexpected files.
- **Recovery-time objectives:** separate restore and verification timings, with an end-to-end target that also includes temporary-directory setup and cleanup. A run passes only when both integrity and time pass.
- **Persistent evidence:** SQLite history, a baseline fingerprint, JSON exports, and standalone HTML reports suitable for printing.
- **Local operations dashboard:** a searchable run list, dataset and outcome filters, a persistent evidence inspector, and measured duration comparisons.
- **Reproducible demo:** creates synthetic files and executes three actual drills: healthy recovery, integrity failure, and a deliberately impossible zero-second target. Timings are measured, never prefilled.
- **Automation-ready CLI:** exit codes suitable for scheduled tasks and CI.

## Quick start

Requires **Python 3.12+**. No cloud account, credentials, or restic installation is required for the local demo.

```powershell
git clone https://github.com/marcusdiccox/recovery-assurance.git
cd recovery-assurance
python -m venv .venv
# Windows PowerShell; activation is optional
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install --no-deps -e .
.venv\Scripts\python -m recovery_assurance demo
.venv\Scripts\python -m recovery_assurance serve
```

Open **http://127.0.0.1:8765**. The dashboard also has a **Run sample drills** button; each click appends three new measured records.

Select a run to inspect its file checks and timings, or download its HTML/JSON report. Press `/` to search. The summary describes the selected dataset; search and outcome filters narrow the history list. See [interface notes and references](docs/interface.md) for the design decisions.

On Linux/macOS, replace `.venv\Scripts\python` with `.venv/bin/python`.

## Test your own directory snapshot

Use stable test data. Capture a baseline **at the time the backup snapshot is created**, and store the manifest outside the source and backup directories. Reusing a baseline from different data will correctly report differences.

```powershell
recovery-assurance capture ./source --manifest ./baseline-2026-11-03.json
# Create your backup snapshot separately; this tool does not schedule backups.
recovery-assurance drill --manifest ./baseline-2026-11-03.json --backup ./backup-snapshot --name "Operations files" --rto-seconds 60
recovery-assurance serve
```

The `directory` backend copies the snapshot into a fresh temporary directory, verifies it, and cleans up. It never restores over your original source. It tests a directory copy, not a backup format or cloud recovery.

| Exit code | Meaning |
|---|---|
| `0` | Drill passed; other commands completed successfully |
| `1` | Drill completed but integrity or time objective failed |
| `2` | Configuration, restore, verification, or command error |

`recovery-assurance list` prints the latest 200 records. Use `--db ./data/custom.db` on drill, demo, list, and serve commands to select another database.

## Test a restic backup

Install [restic](https://restic.net/) separately. Credentials remain in restic's environment or password-file configuration. They are not stored in reports.

```powershell
$env:RESTIC_PASSWORD_FILE = "C:\private\restic-password.txt"
recovery-assurance drill --backend restic --backup "C:\backups\restic-repo" --snapshot "SNAPSHOT_ID:/C/data/source" --manifest ./baseline.json --name "Restic recovery" --rto-seconds 120 --timeout 600
```

Replace `SNAPSHOT_ID` and the subfolder with the actual values from `restic snapshots` and `restic ls SNAPSHOT_ID`. On Linux, a subfolder might be `/home/marcus/source`. Restic restores full absolute paths by default; selecting `ID:/subfolder` puts that subfolder's contents at the verification root. For reproducibility, prefer a full snapshot ID to `latest`. See the [restic runbook](docs/restic-runbook.md).

## Docker

```sh
docker build -t recovery-assurance .
docker run --rm -p 127.0.0.1:8765:8765 -v recovery-evidence:/data recovery-assurance
```

The container runs as a non-root user. The image includes the dashboard and local sample drills; restic is not bundled. Docker commands are provided for reproducibility; see [validation](docs/validation.md) for checks actually run.

## Engineering

```sh
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps -e .
python -m pytest -q
node --test tests/dashboard.test.mjs
python -m ruff check .
python -m ruff format --check .
python -m build
```

CI runs on Windows and Ubuntu and installs a pinned restic release for the actual backup/restore integration test. The integration test skips locally if restic is absent; ordinary demo, API, and CLI tests still run.

- [Architecture and tradeoffs](docs/architecture.md)
- [Recovery drill runbook](docs/restic-runbook.md)
- [Validation and limitations](docs/validation.md)
- [Roadmap](docs/roadmap.md)

## Boundaries

This is a **local, single-user engineering project**, not a production disaster-recovery service. The server binds to loopback and validates hostnames; it has no login or multi-user authorization. Do not expose it publicly. The browser can trigger only synthetic sample drills; real targets are configured through the CLI.

Verification covers regular-file content and paths, not permissions, file metadata, empty directories, application startup, database consistency, or recovery-point objectives. Symlinks and junctions are rejected. Use stable snapshots; concurrent changes are unsupported. Reports are append-only through the application, but SQLite is not a signed or tamper-proof audit log. Preserve baseline manifests separately; the fingerprint identifies a baseline and does not establish its trustworthiness.

No Azure resources are provisioned by this repository. Cloud identity, scheduling, storage, and telemetry integrations are future work, documented explicitly in the roadmap.
