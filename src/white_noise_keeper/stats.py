from __future__ import annotations

import copy
import datetime

NEST_OUTAGE_REASON = "nest_unavailable"
FAILURE_RETENTION_SECONDS = 7 * 24 * 60 * 60


def build_empty_stats() -> dict:
    return {
        "open_outage": None,
        "failure_records": [],
    }


def normalize_stats(stats: dict | None, now_seconds: float) -> dict:
    normalized = build_empty_stats()
    if not isinstance(stats, dict):
        return normalized

    normalized["open_outage"] = _normalize_open_outage(stats.get("open_outage"))
    normalized["failure_records"] = _prune_failure_records(
        _normalize_failure_records(stats.get("failure_records")),
        now_seconds,
    )
    return normalized


def start_outage(stats: dict, now_seconds: float) -> bool:
    if stats.get("open_outage") is not None:
        return False
    stats["open_outage"] = {
        "started_at": now_seconds,
        "reason": NEST_OUTAGE_REASON,
    }
    return True


def close_outage(stats: dict, now_seconds: float) -> dict | None:
    outage = _normalize_open_outage(stats.get("open_outage"))
    if outage is None:
        stats["open_outage"] = None
        return None

    ended_at = now_seconds
    started_at = outage["started_at"]
    record = {
        "started_at": started_at,
        "ended_at": ended_at,
        "reason": outage["reason"],
        "duration_seconds": max(0.0, ended_at - started_at),
    }
    records = _normalize_failure_records(stats.get("failure_records"))
    records.append(record)
    stats["failure_records"] = _prune_failure_records(records, now_seconds)
    stats["open_outage"] = None
    return record


def snapshot_stats(stats: dict | None, now_seconds: float) -> dict:
    normalized = normalize_stats(stats, now_seconds)
    bucket_start, bucket_end = current_bucket_bounds(now_seconds)
    daily_count, daily_total_seconds = _daily_summary(normalized, bucket_start, bucket_end, now_seconds)
    return {
        "daily": {
            "bucket_start": bucket_start,
            "bucket_end": bucket_end,
            "count": daily_count,
            "total_seconds": daily_total_seconds,
        },
        "open_outage": copy.deepcopy(normalized["open_outage"]),
        "failure_records": copy.deepcopy(normalized["failure_records"]),
    }


def current_bucket_bounds(now_seconds: float) -> tuple[float, float]:
    now = datetime.datetime.fromtimestamp(now_seconds)
    bucket_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now < bucket_start:
        bucket_start -= datetime.timedelta(days=1)
    bucket_end = bucket_start + datetime.timedelta(days=1)
    return bucket_start.timestamp(), bucket_end.timestamp()


def _daily_summary(
    stats: dict,
    bucket_start: float,
    bucket_end: float,
    now_seconds: float,
) -> tuple[int, float]:
    count = 0
    total_seconds = 0.0
    for record in stats["failure_records"]:
        overlap = _interval_overlap(
            record["started_at"],
            record["ended_at"],
            bucket_start,
            bucket_end,
        )
        if overlap > 0.0:
            count += 1
            total_seconds += overlap

    outage = stats.get("open_outage")
    if outage is not None:
        overlap = _interval_overlap(
            outage["started_at"],
            now_seconds,
            bucket_start,
            bucket_end,
        )
        if overlap > 0.0:
            count += 1
            total_seconds += overlap

    return count, total_seconds


def _normalize_open_outage(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    started_at = _coerce_float(value.get("started_at"))
    if started_at is None:
        return None
    return {
        "started_at": started_at,
        "reason": NEST_OUTAGE_REASON,
    }


def _normalize_failure_records(value) -> list[dict]:
    if not isinstance(value, list):
        return []

    records: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        started_at = _coerce_float(item.get("started_at"))
        ended_at = _coerce_float(item.get("ended_at"))
        if started_at is None or ended_at is None:
            continue
        record = {
            "started_at": started_at,
            "ended_at": ended_at,
            "reason": item.get("reason") or NEST_OUTAGE_REASON,
            "duration_seconds": max(
                0.0,
                _coerce_float(item.get("duration_seconds"), max(0.0, ended_at - started_at)),
            ),
        }
        records.append(record)
    records.sort(key=lambda record: (record["ended_at"], record["started_at"]))
    return records


def _prune_failure_records(records: list[dict], now_seconds: float) -> list[dict]:
    cutoff = now_seconds - FAILURE_RETENTION_SECONDS
    return [
        record
        for record in records
        if record["ended_at"] >= cutoff
    ]


def _interval_overlap(
    start_seconds: float,
    end_seconds: float,
    bucket_start: float,
    bucket_end: float,
) -> float:
    overlap = min(end_seconds, bucket_end) - max(start_seconds, bucket_start)
    return max(0.0, overlap)


def _coerce_float(value, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
