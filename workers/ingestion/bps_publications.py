"""Publication mirror helpers with path and capacity guards."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def publication_path(root: Path, domain: str, publication_id: str) -> Path:
    safe_domain = re.sub(r"[^A-Za-z0-9_-]+", "_", str(domain)).strip("_") or "unknown"
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(publication_id)).strip("_") or "unknown"
    return root / safe_domain / f"{safe_id}.pdf"


def capacity_decision(
    *,
    available_bytes: int,
    remaining_declared_bytes: int,
    reserve_bytes: int,
    unknown_size_count: int = 0,
    assumed_bytes_per_unknown: int = 30 * 1024**2,
) -> dict[str, Any]:
    """Decide whether the local mirror can hold the remaining publications.

    Unknown-size publications are estimated with a conservative per-item bound
    instead of hard-blocking the whole run.
    """
    unknown_estimate = unknown_size_count * assumed_bytes_per_unknown
    shortfall = max(0, remaining_declared_bytes + unknown_estimate + reserve_bytes - available_bytes)
    return {
        "allowed": shortfall == 0,
        "available_bytes": available_bytes,
        "remaining_declared_bytes": remaining_declared_bytes,
        "unknown_estimate_bytes": unknown_estimate,
        "reserve_bytes": reserve_bytes,
        "shortfall_bytes": shortfall,
        "unknown_size_count": unknown_size_count,
    }
