# Validation

This document distinguishes executed checks from configuration and future plans.

## Executed locally

- Windows, Python 3.12: **31 Python tests passed**, including an actual restic backup and restore.
- Node.js: **6 dashboard tests passed**, including combined filters, incomplete timing summaries, empty evidence, and distinct failure reasons.
- Ruff: lint and formatting checks passed.
- Built the wheel and source distribution, with dashboard assets included in the wheel.
- restic **0.19.1**, downloaded from the official release; checksum-verified installer also exercised on Windows.
- CLI demo: three real drills on 25 synthetic files per drill, with recorded durations and deliberate integrity/time failures.
- Browser, November 4, 2026: inspected the redesigned dashboard at desktop and narrow viewport sizes. Verified combined filters, empty datasets, the verification guide, sample execution, selected-run exports, and keyboard focus moving to the inspector. The narrow layout stayed within the viewport, with the table scrolling independently. No browser errors or warnings were recorded.
- Independent read-only code review completed. The recursive-copy containment finding, SQLite cleanup, and misleading sample-summary message were fixed and covered by regressions.
- The interface redesign received a separate read-only review. Its desktop focus and secondary-text contrast findings were corrected; live keyboard verification confirmed that selecting a run focuses the inspector.

## Executed in GitHub Actions

The preceding release passed Windows and Ubuntu jobs with the Python suite, JavaScript tests, Ruff, and package build (run `41763108831`). CI installs a checksum-verified pinned restic release. [GitHub Actions results](https://github.com/marcusdiccox/recovery-assurance/actions) are the authority for each subsequent revision; workflow configuration alone is not execution evidence.

## Not executed / outside this release

- Docker build/run: Docker Desktop's Linux engine was unavailable locally.
- Azure provisioning, cloud-restic repositories, identity, RBAC, hosted authentication, and alerts.
- Full disaster recovery, application startup, database consistency, permissions/metadata, RPO, large-data performance benchmarks, or multi-user load testing.

The demonstrated metrics are small local-fixture measurements, not production recovery claims.
