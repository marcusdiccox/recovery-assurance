"""Capture baselines, restore into isolation, and produce measured evidence."""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .storage import Store


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _files(root: Path) -> list[Path]:
    root = Path(root)
    if _is_link(root):
        raise ValueError("Symbolic links and junctions are not supported")
    if not root.is_dir():
        raise ValueError("Expected an existing directory")
    files = []

    def fail(error):
        raise error

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs + names:
            path = Path(directory) / name
            if _is_link(path):
                raise ValueError("Symbolic links and junctions are not supported")
        for name in names:
            path = Path(directory) / name
            if not path.is_file():
                raise ValueError("Only regular files are supported")
            files.append(path)
    return sorted(files)


def _fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("File changed during capture; use a stable snapshot")
    return {"size": after.st_size, "sha256": digest.hexdigest()}


def capture(source: Path) -> dict:
    """Record a nonempty directory without including absolute machine paths."""
    source = Path(source)
    files = {path.relative_to(source).as_posix(): _fingerprint(path) for path in _files(source)}
    manifest = {"schema_version": 1, "created_at": _now(), "files": files}
    _validate(manifest)
    return manifest


def _validate(manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("A baseline cannot be empty")
    for name, info in files.items():
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or ":" in name
            or PurePosixPath(name).is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))
        ):
            raise ValueError("Manifest paths must be safe, relative POSIX paths")
        if (
            not isinstance(info, dict)
            or type(info.get("size")) is not int
            or info["size"] < 0
            or not isinstance(info.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", info["sha256"])
        ):
            raise ValueError("Each manifest entry needs a size and SHA-256 digest")


def save_manifest(manifest: dict, path: Path) -> None:
    _validate(manifest)
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)


def load_manifest(path: Path) -> dict:
    if Path(path).stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Manifest exceeds 32 MiB limit")
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate(manifest)
    return manifest


def compare(manifest: dict, restored: Path) -> dict:
    _validate(manifest)
    restored = Path(restored)
    actual = {p.relative_to(restored).as_posix(): _fingerprint(p) for p in _files(restored)}
    expected = manifest["files"]
    common = set(expected) & set(actual)
    changed = sorted(name for name in common if expected[name] != actual[name])
    return {
        "matched": len(common) - len(changed),
        "changed": changed,
        "missing": sorted(set(expected) - set(actual)),
        "unexpected": sorted(set(actual) - set(expected)),
        "expected_files": len(expected),
        "restored_files": len(actual),
        "expected_bytes": sum(entry["size"] for entry in expected.values()),
    }


def _resolve_restic_snapshot(backup: Path | str, selector: str, timeout: float) -> str:
    """Resolve once, retaining only the immutable ID from restic's private metadata."""
    binary = shutil.which("restic")
    if binary is None:
        raise ValueError("restic is not installed or is not on PATH")
    result = subprocess.run(
        [binary, "--repo", str(backup), "snapshots", "--json", selector],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=True,
    )
    snapshots = json.loads(result.stdout)
    if not isinstance(snapshots, list) or len(snapshots) != 1 or not isinstance(snapshots[0], dict):
        raise ValueError("Snapshot selector must resolve to exactly one snapshot")
    resolved = snapshots[0].get("id")
    if (
        not isinstance(resolved, str)
        or not re.fullmatch(r"[0-9a-f]{64}", resolved)
        or (selector != "latest" and not resolved.startswith(selector.lower()))
    ):
        raise ValueError("Invalid resolved snapshot ID")
    return resolved


def _restore(backend: str, backup: Path | str, target: Path, snapshot: str, timeout: float) -> None:
    if backend == "directory":
        # Validate the entire tree before copying: never follow source links.
        backup = Path(backup)
        if target.resolve().is_relative_to(backup.resolve()):
            raise ValueError("Restore destination must be outside the backup snapshot")
        _files(backup)
        shutil.copytree(backup, target, dirs_exist_ok=True, symlinks=True)
    else:
        binary = shutil.which("restic")
        if binary is None:
            raise ValueError("restic is not installed or is not on PATH")
        # No shell. Credentials come from restic's environment, never the database.
        subprocess.run(
            [binary, "--repo", str(backup), "restore", snapshot, "--target", str(target)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=True,
        )


def run_drill(
    manifest: dict,
    backup: Path | str,
    store: Store,
    *,
    name: str = "Recovery drill",
    rto_seconds: float = 60,
    backend: str = "directory",
    snapshot: str = "latest",
    timeout: float = 600,
    sample: bool = False,
    dataset: str | None = None,
) -> dict:
    """RTO covers restore + integrity verification, excluding report persistence."""
    _validate(manifest)
    if dataset is not None and (not isinstance(dataset, str) or "\x00" in dataset):
        raise ValueError("Dataset must be a string without NUL characters")
    if not math.isfinite(rto_seconds) or rto_seconds < 0:
        raise ValueError("Recovery-time objective must be finite and nonnegative")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Restore timeout must be finite and positive")
    if backend not in ("directory", "restic"):
        raise ValueError("Unknown restore backend")
    if not re.fullmatch(r"(?:latest|[a-fA-F0-9]{8,64})(?::/[^\r\n]*)?", snapshot):
        raise ValueError("Use a restic snapshot ID or latest, optionally with :/subfolder")
    if not name.strip() or len(name) > 100:
        raise ValueError("Drill name must contain 1–100 characters")
    report = {
        "schema_version": 1,
        "id": uuid.uuid4().hex,
        "name": name.strip(),
        "dataset": dataset,
        "started_at": _now(),
        "backend": backend,
        "sample": sample,
        "baseline_created_at": manifest.get("created_at"),
        "baseline_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "snapshot": snapshot.split(":", 1)[0] if backend == "restic" else None,
        "resolved_snapshot": None,
        "rto_seconds": rto_seconds,
        "status": "error",
        "integrity_ok": False,
        "rto_met": False,
        "restore_seconds": 0.0,
        "verify_seconds": 0.0,
        "comparison": None,
        "error": None,
    }
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="recovery-drill-") as directory:
            target = Path(directory)
            restore_snapshot = snapshot
            restore_timeout = timeout
            if backend == "restic":
                selector, separator, subfolder = snapshot.partition(":")
                deadline = time.perf_counter() + timeout
                resolved = _resolve_restic_snapshot(backup, selector, timeout)
                report["resolved_snapshot"] = resolved
                restore_snapshot = resolved + (separator + subfolder if separator else "")
                restore_timeout = deadline - time.perf_counter()
                if restore_timeout <= 0:
                    raise subprocess.TimeoutExpired("restic", timeout)
            _restore(backend, backup, target, restore_snapshot, restore_timeout)
            report["restore_seconds"] = time.perf_counter() - started
            verify_start = time.perf_counter()
            report["comparison"] = compare(manifest, target)
            report["verify_seconds"] = time.perf_counter() - verify_start
            result = report["comparison"]
            report["integrity_ok"] = not any(
                result[key] for key in ("changed", "missing", "unexpected")
            )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Do not persist stderr, credentials, source paths, or file contents.
        report["error"] = f"Restore or verification failed ({type(error).__name__}). See runbook."
    report["total_seconds"] = time.perf_counter() - started
    if report["error"] is None:
        report["rto_met"] = report["total_seconds"] <= rto_seconds
        report["status"] = "passed" if report["integrity_ok"] and report["rto_met"] else "failed"
    report["finished_at"] = _now()
    store.add(report)
    return report
