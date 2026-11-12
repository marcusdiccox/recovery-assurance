# Roadmap

## v0.1 — local recovery evidence

Implemented: baseline capture, directory/restic restore drills, SHA-256 comparison, measured timing, SQLite history, CLI, local dashboard, portable reports, synthetic demo, and automated tests.

Restic drills now resolve and pin full snapshot IDs before restoring, record them in evidence, and share a lookup/restore timeout budget.

Named dataset profiles now pair baseline paths, backup locations, and drill settings for repeatable CLI execution from different working directories.

Provided dataset labels now persist with each run and support CLI/API history filtering before the record limit, linking past baseline fingerprints and resolved snapshot IDs.

Saved evidence can now be exported from the CLI as JSON or standalone HTML without running a server or repeating a drill. Existing output files are protected from overwriting, and source databases are opened read-only without journal-mode or schema initialization.

Full history can now be copied with `backup-history` into a standalone SQLite archive, including committed WAL records and history beyond the latest-200 view. Source access is read-only and existing destinations are protected.

## Next — make evidence operational

- Durable job scheduling, concurrency limits, and retry policy for real drills.
- Registered dataset identities and an explicit baseline-to-snapshot catalog beyond run history.
- Retention policies, signed evidence, and external report delivery beyond local exports.
- File-level progress and cancellation for large datasets.
- Performance baselines using documented, representative workloads.

## Azure integration — proposed, not deployed

- Container Apps Jobs for isolated scheduled workers.
- Managed identities and scoped RBAC for storage access.
- Blob Storage for baseline manifests and exported evidence.
- Managed database for centralized history; do not share a SQLite file across workers.
- Azure Monitor for failed-drill alerts and execution telemetry.
- Bicep for reproducible infrastructure and GitHub Actions OIDC for deployment.
- Microsoft Entra authentication and authorization before exposing a dashboard.

Start with a disposable test subscription and a measured cost estimate. The local application is not ready for an internet-facing deployment.

## Cloud Operations Center

Recovery Assurance can become a module alongside inventory, availability checks, incident history, and cost reporting. Define shared identity, dataset, and evidence contracts before adding those modules.
