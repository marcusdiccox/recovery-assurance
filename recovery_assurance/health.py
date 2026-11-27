"""Read saved evidence for monitoring without running another restore."""

import math
from datetime import UTC, datetime

from .storage import Store


def _recorded_time(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Latest run needs valid timezone-aware timestamps")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Latest run needs valid timezone-aware timestamps") from None
    if timestamp.utcoffset() is None:
        raise ValueError("Latest run needs valid timezone-aware timestamps")
    return timestamp


def check_health(
    store: Store, *, dataset: str, max_age_hours: float, now: datetime | None = None
) -> dict:
    """Assess the latest real run; the caller chooses the freshness policy."""
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError("Maximum evidence age must be finite and positive")
    result = {
        "schema_version": 1,
        "dataset": dataset,
        "healthy": False,
        "reasons": [],
        "max_age_hours": max_age_hours,
        "run_id": None,
        "run_status": None,
        "finished_at": None,
        "age_hours": None,
    }
    report = store.latest_real_run(dataset)
    if report is None:
        result["reasons"].append("no_evidence")
        return result
    if report.get("dataset") != dataset or report.get("sample") is not False:
        raise ValueError("Latest run has inconsistent dataset or sample metadata")
    status = report.get("status")
    if status not in ("passed", "failed", "error"):
        raise ValueError("Latest run has an unsupported outcome")
    if not isinstance(report.get("id"), str) or not report["id"]:
        raise ValueError("Latest run needs a run ID")
    if status == "passed" and (
        report.get("integrity_ok") is not True or report.get("rto_met") is not True
    ):
        raise ValueError("Latest passing run has inconsistent integrity or time evidence")
    started = _recorded_time(report.get("started_at"))
    finished = _recorded_time(report.get("finished_at"))
    now = now if now is not None else datetime.now(UTC)
    if now.utcoffset() is None:
        raise ValueError("Health check needs a timezone-aware clock")
    if finished < started or finished > now:
        raise ValueError("Latest run has reversed or future timestamps")
    age_hours = (now - finished).total_seconds() / 3600
    if age_hours > max_age_hours:
        result["reasons"].append("stale")
    if status != "passed":
        result["reasons"].append(f"latest_run_{status}")
    result.update(
        healthy=not result["reasons"],
        run_id=report["id"],
        run_status=status,
        finished_at=report["finished_at"],
        age_hours=age_hours,
    )
    return result


def check_datasets(store: Store, *, datasets: list[str], max_age_hours: float) -> dict:
    """Assess every explicitly required label; an empty selection cannot pass."""
    if (
        not isinstance(datasets, list)
        or not datasets
        or any(not isinstance(dataset, str) or "\x00" in dataset for dataset in datasets)
    ):
        raise ValueError("Provide at least one valid dataset label")
    results = [
        check_health(store, dataset=dataset, max_age_hours=max_age_hours)
        for dataset in dict.fromkeys(datasets)
    ]
    return {
        "schema_version": 1,
        "healthy": all(result["healthy"] for result in results),
        "max_age_hours": max_age_hours,
        "results": results,
    }
