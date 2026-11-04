"""Synthetic fixtures, real I/O, measured timings. Never invented run statistics."""

import shutil
import tempfile
from pathlib import Path

from .core import capture, run_drill
from .storage import Store


def seed_demo(store: Store) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="recovery-demo-") as directory:
        root = Path(directory)
        source, backup = root / "source", root / "backup"
        (source / "finance").mkdir(parents=True)
        (source / "operations").mkdir()
        for number in range(24):
            (source / "finance" / f"ledger-{number:02}.csv").write_text(
                f"id,description,amount\n{number},Synthetic transaction,{number * 17}\n",
                encoding="utf-8",
            )
        (source / "operations" / "runbook.txt").write_text(
            "Synthetic operations runbook. No personal or production data.\n" * 500,
            encoding="utf-8",
        )
        manifest = capture(source)
        shutil.copytree(source, backup)
        healthy = run_drill(
            manifest, backup, store, name="Operations · recovery verified", sample=True
        )
        (backup / "finance" / "ledger-00.csv").write_text("Corrupted fixture", encoding="utf-8")
        (backup / "finance" / "ledger-01.csv").unlink()
        (backup / "unexpected.txt").write_text("Unexpected fixture", encoding="utf-8")
        damaged = run_drill(
            manifest, backup, store, name="Finance · integrity failure", sample=True
        )
        timing = run_drill(
            manifest,
            source,
            store,
            name="Operations · recovery target missed",
            rto_seconds=0,
            sample=True,
        )
    return [healthy, damaged, timing]
