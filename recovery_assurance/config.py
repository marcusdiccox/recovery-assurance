"""Read private, named dataset profiles for repeatable CLI drills."""

import json
import math
import re
from pathlib import Path

_SETTINGS = {"manifest", "backup", "backend", "name", "snapshot", "rto_seconds", "timeout"}


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"Dataset {field} must be a nonempty string")
    return value


def _seconds(value, field: str, *, allow_zero: bool) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"Dataset {field} must be a finite number")
    try:
        seconds = float(value)
    except OverflowError:
        raise ValueError(f"Dataset {field} is too large") from None
    if not math.isfinite(seconds) or seconds < 0 or (seconds == 0 and not allow_zero):
        raise ValueError(f"Dataset {field} is outside the supported range")
    return seconds


def _local_path(value: str, base: Path) -> Path:
    path = Path(value)
    if path.drive and not path.is_absolute():
        raise ValueError("Windows drive-relative paths are not supported; use an absolute path")
    # Keep links visible to the drill engine's existing symlink/junction checks.
    return path if path.is_absolute() else base / path


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key")
        result[key] = value
    return result


def load_dataset(config_path: Path, dataset: str) -> dict:
    """Resolve local paths against the config file; leave remote restic URIs untouched."""
    config_path = Path(config_path).resolve()
    if config_path.stat().st_size > 1024 * 1024:
        raise ValueError("Dataset configuration exceeds 1 MiB limit")
    document = json.loads(
        config_path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_keys
    )
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
        or set(document) != {"schema_version", "datasets"}
        or not isinstance(document["datasets"], dict)
    ):
        raise ValueError("Expected schema_version 1 and a datasets object")
    if dataset not in document["datasets"]:
        raise ValueError(f"Unknown dataset: {dataset}")
    settings = document["datasets"][dataset]
    if not isinstance(settings, dict) or set(settings) - _SETTINGS:
        raise ValueError("Unknown or invalid dataset settings")
    backend = settings.get("backend", "directory")
    if backend not in ("directory", "restic"):
        raise ValueError("Dataset backend must be directory or restic")
    backup = _text(settings.get("backup"), "backup")
    remote = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", backup)
    windows_drive = re.match(r"^[A-Za-z]:", backup)
    if backend != "restic" or not remote or windows_drive:
        backup = _local_path(backup, config_path.parent)
    return {
        "manifest": _local_path(_text(settings.get("manifest"), "manifest"), config_path.parent),
        "backup": backup,
        "backend": backend,
        "name": _text(settings.get("name", dataset), "name"),
        "snapshot": _text(settings.get("snapshot", "latest"), "snapshot"),
        "rto_seconds": _seconds(settings.get("rto_seconds", 60), "rto_seconds", allow_zero=True),
        "timeout": _seconds(settings.get("timeout", 600), "timeout", allow_zero=False),
    }
