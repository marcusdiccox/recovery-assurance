# Named dataset profiles

Use a private JSON file when the same backup needs repeated drills. A profile pairs its baseline, backup location, time objective, and restic selector so scheduled commands do not need to repeat those arguments.

Copy [the example configuration](../examples/datasets.example.json) to `datasets.local.json`, then edit the paths for your own disposable test dataset. The example is a template; its baseline, backup directories, and Azure repository are not created by the application. The root-level `datasets.local.json` filename is ignored by Git. Keep real configurations and credential-bearing URLs out of the public repository. Restic credentials still come from its environment or password-file configuration; there are no credential fields in a profile.

```sh
recovery-assurance run ./datasets.local.json --dataset documents-directory --db ./data/runs.db
```

The command runs one selected dataset and prints the same JSON evidence as `drill`. Exit codes remain **0** for passed, **1** for failed integrity or time target, and **2** for a configuration or execution error. Unknown dataset names fail without falling back to another profile.

## Review a dataset's history

Named-profile runs save the profile key in the report's `dataset` field, separately from its display name. This identity stays attached to passed, failed, and error records alongside the baseline fingerprint and resolved restic snapshot ID. The JSON and HTML exports include it.

```sh
recovery-assurance list --dataset documents-directory --db ./data/runs.db
```

This returns up to 200 matching records, ordered by stored start timestamp descending, then ID descending for equal timestamps. Matching happens before the limit, so a quiet dataset's older records remain accessible even when other datasets have newer activity. Labels match exactly, including case. An unknown label returns an empty list and exit code 0.

To continue through older evidence, use the final record's `id` as the next page's cursor:

```sh
recovery-assurance list --dataset documents-directory --before LAST_RUN_ID --db ./data/runs.db
```

Repeat the same dataset filter and replace `LAST_RUN_ID` with the final ID on each returned page. The cursor record is excluded; stop when the list is empty. The read API supports the same traversal with `/api/runs?dataset=documents-directory&before=LAST_RUN_ID`, retaining its JSON list response and 200-record page size. Omitting `before` requests the first page.

The cursor must identify a stored run in the selected database. Unknown or empty cursor IDs return CLI exit code **2** or API **404**, rather than restarting history. Cursor position and dataset membership are independent: a cursor from another dataset still sets the boundary, and every result must match the requested label.

Newer inserts do not shift an existing cursor boundary, including ties with higher IDs. This is a live traversal; records inserted with older timestamps can appear on later pages. Use a stable local history copy when an audit needs a fixed snapshot.

CLI `list --db ARCHIVE` opens an existing history file read-only, so dataset filters, pagination, and rejected cursors preserve a prepared archive's database bytes and journal mode. Write-protected files are supported when SQLite can read them; WAL histories retain the [sidecar requirements described in the README](../README.md#export-saved-evidence-from-the-cli). An existing file without initialized history is rejected rather than given a new runs table.

For a direct drill, add `--dataset documents-directory` to the existing `drill` command to attach the same label. The local read API accepts `/api/runs?dataset=documents-directory` as well. Omitting the filter returns the ordinary combined history; the dashboard's existing sample/real-run controls are unchanged.

Dataset labels are local database groupings, not globally registered IDs. Reusing the same key across config files combines their history, so choose distinct keys when that is not intended. Renaming a profile key starts a new grouping; changing only its `name` keeps the grouping. Older reports and unlabelled direct/sample drills remain visible in combined history and are not retroactively assigned to a profile. Choose non-sensitive labels when exporting public portfolio evidence.

## Path rules

- Relative manifest paths and bare local backup paths resolve against the configuration file's directory, including for local restic repositories. They are independent of the shell's working directory.
- Absolute local paths are accepted. Paths must be valid on the operating system running the command. Windows drive-relative paths such as `C:backup` are rejected; use `C:/backups/finance` or `./backup`.
- Restic repository URIs, such as `azure:recovery:/documents`, are passed unchanged. Use an absolute repository location within the URI when its backend supports relative locations.
- `--db` keeps its ordinary CLI behavior: a relative database path resolves against the working directory. For a scheduler, use absolute paths for the config file and `--db`.
- The loader does not expand shell variables, environment variables, or `~` inside JSON strings.

## Fields

The root object has `schema_version: 1` and a `datasets` object keyed by dataset name. A selected profile accepts only these settings:

| Setting | Required / default | Meaning |
|---|---|---|
| `manifest` | Required | Baseline JSON captured at backup time |
| `backup` | Required | Directory snapshot, local restic repository, or remote restic URI |
| `backend` | `directory` | `directory` or `restic` |
| `name` | Dataset key | Display name in evidence, 1–100 characters |
| `snapshot` | `latest` | Restic ID or `latest`, optionally with `:/subfolder` |
| `rto_seconds` | `60` | Finite nonnegative recovery-time objective; zero deliberately fails any nonzero-duration drill |
| `timeout` | `600` | Finite positive shared restic lookup/restore timeout |

The file must be UTF-8, with or without a BOM, and no larger than 1 MiB. Duplicate JSON keys, unknown settings, and invalid field types are rejected. Only the selected profile's settings are validated; an unused profile cannot change the selected drill. Snapshot and display-name rules remain enforced by the existing drill engine. Profiles never create or update baseline manifests, and they do not create backups.

This command supplies repeatable inputs. It does not install a scheduler, retry work, apply retention policies, or provision cloud resources. Windows Task Scheduler or cron can invoke it using the same exit-code contract as `drill`.
