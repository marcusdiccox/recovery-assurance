# Recovery drill runbook

## Before a drill

1. Select a stable source dataset. Start with disposable synthetic files.
2. Capture a baseline manifest outside the source directory.
3. Back up that same stable dataset. Keep the baseline paired with the snapshot ID.
4. Check available temporary-disk space. Restored files are staged on the system temporary volume.
5. Choose a recovery-time target appropriate to the dataset; the target is measured, not enforced as a process timeout.

## Directory backend

```sh
recovery-assurance capture ./source --manifest ./baseline.json
# Copy ./source into ./backup-snapshot with your normal backup workflow.
recovery-assurance drill --manifest ./baseline.json --backup ./backup-snapshot --rto-seconds 60
```

Do not put the manifest inside the data you are verifying. Capture refuses to overwrite an existing manifest: choose a new filename for a new baseline.

## Restic backend

Run `restic snapshots --json` to identify the snapshot, then `restic ls ID` to inspect its stored path. Use `ID:/subfolder` so restored paths line up with the baseline. The restic repository value is passed unchanged, including remote repository URLs.

```sh
recovery-assurance drill --backend restic --backup /backups/repository --snapshot ID:/home/marcus/source --manifest ./baseline.json --rto-seconds 120 --timeout 600
```

Set restic credentials in your environment or password-file configuration before running the command. Never add password files or credential-bearing repository URLs to Git. Azure Blob repositories are supported by restic itself; no Azure credential integration or cloud deployment has been exercised in this project.

### Snapshot identity

Before restoring, the application runs `restic snapshots --json SELECTOR` and requires exactly one full 64-character snapshot ID. It passes that ID, plus any requested `:/subfolder`, to restore. This pins `latest` and abbreviated IDs within a drill even if new backups arrive during execution. An empty, ambiguous, or malformed lookup stops the drill with an error; it never falls back to another snapshot.

The JSON and HTML reports include:

- `snapshot`: the requested ID or `latest`, without private subfolder paths.
- `resolved_snapshot`: the full immutable ID. It remains available if resolution succeeded but the subsequent restore failed; it is `null` for directory drills or failed resolution. Older reports do not contain this field.

For consistent selection across future drills, rerun with `resolved_snapshot` as the explicit ID and the same subfolder. A snapshot ID identifies a backup; it does not by itself prove a recovery passed. Check the outcome, integrity, and time results too.

Lookup and restore share one `--timeout` budget. Resolution time also counts toward the measured recovery objective. Restore output and raw errors are suppressed; only the full ID is retained from lookup metadata. See [restic's JSON output documentation](https://restic.readthedocs.io/en/stable/075_scripting.html) for its scripting format.

## Interpret outcomes

| Finding | What to investigate |
|---|---|
| Changed | Same path, different bytes or size; inspect the backup and baseline pairing |
| Missing | Expected path absent; check the snapshot, excludes, and selected subfolder |
| Unexpected | Restored path absent from baseline; check for the wrong dataset or snapshot |
| Time objective missed | Restore + verification + setup/cleanup exceeded the objective |
| `CalledProcessError` | Run restic directly with the same snapshot to diagnose its failure |
| `TimeoutExpired` | Snapshot lookup and restore exhausted the shared `--timeout`; increase only after understanding the workload |
| `ValueError` / `OSError` | Check directory existence, symlinks, permissions, disk space, and stable files |

The local directory backend does not implement a hard timeout; `--timeout` applies only to restic. Do not run very large drills from the demo HTTP endpoint; that endpoint is intentionally limited to fixed synthetic fixtures.

## Evidence to keep

- Baseline manifest and the exact backup snapshot ID.
- JSON evidence report and, if useful, the HTML report.
- Environment details, dataset size, storage backend, and test conditions.
- Measured findings and actual corrective actions; rerun the drill after a correction.

For task scheduling, use Windows Task Scheduler or cron to run the CLI and inspect its exit code. [Named dataset profiles](datasets.md) keep baseline and backup arguments together; use absolute paths for the config and database in scheduled commands. Native scheduling, retries, notifications, and retention policies are not implemented yet.
