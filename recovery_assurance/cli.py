"""Command-line interface for repeatable, schedulable recovery drills."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .config import load_dataset
from .core import capture, load_manifest, run_drill, save_manifest
from .demo import seed_demo
from .health import check_datasets, check_health
from .locking import DrillLockBusy, drill_lock
from .reports import html_report
from .storage import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recovery Assurance — verify recoverability")
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture", help="Create an immutable baseline manifest")
    capture_parser.add_argument("source", type=Path)
    capture_parser.add_argument("--manifest", required=True, type=Path)
    drill = sub.add_parser(
        "drill", help="Restore into isolation and verify against a saved baseline"
    )
    drill.add_argument("--manifest", required=True, type=Path)
    drill.add_argument(
        "--backup",
        required=True,
        help="Directory snapshot path or restic repository URL/path",
    )
    drill.add_argument("--backend", choices=["directory", "restic"], default="directory")
    drill.add_argument("--snapshot", default="latest", help="restic ID[:/subfolder] or latest")
    drill.add_argument("--name", default="Recovery drill")
    drill.add_argument("--dataset", help="Optional dataset label saved with this drill")
    drill.add_argument("--rto-seconds", type=float, default=60)
    drill.add_argument(
        "--timeout",
        type=float,
        default=600,
        help="Shared restic lookup and restore timeout in seconds",
    )
    configured = sub.add_parser("run", help="Run a named dataset from a JSON configuration")
    configured.add_argument("config", type=Path, help="Dataset configuration file")
    configured.add_argument("--dataset", required=True, help="Dataset name in the configuration")
    health = sub.add_parser("check", help="Check freshness and outcome of saved dataset evidence")
    health.add_argument("--dataset", required=True, help="Exact dataset label to monitor")
    health.add_argument(
        "--max-age-hours", required=True, type=float, help="Positive maximum age since completion"
    )
    multi_health = sub.add_parser("check-many", help="Check every explicitly required dataset")
    multi_health.add_argument(
        "--dataset",
        dest="datasets",
        action="append",
        required=True,
        help="Exact required dataset label; repeat for each dataset",
    )
    multi_health.add_argument(
        "--max-age-hours", required=True, type=float, help="Positive maximum age since completion"
    )
    for command in (drill, configured):
        command.add_argument(
            "--lock-file",
            type=Path,
            help="Skip with exit 75 if another drill holds this local .lock file",
        )
    exporter = sub.add_parser("export", help="Export a saved run without rerunning the drill")
    exporter.add_argument("run_id", help="ID from a drill report or the history list")
    exporter.add_argument("--format", choices=["json", "html"], default="json")
    exporter.add_argument("--output", required=True, type=Path, help="New evidence file to create")
    history_backup = sub.add_parser(
        "backup-history", help="Create a consistent SQLite copy of all saved run history"
    )
    history_backup.add_argument(
        "--output", required=True, type=Path, help="New SQLite file to create"
    )
    for command in (
        drill,
        configured,
        health,
        multi_health,
        exporter,
        history_backup,
        sub.add_parser("demo", help="Execute three drills with synthetic files"),
        sub.add_parser("serve", help="Start the local dashboard"),
        sub.add_parser("list", help="Print the latest 200 evidence records"),
        sub.add_parser("datasets", help="List datasets recorded by real drills"),
    ):
        command.add_argument("--db", type=Path, default=Path("data/runs.db"))
    serve = sub.choices["serve"]
    serve.add_argument("--port", type=int, default=8765)
    sub.choices["list"].add_argument(
        "--dataset", help="Only list runs with this exact dataset label"
    )
    sub.choices["list"].add_argument(
        "--before", help="Continue history before the final run ID from a previous page"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            manifest = capture(args.source)
            save_manifest(manifest, args.manifest)
            print(f"Captured {len(manifest['files'])} files. Baseline: {args.manifest}")
            return 0
        if args.command in ("drill", "run"):
            with drill_lock(args.lock_file, database=args.db):
                store = Store(args.db)
                if args.command == "run":
                    options = load_dataset(args.config, args.dataset)
                    manifest_path = options.pop("manifest")
                else:
                    manifest_path = args.manifest
                    options = {
                        "backup": args.backup,
                        "name": args.name,
                        "rto_seconds": args.rto_seconds,
                        "backend": args.backend,
                        "snapshot": args.snapshot,
                        "timeout": args.timeout,
                    }
                report = run_drill(
                    load_manifest(manifest_path), store=store, dataset=args.dataset, **options
                )
                print(json.dumps(report, indent=2))
                return {"passed": 0, "failed": 1, "error": 2}[report["status"]]
        read_only = args.command in (
            "export",
            "backup-history",
            "check",
            "check-many",
            "datasets",
        ) or (args.command == "list" and args.db.is_file())
        if read_only and not args.db.is_file():
            raise ValueError("Database does not exist; select an existing history with --db")
        store = Store(args.db, read_only=read_only)
        if args.command == "check":
            result = check_health(store, dataset=args.dataset, max_age_hours=args.max_age_hours)
            print(json.dumps(result, indent=2, allow_nan=False))
            return 0 if result["healthy"] else 1
        if args.command == "check-many":
            result = check_datasets(store, datasets=args.datasets, max_age_hours=args.max_age_hours)
            print(json.dumps(result, indent=2, allow_nan=False))
            return 0 if result["healthy"] else 1
        if args.command == "demo":
            print(json.dumps(seed_demo(store), indent=2))
        elif args.command == "list":
            print(json.dumps(store.list(dataset=args.dataset, before=args.before), indent=2))
        elif args.command == "datasets":
            print(json.dumps(store.dataset_inventory(), indent=2, allow_nan=False))
        elif args.command == "backup-history":
            store.backup_to(args.output)
            print("History backup created.")
        elif args.command == "export":
            report = store.get(args.run_id)
            if report is None:
                raise ValueError("Run not found")
            document = (
                html_report(report)
                if args.format == "html"
                else json.dumps(report, indent=2, allow_nan=False) + "\n"
            )
            with args.output.open("x", encoding="utf-8") as file:
                file.write(document)
            print(f"Exported {args.format.upper()} evidence.")
        else:
            import uvicorn

            from .web import create_app

            print(f"Dashboard: http://127.0.0.1:{args.port}")
            uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)
        return 0
    except DrillLockBusy as error:
        print(f"Skipped: {error}", file=sys.stderr)
        return 75
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
