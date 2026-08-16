from __future__ import annotations

from scripts.check_bps_updates import (
    SENTINEL_LOGICAL_REQUEST_BUDGET,
    SENTINEL_MAX_ATTEMPTS,
    compare_dynamic_page,
    compare_publication_page,
    compare_simdasi_catalog,
    has_changes,
)


def test_sentinel_has_hard_three_call_budget_without_retries() -> None:
    assert SENTINEL_LOGICAL_REQUEST_BUDGET == 3
    assert SENTINEL_MAX_ATTEMPTS == 1


def test_simdasi_detects_new_year_and_latest_update() -> None:
    remote = [
        {"id_tabel": "table-a", "latest_update": "2026-08-01", "ketersediaan_tahun": [2024, 2025]},
        {"id_tabel": "table-b", "latest_update": "2026-08-02", "ketersediaan_tahun": [2025]},
    ]
    local = {
        "table-a": {"latest_update": "2026-07-01", "years": [2024]},
    }

    result = compare_simdasi_catalog(remote, local)

    assert result["new_tables"] == ["table-b"]
    assert result["changed_tables"] == [
        {"table_id": "table-a", "latest_update_changed": True, "new_years": [2025]}
    ]


def test_publication_page_flags_unknown_and_revision_without_listing_all_pages() -> None:
    remote = [
        {"pub_id": "new", "updt_date": "2026-08-10", "rl_date": "2026-08-10"},
        {"pub_id": "old", "updt_date": "2026-08-09", "rl_date": "2026-08-01"},
    ]
    local = {"old": {"updated_date": "2026-08-01"}}

    result = compare_publication_page(remote, local, remote_total=603, local_total=602)

    assert result["total_changed"] is True
    assert result["new_publications"] == ["new"]
    assert result["revised_publications"] == ["old"]


def test_dynamic_page_detects_catalog_metadata_change_only() -> None:
    remote = [
        {"var_id": 1, "title": "New title", "unit": "Jiwa", "sub_id": 2, "vertical": 5},
        {"var_id": 2, "title": "Added", "unit": "Unit", "sub_id": 2, "vertical": 5},
    ]
    local = {"1": {"title": "Old title", "unit": "Jiwa", "subject_id": "2", "vertical_id": "5"}}

    result = compare_dynamic_page(remote, local, remote_total=336, local_total=335)

    assert result["total_changed"] is True
    assert result["new_variables"] == ["2"]
    assert result["changed_variables"] == ["1"]


def test_no_differences_means_no_alert() -> None:
    simdasi = compare_simdasi_catalog(
        [{"id_tabel": "a", "latest_update": "2026", "ketersediaan_tahun": [2025]}],
        {"a": {"latest_update": "2026", "years": [2025]}},
    )
    publication = compare_publication_page(
        [{"pub_id": "p", "updt_date": "2026-01-01"}],
        {"p": {"updated_date": "2026-01-01"}},
        remote_total=1,
        local_total=1,
    )
    dynamic = compare_dynamic_page(
        [{"var_id": 1, "title": "T", "unit": "Jiwa", "sub_id": 2, "vertical": 5}],
        {"1": {"title": "T", "unit": "Jiwa", "subject_id": "2", "vertical_id": "5"}},
        remote_total=1,
        local_total=1,
    )

    assert has_changes(simdasi, dynamic, publication) is False


def test_has_changes_returns_true_for_a_real_signal() -> None:
    assert has_changes(
        {"new_tables": [], "changed_tables": [{"table_id": "x"}]},
        {"new_variables": [], "changed_variables": [], "total_changed": False},
        {"new_publications": [], "revised_publications": [], "total_changed": False},
    ) is True
