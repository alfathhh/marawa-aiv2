from __future__ import annotations

from pathlib import Path

from workers.ingestion.bps_publications import capacity_decision, publication_path


def test_publication_path_is_stable_and_cannot_escape_root(tmp_path: Path) -> None:
    path = publication_path(tmp_path, "1306", "../../pub:unsafe")

    assert path.parent == tmp_path / "1306"
    assert path.name == "pub_unsafe.pdf"
    assert path.resolve().is_relative_to(tmp_path.resolve())


def test_capacity_decision_requires_operational_reserve() -> None:
    decision = capacity_decision(
        available_bytes=10 * 1024**3,
        remaining_declared_bytes=8 * 1024**3,
        reserve_bytes=3 * 1024**3,
    )

    assert decision["allowed"] is False
    assert decision["shortfall_bytes"] == 1 * 1024**3


def test_capacity_decision_allows_download_with_headroom() -> None:
    decision = capacity_decision(
        available_bytes=10 * 1024**3,
        remaining_declared_bytes=5 * 1024**3,
        reserve_bytes=3 * 1024**3,
    )

    assert decision["allowed"] is True
    assert decision["shortfall_bytes"] == 0
    assert decision["unknown_estimate_bytes"] == 0


def test_capacity_decision_estimates_unknown_sizes() -> None:
    decision = capacity_decision(
        available_bytes=10 * 1024**3,
        remaining_declared_bytes=1 * 1024**3,
        reserve_bytes=3 * 1024**3,
        unknown_size_count=2,
    )

    # 2 unknowns × 30 MB estimate = 60 MB; total need 4.06 GB < 10 GB → allowed.
    assert decision["allowed"] is True
    assert decision["unknown_estimate_bytes"] == 2 * 30 * 1024**2


def test_capacity_decision_shortfalls_when_unknowns_overflow() -> None:
    decision = capacity_decision(
        available_bytes=2 * 1024**3,
        remaining_declared_bytes=1 * 1024**3,
        reserve_bytes=0,
        unknown_size_count=10000,
    )

    assert decision["allowed"] is False
    assert decision["shortfall_bytes"] > 0
