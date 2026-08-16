#!/usr/bin/env python3
"""Retry one stubborn publication PDF with .part resume across attempts."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT)) if False else None
import os  # noqa: E402

import psycopg  # noqa: E402

from scripts.download_bps_publications import TARGET, download, read_api_config, update_status  # noqa: E402
from workers.ingestion.bps_publications import publication_path  # noqa: E402
from workers.ingestion.bps_storage import load_postgres_dsn  # noqa: E402

POSTGRES_ENV = ROOT / ".config" / "marawa-ai" / "postgres.env"
if not POSTGRES_ENV.exists():
    POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else "915bdfbaedf526005eb11572"
    attempts = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    dsn = load_postgres_dsn(POSTGRES_ENV)
    proxy_url = read_api_config().get("BPS_HTTP_PROXY")
    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            "SELECT domain, pdf_url FROM bps_publications WHERE publication_id=%s", (pid,)
        ).fetchone()
        if row is None:
            print("publication not found:", pid)
            return 1
        domain, url = row[0], row[1]
        target = publication_path(TARGET, domain, pid)
        for attempt in range(1, attempts + 1):
            try:
                digest, size, content_type = download(url, target, proxy_url=proxy_url)
            except Exception as error:  # noqa: BLE001
                print(f"attempt {attempt}/{attempts} FAIL: {str(error)[:100]}")
                continue
            update_status(connection, {
                "domain": domain, "publication_id": pid, "source_url": url,
                "local_path": str(target), "sha256": digest, "bytes": size,
                "content_type": content_type, "download_status": "downloaded", "last_error": None,
            })
            connection.commit()
            print(f"OK attempt {attempt}: {size} bytes {digest[:20]}")
            return 0
    print("GIVE UP")
    return 2


if __name__ == "__main__":
    import os  # noqa: F811
    raise SystemExit(main())
