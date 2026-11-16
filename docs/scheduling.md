# Run a dataset with a host scheduler

Use a named profile for repeatable drills after a backup has finished and its matching baseline is available. This guide connects the existing CLI to Windows Task Scheduler or Linux cron. Recovery Assurance's built-in job scheduling, retry policy and worker coordination remain [roadmap work](roadmap.md).

## Prepare the account and paths

1. Install the package in a dedicated virtual environment, then select its Python executable directly. Activation is unnecessary.
2. Prepare a private [dataset configuration](datasets.md) and test the profile with disposable data first. The examples use the key `finance`; replace it with a real profile key.
3. Use absolute Python, config and database paths. Profile-relative baseline/backup paths use the config directory; `--db` otherwise uses the process working directory.
4. Run under an account with the access required by the selected backend, plus write access to the database directory, temporary restore space and any log directory. Keep SQLite and lock files on local storage.
5. Make restic available on that account's PATH and configure its credentials for that account. A scheduler does not inherit the environment of a terminal opened later. Prefer protected password-file references; keep passwords and credential-bearing URLs out of task arguments, source control and public logs.

A manual preflight should exercise the same account, executable, profile and database as the scheduled action. Confirm that a new run appears in that exact database before enabling repetition.

## Windows Task Scheduler

In **Create Task**, choose a unique name such as `RecoveryAssurance-Finance` and the account prepared above. For unattended execution, select the host's run-when-logged-out option and supply required credentials locally according to its policy. Elevated privileges are not a substitute for granting the required access.

For a **Start a program** action, replace these example values:

| Field | Value |
|---|---|
| Program/script | `C:\Tools\recovery-assurance\.venv\Scripts\python.exe` |
| Arguments | `-m recovery_assurance run "C:\Private\Recovery\datasets.local.json" --dataset finance --db "C:\Private\Recovery\runs.db"` |
| Start in | `C:\Tools\recovery-assurance` |

Keep the executable separate from its arguments and quote argument paths containing spaces. Shell redirection such as `>` is not part of this direct Python action. Task Scheduler does not save Python stdout automatically; inspect persisted evidence and task results, or provide a private logging wrapper when needed. See Microsoft's [action definition reference](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskaction?view=windowsserver2025-ps).

Add a daily trigger at the intended host-local time, for example 09:00, after the backup window. Under **Settings**, select **Do not start a new instance** and enable the missed-start behavior appropriate for the host. Choose an execution limit based on measured restore, verification and cleanup time. These PowerShell equivalents construct action/settings definitions for inspection; they do not register or start a task:

```powershell
$drillPython = 'C:\Tools\recovery-assurance\.venv\Scripts\python.exe'
$drillConfig = 'C:\Private\Recovery\datasets.local.json'
$drillDb = 'C:\Private\Recovery\runs.db'
$drillArguments = '-m recovery_assurance run "{0}" --dataset finance --db "{1}"' -f $drillConfig, $drillDb
$drillAction = New-ScheduledTaskAction -Execute $drillPython -Argument $drillArguments -WorkingDirectory 'C:\Tools\recovery-assurance'
$drillSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
```

The 30-minute limit is an example, not a tested production duration. `IgnoreNew` applies to that task registration; a separate task or manual CLI invocation can still overlap. Review the host's power, network and logon conditions and [settings behavior](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps). Avoid automatic retries until failures have been classified: an integrity failure is not fixed by repeating the same restore.

Save the task through Task Scheduler, run it once on demand, and inspect **Last Run Result**, its history and the corresponding database record. A launcher/account failure may produce a host error code rather than a CLI exit code. A task forcibly stopped by the scheduler can have no completed evidence record.

## Linux cron with a local overlap lock

Prepare private, account-writable database/log directories and install util-linux `flock`. Save this wrapper at `/opt/recovery-assurance/run-finance-drill.sh`, replacing the paths and profile key. The wrapper captures the command's exit status before writing the log marker:

```sh
#!/bin/sh
set -u
umask 077
PATH=/usr/local/bin:/usr/bin:/bin
export PATH
# Set this only when the selected restic profile uses a password file.
RESTIC_PASSWORD_FILE=/etc/recovery-assurance/restic-password.txt
export RESTIC_PASSWORD_FILE
drill_log=/var/log/recovery-assurance/finance.log

/usr/bin/flock --nonblock --conflict-exit-code 75 \
  /var/lib/recovery-assurance/finance.lock \
  /opt/recovery-assurance/.venv/bin/python -m recovery_assurance run \
  /etc/recovery-assurance/datasets.local.json --dataset finance \
  --db /var/lib/recovery-assurance/runs.db >>"$drill_log" 2>&1
drill_status=$?
printf '%s scheduler_exit=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$drill_status" >>"$drill_log"
exit "$drill_status"
```

The log contains report JSON plus status markers, so it is not a single JSON document. Reports and logs can contain filenames and dataset labels; protect existing log permissions as well as newly created files and set an appropriate rotation policy.

In the prepared account's user crontab, a daily 09:00 entry is:

```cron
0 9 * * * /bin/sh /opt/recovery-assurance/run-finance-drill.sh
```

Check the daemon's timezone and missed-run behavior on the host. Do not assume its environment matches an interactive shell. Refer to the host's [crontab format](https://manpages.debian.org/trixie/cron/crontab.5.en.html).

All scheduled invocations for this profile must use the same local lock path. `flock` holds the lock around the child command; conflict exit **75** means the wrapper skipped a run because another holder was active. Leave the lock file in place between runs: the lock is released when its holder closes it. A direct invocation without this lock is not coordinated, and this is not a cross-host worker lock. See the [util-linux flock reference](https://man7.org/linux/man-pages/man1/flock.1.html).

## Check outcomes, not just launches

| Result | Interpretation and next action |
|---|---|
| CLI `0` | Drill passed; inspect its run ID, dataset, baseline fingerprint and selected snapshot evidence. |
| CLI `1` | Integrity or recovery-time objective failed; review the findings before retrying. |
| CLI `2` | Input or execution error; inspect private stderr/logs and the report when one was created. A config error may create no run. |
| Wrapper `75` | Local lock conflict; no drill started through that wrapper. Check the current holder and expected duration. |
| Host/startup/forced-stop error | Inspect scheduler history and account/path/availability conditions; a finished report is not guaranteed. |

After a launch, query the configured database explicitly:

```sh
recovery-assurance list --dataset finance --db /var/lib/recovery-assurance/runs.db
```

Use the Windows database path instead on Windows. Check that the record is new and has the expected dataset and baseline; an old passing row does not prove the latest scheduled invocation succeeded. For an identified run, use `export RUN_ID --db DATABASE --output NEW_REPORT_FILE` to collect evidence. Keep evidence archiving separate from the scheduled restore and use a new `backup-history` destination each time.

The profile's `timeout` bounds restic lookup/restore, while its RTO is a pass/fail target. Neither is a whole-job scheduler deadline. Host scheduling, locking and logs do not themselves provide alerts, offsite evidence storage or retries; arrange those operational policies separately.

## Validation scope

Windows action/settings objects were constructed locally, and the equivalent direct process invocation was exercised with synthetic data, absolute config/database paths containing spaces and a different working directory. Exit codes 0/1/2 were observed, with two completed records persisted for the successful and integrity-failed drills. PowerShell snippets and the POSIX wrapper parsed successfully, the latter through Git sh. No scheduled task was registered or triggered during validation. The Linux wrapper/cron recipe follows the cited host tools; it was not executed locally, and unattended account access, timed triggers and cross-host coordination are not claimed as tested.
