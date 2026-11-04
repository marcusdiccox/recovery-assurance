# Validation

This document distinguishes executed checks from configuration and future plans.

## Executed locally

- Windows, Python 3.12: **31 Python tests passed**, including an actual restic backup and restore.
- Node.js: **2 dashboard outcome-summary tests passed**.
- Ruff: lint and formatting checks passed.
- Built the wheel and source distribution, with dashboard assets included in the wheel.
- restic **0.19.1**, downloaded from the official release; checksum-verified installer also exercised on Windows.
- CLI demo: three real drills on 25 synthetic files per drill, with recorded durations and deliberate integrity/time failures.
- Chrome: desktop overview and file-level evidence dialog inspected. File mismatch, missing file, unexpected file, timing, and report links displayed correctly.
- Independent read-only code review completed. The recursive-copy containment finding, SQLite cleanup, and misleading sample-summary message were fixed and covered by regressions.

## Executed in GitHub Actions

The initial Windows and Ubuntu jobs passed the Python suite, JavaScript tests, Ruff, and package build (run `41762905377`). CI installs a checksum-verified pinned restic release. GitHub Actions results are the authority for each subsequent revision; workflow configuration alone is not execution evidence.

## Not executed / outside this release

- Docker build/run: Docker Desktop's Linux engine was unavailable locally.
- Azure provisioning, cloud-restic repositories, identity, RBAC, hosted authentication, and alerts.
- Full disaster recovery, application startup, database consistency, permissions/metadata, RPO, large-data performance benchmarks, or multi-user load testing.

The demonstrated metrics are small local-fixture measurements, not production recovery claims.
