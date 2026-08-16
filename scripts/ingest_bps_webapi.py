#!/usr/bin/env python3
"""Ingest scoped BPS WebAPI resources into MARAWA PostgreSQL.

Scope:
- Dynamic data and publication: BPS website domain 1306.
- SIMDASI: seven-digit region 1306000.
- Census: event/topic/dataset catalogue, facts for matching MFD 1306/1306000.
- Glossary: global BPS concepts needed to interpret local data.

Requests are serialized and rate limited. Raw responses are immutable snapshots; normalized
current tables are idempotent upserts. The API key is loaded from a 0600 file and never
persisted in request metadata or logs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_client import ApiResult, BpsApiClient, BpsApiError, load_api_config
from workers.ingestion.bps_crawler import (
    dynamic_period_chunks,
    extract_interop_data,
    iter_available_years,
    select_census_target_areas,
    simdasi_detail_params,
)
from workers.ingestion.bps_storage import BpsStore, load_postgres_dsn
from workers.ingestion.bps_webapi import (
    ApiPayloadError,
    decode_dynamic_facts,
    normalize_census_rows,
    normalize_dynamic_dimensions,
    normalize_glossary_hit,
    normalize_publication,
    normalize_simdasi_detail,
    normalize_simdasi_facts,
    require_text,
    merge_publication_rows,
)

API_ENV = Path("/home/ubuntu/.config/marawa-ai/webapi.env")
POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
DEFAULT_FAMILIES = ["simdasi", "dynamic", "census", "publication", "glossary"]


def log(message: str) -> None:
    print(message, flush=True)


def payload_meta_rows(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], dict):
        raise ApiPayloadError("expected paginated [metadata, rows] response")
    if not isinstance(data[1], list):
        raise ApiPayloadError("expected list rows in paginated response")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(data[1]):
        if not isinstance(row, dict):
            raise ApiPayloadError(f"paginated row {index} is not an object")
        rows.append(row)
    return data[0], rows


class Crawl:
    def __init__(self, *, resume: bool, families: list[str]) -> None:
        api = load_api_config(str(API_ENV))
        self.domain = api["BPS_DOMAIN"]
        self.simdasi_region = api["BPS_SIMDASI_REGION"]
        self.client = BpsApiClient(
            api["BPS_WEBAPI_KEY"],
            proxy_url=api.get("BPS_HTTP_PROXY"),
            timeout=45,
            max_attempts=3,
            min_delay=float(api.get("BPS_MIN_DELAY", "0.5")),
            max_delay=float(api.get("BPS_MAX_DELAY", "1.0")),
            backoff_base=2,
            max_backoff=8,
        )
        self.store = BpsStore(load_postgres_dsn(POSTGRES_ENV))
        self.store.ensure_schema()
        self.resume = resume
        self.families = families
        self.summary: dict[str, Any] = {
            "domain": self.domain,
            "simdasi_region": self.simdasi_region,
            "families": {},
            "errors": [],
            "requests": 0,
            "bytes": 0,
        }
        self.run_id = self.store.start_run(
            "resume" if resume else "full_update",
            {"domain": self.domain, "simdasi_region": self.simdasi_region, "families": families},
        )

    def record(self, family: str, resource: str, result: ApiResult) -> int:
        self.summary["requests"] += 1
        self.summary["bytes"] += result.bytes_received
        return self.store.record_snapshot(
            self.run_id, family, resource, result.request, result.payload
        )

    def error(self, family: str, resource: str, error: Exception) -> None:
        text = f"{type(error).__name__}: {error}"
        self.summary["errors"].append({"family": family, "resource": resource, "error": text})
        log(f"  ERROR {family}/{resource}: {text}")

    def checkpoint(self, family: str, state: dict[str, Any]) -> None:
        self.store.save_checkpoint(f"bps:{family}:{self.domain}:{self.simdasi_region}", state)
        if state.get("done") is True:
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "update_bps_checklist.py")],
                cwd=ROOT, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def previous_checkpoint(self, family: str) -> dict[str, Any] | None:
        if not self.resume:
            return None
        return self.store.load_checkpoint(f"bps:{family}:{self.domain}:{self.simdasi_region}")

    def paginated(
        self,
        *,
        family: str,
        resource: str,
        model: str,
        domain: str | None,
        perpage: int | None = None,
    ) -> list[tuple[list[dict[str, Any]], int]]:
        pages: list[tuple[list[dict[str, Any]], int]] = []
        first = self.client.get_paginated(model, domain=domain, page=1, perpage=perpage)
        snapshot_id = self.record(family, f"{resource}:page:1", first)
        meta, rows = payload_meta_rows(first.payload)
        pages.append((rows, snapshot_id))
        total_pages = int(meta.get("pages") or 1)
        log(f"  {resource}: page 1/{total_pages}, rows={len(rows)}, total={meta.get('total')}")
        for page in range(2, total_pages + 1):
            result = self.client.get_paginated(model, domain=domain, page=page, perpage=perpage)
            sid = self.record(family, f"{resource}:page:{page}", result)
            _, page_rows = payload_meta_rows(result.payload)
            pages.append((page_rows, sid))
            if page % 10 == 0 or page == total_pages:
                log(f"  {resource}: page {page}/{total_pages}")
        return pages

    def crawl_simdasi(self) -> None:
        family = "simdasi"
        stats = {"regions": 0, "subjects": 0, "tables": 0, "details": 0, "columns": 0, "facts": 0, "failed_details": 0}
        previous = self.previous_checkpoint(family) or {}
        completed = set(previous.get("completed_details", []))
        log("[SIMDASI] MFD, subject, table, detail per year")

        for service_id, level, params, parent in [
            (26, "province", [], None),
            (27, "regency", [("parent", "1300000")], "1300000"),
            (28, "district", [("parent", self.simdasi_region)], self.simdasi_region),
        ]:
            try:
                result = self.client.get_interop("simdasi", service_id, params)
                sid = self.record(family, f"mfd:{level}", result)
                envelope, rows = extract_interop_data(result.payload)
                stats["regions"] += self.store.upsert_simdasi_regions(
                    rows, sid, level=level, parent_code=parent, version=envelope.get("versi")
                )
            except Exception as error:
                self.error(family, f"mfd:{level}", error)

        result = self.client.get_interop("simdasi", 22, [("wilayah", self.simdasi_region)])
        sid = self.record(family, "subjects", result)
        _, subjects = extract_interop_data(result.payload)
        stats["subjects"] = self.store.upsert_simdasi_subjects(self.simdasi_region, subjects, sid)

        result = self.client.get_interop("simdasi", 23, [("wilayah", self.simdasi_region)])
        sid = self.record(family, "tables", result)
        _, tables = extract_interop_data(result.payload)
        stats["tables"] = self.store.upsert_simdasi_tables(self.simdasi_region, tables, sid)
        total_details = sum(len(iter_available_years(table)) for table in tables)
        log(f"  SIMDASI catalogue: subjects={len(subjects)}, tables={len(tables)}, table-years={total_details}")

        position = 0
        for table in tables:
            try:
                table_id = require_text(table, "id_tabel")
            except ApiPayloadError as error:
                stats["failed_details"] += 1
                self.error(family, "table:id_tabel", error)
                continue
            for year in iter_available_years(table):
                position += 1
                detail_key = f"{table_id}:{year}"
                if detail_key in completed:
                    continue
                try:
                    detail_result = self.client.get_interop(
                        "simdasi", 25, simdasi_detail_params(self.simdasi_region, year, table_id),
                    )
                    detail_sid = self.record(family, f"detail:{table_id}:{year}", detail_result)
                    service = detail_result.payload["data"][1]
                    if not isinstance(service, dict):
                        raise ApiPayloadError("SIMDASI detail service payload is not an object")
                    normalized = normalize_simdasi_detail(
                        self.simdasi_region, table_id, year, service
                    )
                    self.store.upsert_simdasi_details([normalized], detail_sid)
                    columns, facts = normalize_simdasi_facts(self.simdasi_region, table_id, year, service)
                    stats["columns"] += self.store.upsert_simdasi_columns(columns, detail_sid)
                    stats["facts"] += self.store.upsert_simdasi_facts(facts, detail_sid)
                    stats["details"] += 1
                    completed.add(detail_key)
                    self.checkpoint(family, {"completed_details": sorted(completed), "done": False})
                except Exception as error:
                    stats["failed_details"] += 1
                    self.error(family, f"detail:{table_id}:{year}", error)
                if position % 10 == 0 or position == total_details:
                    log(f"  SIMDASI detail {position}/{total_details}: ok={stats['details']} fail={stats['failed_details']}")
        self.checkpoint(family, {"completed_details": sorted(completed), "done": stats["failed_details"] == 0})
        self.summary["families"][family] = stats

    def crawl_dynamic(self) -> None:
        family = "dynamic"
        stats = {"subjects": 0, "variables": 0, "periods": 0, "data_requests": 0, "dimensions": 0, "facts": 0, "null_data_chunks": 0, "failed_variables": 0}
        previous = self.previous_checkpoint(family) or {}
        completed = set(previous.get("completed_variables", []))
        log("[DYNAMIC] subject, variables, and all dimensioned facts")

        for rows, sid in self.paginated(family=family, resource="subjects", model="subject", domain=self.domain):
            stats["subjects"] += self.store.upsert_dynamic_subjects(self.domain, rows, sid)
        variables: list[dict[str, Any]] = []
        for rows, sid in self.paginated(family=family, resource="variables", model="var", domain=self.domain):
            stats["variables"] += self.store.upsert_dynamic_variables(self.domain, rows, sid)
            variables.extend(rows)
        log(f"  dynamic catalogue: variables={len(variables)}")

        for index, variable in enumerate(variables, 1):
            try:
                var_id = require_text(variable, "var_id")
            except ApiPayloadError as error:
                stats["failed_variables"] += 1
                self.error(family, f"variable:{index}", error)
                continue
            if var_id in completed:
                continue
            try:
                # `th` must be scoped to a variable and paged before data chunks are requested.
                period_rows: list[dict[str, Any]] = []
                first = self.client.get_paginated("th", domain=self.domain, page=1, extra=[("var", var_id)])
                self.record(family, f"periods:var:{var_id}:page:1", first)
                meta, first_rows = payload_meta_rows(first.payload)
                period_rows.extend(first_rows)
                for page in range(2, int(meta.get("pages") or 1) + 1):
                    page_result = self.client.get_paginated("th", domain=self.domain, page=page, extra=[("var", var_id)])
                    self.record(family, f"periods:var:{var_id}:page:{page}", page_result)
                    _, page_rows = payload_meta_rows(page_result.payload)
                    period_rows.extend(page_rows)
                stats["periods"] += len(period_rows)
                chunks = dynamic_period_chunks(period_rows)
                if not chunks:
                    raise ApiPayloadError(f"dynamic variable {var_id} has no period IDs")
                for chunk_index, period_ids in enumerate(chunks, 1):
                    period_parameter = ";".join(period_ids)
                    result = self.client.get_paginated(
                        "data", domain=self.domain, page=1,
                        extra=[("var", var_id), ("th", period_parameter)],
                    )
                    sid = self.record(
                        family, f"data:var:{var_id}:periods:{period_parameter}", result
                    )
                    stats["data_requests"] += 1
                    if result.payload is None:
                        stats["null_data_chunks"] += 1
                        continue
                    dimensions = normalize_dynamic_dimensions(self.domain, var_id, result.payload)
                    stats["dimensions"] += self.store.upsert_dynamic_dimensions(dimensions, sid)
                    facts = decode_dynamic_facts(self.domain, result.payload, expected_var_id=var_id)
                    stats["facts"] += self.store.upsert_dynamic_facts(facts, sid)
                completed.add(var_id)
                self.checkpoint(family, {"completed_variables": sorted(completed), "done": False})
            except Exception as error:
                stats["failed_variables"] += 1
                self.error(family, f"data:var:{var_id}", error)
            if index % 10 == 0 or index == len(variables):
                log(f"  dynamic data {index}/{len(variables)}: facts={stats['facts']} fail={stats['failed_variables']}")
        self.checkpoint(family, {"completed_variables": sorted(completed), "done": stats["failed_variables"] == 0})
        self.summary["families"][family] = stats

    def crawl_census(self) -> None:
        family = "census"
        stats = {"events": 0, "topics": 0, "areas": 0, "datasets": 0, "facts": 0, "null_area_catalogues": 0, "events_without_target_area": 0, "failed_data": 0}
        previous = self.previous_checkpoint(family) or {}
        completed = set(previous.get("completed_data", []))
        log("[CENSUS] events, topics, areas, datasets, local facts")

        result = self.client.get_interop("sensus", 37, [])
        sid = self.record(family, "events", result)
        _, events = extract_interop_data(result.payload)
        stats["events"] = self.store.upsert_census_events(events, sid)

        for event in events:
            try:
                event_id = require_text(event, "id")
            except ApiPayloadError as error:
                stats["failed_data"] += 1
                self.error(family, "event:id", error)
                continue
            try:
                topic_result = self.client.get_interop("sensus", 38, [("kegiatan", event_id)])
                topic_sid = self.record(family, f"topics:{event_id}", topic_result)
                _, topics = extract_interop_data(topic_result.payload)
                stats["topics"] += self.store.upsert_census_topics(event_id, topics, topic_sid)

                area_result = self.client.get_interop("sensus", 39, [("kegiatan", event_id)])
                area_sid = self.record(family, f"areas:{event_id}", area_result)
                _, areas = extract_interop_data(area_result.payload)
                if area_result.payload.get("data", [None, object()])[1] is None:
                    stats["null_area_catalogues"] += 1
                    log(f"  census {event_id}: upstream returned explicit null area catalogue")
                stats["areas"] += self.store.upsert_census_areas(event_id, areas, area_sid)
                target_areas = select_census_target_areas(areas)
                if not target_areas:
                    stats["events_without_target_area"] += 1
                    log(f"  census {event_id}: no exact Padang Pariaman area (kode_mfd 1306/1306000)")
                log(f"  census {event_id}: topics={len(topics)}, areas={len(areas)}, target_areas={len(target_areas)}")

                for topic in topics:
                    try:
                        topic_id = require_text(topic, "id")
                    except ApiPayloadError as error:
                        stats["failed_data"] += 1
                        self.error(family, f"topic:{event_id}", error)
                        continue
                    dataset_result = self.client.get_interop(
                        "sensus", 40, [("kegiatan", event_id), ("topik", topic_id)]
                    )
                    dataset_sid = self.record(family, f"datasets:{event_id}:{topic_id}", dataset_result)
                    _, datasets = extract_interop_data(dataset_result.payload)
                    stats["datasets"] += self.store.upsert_census_datasets(event_id, topic_id, datasets, dataset_sid)
                    for dataset in datasets:
                        try:
                            dataset_id = require_text(dataset, "id")
                        except ApiPayloadError as error:
                            stats["failed_data"] += 1
                            self.error(family, f"dataset:{event_id}:{topic_id}", error)
                            continue
                        for area in target_areas:
                            try:
                                area_id = require_text(area, "id")
                            except ApiPayloadError as error:
                                stats["failed_data"] += 1
                                self.error(family, f"area:{event_id}", error)
                                continue
                            data_key = f"{event_id}:{dataset_id}:{area_id}"
                            if data_key in completed:
                                continue
                            try:
                                data_result = self.client.get_interop(
                                    "sensus", 41,
                                    [("kegiatan", event_id), ("wilayah_sensus", area_id), ("dataset", dataset_id)],
                                )
                                data_sid = self.record(family, f"data:{data_key}", data_result)
                                service = data_result.payload["data"][1]
                                if not isinstance(service, dict):
                                    raise ApiPayloadError("census data service payload is not an object")
                                facts = normalize_census_rows(event_id, dataset_id, service)
                                stats["facts"] += self.store.upsert_census_facts(facts, data_sid)
                                completed.add(data_key)
                                self.checkpoint(family, {"completed_data": sorted(completed), "done": False})
                            except Exception as error:
                                stats["failed_data"] += 1
                                self.error(family, f"data:{data_key}", error)
            except Exception as error:
                self.error(family, f"event:{event_id}", error)
        self.checkpoint(family, {"completed_data": sorted(completed), "done": stats["failed_data"] == 0})
        self.summary["families"][family] = stats

    def crawl_publication(self) -> None:
        family = "publication"
        stats = {"listed": 0, "detailed": 0, "failed_details": 0}
        previous = self.previous_checkpoint(family) or {}
        completed = set(previous.get("completed_details", []))
        log("[PUBLICATION] catalogue and full metadata detail")
        publications: list[dict[str, Any]] = []
        list_by_id: dict[str, dict[str, Any]] = {}
        for rows, sid in self.paginated(family=family, resource="list", model="publication", domain=self.domain):
            normalized = [normalize_publication(self.domain, row) for row in rows]
            stats["listed"] += self.store.upsert_publications(normalized, sid)
            publications.extend(rows)
            for row in rows:
                try:
                    list_by_id[require_text(row, "pub_id")] = row
                except ApiPayloadError:
                    pass
        for index, publication in enumerate(publications, 1):
            try:
                pub_id = require_text(publication, "pub_id")
            except ApiPayloadError as error:
                stats["failed_details"] += 1
                self.error(family, f"detail:{index}", error)
                continue
            if pub_id in completed:
                continue
            try:
                result = self.client.get_view("publication", domain=self.domain, item_id=pub_id)
                sid = self.record(family, f"detail:{pub_id}", result)
                raw = result.payload.get("data")
                if isinstance(raw, list) and len(raw) > 1 and isinstance(raw[1], list) and raw[1]:
                    raw = raw[1][0]
                if not isinstance(raw, dict):
                    raise ApiPayloadError("publication detail data is not an object")
                # Merge: list-only fields (pdf, size, cover, release dates) must survive a
                # detail response that omits them.
                merged = merge_publication_rows(list_by_id.get(pub_id), raw)
                self.store.upsert_publications([normalize_publication(self.domain, merged)], sid)
                stats["detailed"] += 1
                completed.add(pub_id)
                self.checkpoint(family, {"completed_details": sorted(completed), "done": False})
            except Exception as error:
                stats["failed_details"] += 1
                self.error(family, f"detail:{pub_id}", error)
            if index % 25 == 0 or index == len(publications):
                log(f"  publication detail {index}/{len(publications)}: ok={stats['detailed']} fail={stats['failed_details']}")
        self.checkpoint(family, {"completed_details": sorted(completed), "done": stats["failed_details"] == 0})
        self.summary["families"][family] = stats

    def crawl_glossary(self) -> None:
        family = "glossary"
        stats = {"concepts": 0}
        log("[GLOSSARY] global statistical definitions")
        for rows, sid in self.paginated(family=family, resource="list", model="glosarium", domain=None):
            normalized = [normalize_glossary_hit(row) for row in rows]
            stats["concepts"] += self.store.upsert_glossary(normalized, sid)
        self.checkpoint(family, {"done": True, "concepts": stats["concepts"]})
        self.summary["families"][family] = stats

    def run(self) -> int:
        status = "completed"
        try:
            for family in self.families:
                method = getattr(self, f"crawl_{family}")
                try:
                    method()
                except Exception as error:
                    status = "partial"
                    self.error(family, "family", error)
                    traceback.print_exc()
        except KeyboardInterrupt:
            status = "interrupted"
            self.summary["errors"].append({"family": "runner", "resource": "signal", "error": "KeyboardInterrupt"})
        if self.summary["errors"] and status == "completed":
            status = "partial"
        self.store.finish_run(self.run_id, status, self.summary)
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "update_bps_checklist.py"), "--force"],
            cwd=ROOT, check=False,
        )
        log("SUMMARY " + json.dumps({"run_id": str(self.run_id), "status": status, **self.summary}, ensure_ascii=False))
        return 0 if status == "completed" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--families", default=",".join(DEFAULT_FAMILIES))
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    families = [item.strip() for item in args.families.split(",") if item.strip()]
    unknown = set(families) - set(DEFAULT_FAMILIES)
    if unknown:
        raise SystemExit(f"unknown families: {sorted(unknown)}")
    return Crawl(resume=args.resume, families=families).run()


if __name__ == "__main__":
    raise SystemExit(main())
