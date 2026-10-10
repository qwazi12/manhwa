"""Disk volume storage monitoring and overflow protection for Manhwa Recap Studio."""

from __future__ import annotations

import logging
import pathlib
import shutil

try:
    from .ops_config import settings
except (ImportError, ValueError):
    from ops_config import settings

logger = logging.getLogger("manhwa.space")


class DiskFullError(Exception):
    """Raised when disk volume usage exceeds threshold."""


def get_disk_usage(target_path: pathlib.Path | None = None) -> dict:
    target = target_path or settings.data_path
    try:
        total, used, free = shutil.disk_usage(target)
    except Exception:
        total, used, free = (0, 0, 0)
    pct = round((used / total) * 100, 1) if total > 0 else 0.0
    status = "healthy"
    if pct >= settings.max_disk_usage_percent:
        status = "critical"
    elif pct >= settings.warn_disk_usage_percent:
        status = "warning"
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "total_gb": round(total / (1024**3), 2),
        "used_gb": round(used / (1024**3), 2),
        "free_gb": round(free / (1024**3), 2),
        "percent_used": pct,
        "status": status,
        "max_threshold_percent": settings.max_disk_usage_percent,
        "warn_threshold_percent": settings.warn_disk_usage_percent,
        "path": str(target),
    }


def check_disk_space() -> None:
    """Refuse heavy operations if disk space is critical."""
    usage = get_disk_usage()
    if usage["percent_used"] >= settings.max_disk_usage_percent:
        msg = (
            f"Disk space critical: {usage['percent_used']}% used ({usage['used_gb']} GB / {usage['total_gb']} GB). "
            f"Operation blocked until volume usage drops below {settings.max_disk_usage_percent}%."
        )
        logger.error(msg)
        raise DiskFullError(msg)
