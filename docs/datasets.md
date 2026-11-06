# Named dataset profiles

Use a private JSON file when the same backup needs repeated drills. A profile pairs its baseline, backup location, time objective, and restic selector so scheduled commands do not need to repeat those arguments.

Copy [the example configuration](../examples/datasets.example.json) to `datasets.local.json`, then edit the paths for your own disposable test dataset. The example is a template; its baseline, backup directories, and Azure repository are not created by the application. The root-level `datasets.local.json` filename is ignored by Git. Keep real configurations and credential-bearing URLs out of the public repository. Restic credentials still come from its environment or password-file configuration; there are no credential fields in a profile.

```sh
recovery-assurance run ./datasets.local.json --dataset documents-directory --db ./data/runs.db
```

The command runs one selected dataset and prints the same JSON evidence as `drill`. Exit codes remain **0** for passed, **1** for failed integrity or time target, and **2** for a configuration or execution error. Unknown dataset names fail without falling back to another profile.

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

This command supplies repeatable inputs. It does not install a scheduler, retry work, retain history automatically, or provision cloud resources. Windows Task Scheduler or cron can invoke it using the same exit-code contract as `drill`.
