#!/usr/bin/env python3
"""Capacity-gated, resumable mirror for BPS publication PDFs."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_crawler import publication_declared_bytes
from workers.ingestion.bps_publications import capacity_decision, publication_path
from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
API_ENV = Path("/home/ubuntu/.config/marawa-ai/webapi.env")
TARGET = ROOT / "data" / "publications"
DEFAULT_RESERVE = 5 * 1024**3


def records(connection: psycopg.Connection[Any]) -> list[tuple[str, str, str, str | None]]:
    return connection.execute(
        """
        SELECT domain, publication_id, pdf_url, declared_size
        FROM bps_publications
        WHERE domain='1306' AND coalesce(pdf_url,'')<>''
        ORDER BY release_date DESC NULLS LAST, publication_id
        """
    ).fetchall()


def update_status(connection: psycopg.Connection[Any], values: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO bps_publication_files
            (domain, publication_id, file_type, source_url, local_path, sha256, bytes,
             content_type, download_status, downloaded_at, last_error)
        VALUES (%(domain)s, %(publication_id)s, 'pdf', %(source_url)s, %(local_path)s,
                %(sha256)s, %(bytes)s, %(content_type)s, %(download_status)s,
                CASE WHEN %(download_status)s='downloaded' THEN now() ELSE NULL END,
                %(last_error)s)
        ON CONFLICT (domain, publication_id, file_type) DO UPDATE SET
            source_url=excluded.source_url, local_path=excluded.local_path,
            sha256=excluded.sha256, bytes=excluded.bytes, content_type=excluded.content_type,
            download_status=excluded.download_status,
            downloaded_at=excluded.downloaded_at, last_error=excluded.last_error
        """,
        values,
    )


def read_api_config() -> dict[str, str]:
    from workers.ingestion.bps_client import secure_read_secret_file

    values: dict[str, str] = {}
    for line in secure_read_secret_file(str(API_ENV)):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def download(
    url: str, target: Path, *, timeout: float = 180, proxy_url: str | None = None
) -> tuple[str, int, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".pdf.part")
    start = temporary.stat().st_size if temporary.exists() else 0
    headers = {
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Connection": "close",
        "User-Agent": "MARAWA-BPS-Publication-Mirror/0.4",
    }
    if start:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers)
    handlers = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urllib.request.build_opener(*handlers)
    with opener.open(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        if start and response.status != 206:
            start = 0
            temporary.unlink(missing_ok=True)
        mode = "ab" if start else "wb"
        with temporary.open(mode) as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    with temporary.open("rb") as handle:
        prefix = handle.read(8)
    if not prefix.startswith(b"%PDF"):
        raise ValueError(f"download is not a PDF (prefix={prefix!r})")
    digest = hashlib.sha256()
    size = 0
    with temporary.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    temporary.replace(target)
    return "sha256:" + digest.hexdigest(), size, content_type


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", help="only compute required capacity")
    parser.add_argument("--reserve-gib", type=float, default=5.0)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    reserve = int(args.reserve_gib * 1024**3)
    dsn = load_postgres_dsn(POSTGRES_ENV)
    proxy_url = read_api_config().get("BPS_HTTP_PROXY")
    with psycopg.connect(dsn) as connection:
        items = records(connection)
        already = {
            row[0]
            for row in connection.execute(
                "SELECT publication_id FROM bps_publication_files WHERE domain='1306' AND file_type='pdf' AND download_status='downloaded'"
            ).fetchall()
        }
        pending = [item for item in items if item[1] not in already]
        parsed_sizes = [publication_declared_bytes(item[3]) for item in pending]
        declared = sum(size for size in parsed_sizes if size is not None)
        unknown_size_count = sum(size is None for size in parsed_sizes)
        available = shutil.disk_usage(TARGET.parent).free
        decision = capacity_decision(
            available_bytes=available,
            remaining_declared_bytes=declared,
            reserve_bytes=reserve,
            unknown_size_count=unknown_size_count,
        )
        print({**decision, "total_publications": len(items), "already_downloaded": len(already), "pending": len(pending)}, flush=True)
        if args.plan:
            return 0
        if not decision["allowed"]:
            print("capacity gate blocked publication download", file=sys.stderr)
            return 3
        selected = pending[: args.limit] if args.limit else pending
        failed = 0
        for index, (domain, publication_id, url, _declared_size) in enumerate(selected, 1):
            target = publication_path(TARGET, domain, publication_id)
            try:
                digest, size, content_type = download(url, target, proxy_url=proxy_url)
                update_status(connection, {
                    "domain": domain, "publication_id": publication_id, "source_url": url,
                    "local_path": str(target.relative_to(ROOT)), "sha256": digest, "bytes": size,
                    "content_type": content_type, "download_status": "downloaded", "last_error": None,
                })
                connection.commit()
            except Exception as error:
                failed += 1
                update_status(connection, {
                    "domain": domain, "publication_id": publication_id, "source_url": url,
                    "local_path": str(target.relative_to(ROOT)), "sha256": None, "bytes": None,
                    "content_type": None, "download_status": "failed",
                    "last_error": f"{type(error).__name__}: {error}"[:1000],
                })
                connection.commit()
            if index % 10 == 0 or index == len(selected):
                print(f"publication files {index}/{len(selected)} failed={failed}", flush=True)
            time.sleep(1.0)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
