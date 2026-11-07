# Architecture

## Data flow

```mermaid
flowchart LR
  Source[Stable source files] --> Capture[Capture manifest]
  Capture --> Baseline[Saved paths + sizes + SHA-256]
  Backup[Directory snapshot or restic repository] --> Restore[Isolated temporary restore]
  Restore --> Compare[Verify content and paths]
  Baseline --> Compare
  Compare --> Evidence[Measured run report]
  Evidence --> SQLite[(Local SQLite)]
  SQLite --> Dashboard[Dashboard + JSON / HTML exports]
```

## Modules

| Module | Responsibility |
|---|---|
| `core.py` | Baseline validation, streaming hashes, backend invocation, comparison, timings |
| `storage.py` | Parameterized, append-only SQLite inserts and bounded, optionally dataset-filtered history reads |
| `demo.py` | Synthetic fixtures and genuine successful / failed recovery drills |
| `cli.py` | Capture, direct/profile drills, list, demo, and loopback server commands |
| `config.py` | Versioned named profiles, strict field validation, and config-relative local paths |
| `web.py` | Read APIs, local sample drill action, report downloads, static assets |
| `reports.py` | Escaped, self-contained HTML evidence |
| `static/` | Dependency-free dashboard, accessible controls, native dialogs, SVG chart |

## Design decisions

**Saved baselines rather than the current source.** The expected state must represent the backed-up data. Hashing today's live source after restoring last week's backup would confuse ordinary changes with corruption. Each report records the baseline's canonical-JSON SHA-256.

**All checks must pass.** A fast restore with changed files fails. A correct restore outside its objective fails. A failed backend is an error record, never a successful empty comparison. Empty manifests cannot establish recovery success.

**Temporary restore destinations.** Every drill gets a fresh directory; original data is not overwritten. Temporary files are removed before a successful run is finalized. The end-to-end timer includes cleanup and excludes report persistence.

**A boring local stack.** Python, FastAPI, SQLite, and plain JavaScript keep the first version easy to run and inspect. SQLite uses per-operation connections and WAL. There is no frontend build service or third-party analytics.

**Narrow browser authority.** Real filesystem paths and backup credentials are not browser inputs. The demo mutation requires an explicit header and same-origin requests. Trusted hosts and a restrictive dashboard CSP reduce exposure; these controls do not replace authentication for a future hosted service.

**Restic is a subprocess boundary.** Arguments are passed without a shell; credentials come from the environment. Snapshot lookup and restore share a timeout and have no interactive stdin. Lookup JSON is validated in memory and only its full snapshot ID is retained. Restore output and stderr are suppressed so reports do not retain raw credential-bearing URLs or environment details. Inspect restic directly when diagnosing failures.

## Evidence contract

A report records its ID, UTC timestamps, baseline hash, backend, requested snapshot identifier for restic, sample-data flag, objective, measured durations, integrity result, comparison counts and paths, outcome, and a sanitized error category. The additive `resolved_snapshot` field records the immutable full ID selected before restore; `snapshot` continues to record the requested selector. The resolved ID is used for the restore itself, not looked up again. It is retained on a later restore failure, and is `null` when resolution failed or the backend is a directory. Existing stored reports remain readable without migration.

Stored report JSON is not cryptographically signed. The application has no update/delete route, but anyone with filesystem access can alter the database. Filenames in reports may themselves be sensitive: use synthetic data for portfolio screenshots.

Named profiles save their key in the additive `dataset` field; direct drills can provide the same label. `Store.list(dataset=...)`, CLI history, and the read API apply an exact parameterized filter before the history limit using SQLite's JSON extraction. Old rows need no migration: absent or null labels remain unassigned and appear in unfiltered history. A dataset label links historical baseline fingerprints and resolved snapshot IDs, but it is not a separate catalog or a uniqueness guarantee across configuration files.
