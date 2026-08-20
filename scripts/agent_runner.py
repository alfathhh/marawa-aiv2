#!/usr/bin/env python3
"""Daemon agent runner: poll agent_run_active → LLM → outbox.

Dijalankan sebagai systemd unit `marawa-agent`. Loop 3 detik, batch kecil,
fail-safe: error LLM tetap menuntaskan run dengan fallback ke petugas —
percakapan tidak pernah menggantung.
"""
from __future__ import annotations

import logging
import sys
import time

from scripts.agent_runtime import AgentRuntime, OpenAICompatibleLLM
from scripts.app import _build_store, run_sweep_logic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s marawa-agent %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("marawa-agent")


def _build_rag():
    """RAG pipeline terhadap data BPS lokal. Gagal -> None (agent tetap jalan,
    permintaan angka jatuh ke prompt guardrail, bukan crash)."""
    try:
        from scripts.rag_pipeline import RagPipeline
        from scripts.rag_store_pg import (
            PgSelectionStore,
            _dsn,
            csa_label_for_title,
            fetch_indicator_meta,
            list_topics,
            load_offering_index,
            make_offer,
            query_serving,
        )
        index = load_offering_index()
        sel_store = PgSelectionStore(_dsn())
        rag = RagPipeline(
            store=sel_store,
            llm=None,
            querier=query_serving,
            offer=make_offer(),
            offering_index=index,
            meta=fetch_indicator_meta,
            csa_labeler=csa_label_for_title,
            topic_lister=list_topics,
        )
        log.info("rag pipeline aktif: %d family index", len(index.get("by_family", {})))
        return rag
    except Exception:
        log.exception("rag pipeline gagal dibangun — lanjut tanpa RAG")
        return None


def main() -> int:
    store = _build_store()
    llm = OpenAICompatibleLLM()
    if not llm.configured:
        log.error("MARAWA_LLM_BASE_URL/API_KEY/MODEL tidak lengkap — keluar.")
        return 2
    rag = _build_rag()
    runtime = AgentRuntime(store=store, llm=llm, rag=rag)
    log.info("agent runner siap; model=%s rag=%s", llm.model, "on" if rag else "off")
    idle_cycles = 0
    last_sweep = 0.0
    while True:
        try:
            processed = runtime.process_pending(limit=5)
            if processed:
                log.info("memproses %d agent run", processed)
                idle_cycles = 0
            else:
                idle_cycles += 1
                if idle_cycles % 100 == 0:
                    log.info("idle %d siklus", idle_cycles)
            # sweep timeout tiap 60 dtk, in-process (tidak ada caller eksternal)
            if time.monotonic() - last_sweep >= 60:
                result = run_sweep_logic(store)
                if result["planned"]:
                    log.info("sweep: planned=%d applied=%d", result["planned"], len(result["applied"]))
                last_sweep = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("loop error (tetap lanjut)")
            time.sleep(5)
        time.sleep(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
