"""Contract tests untuk agent runtime v1.

Agent runtime adalah konsumen `agent_run_active`: poll percakapan yang
menandakan run, susun konteks, panggil LLM, enqueue balasan bot, dan
tuntaskan run secara atomik. Tidak pernah menulis angka statistik tanpa
evidence — fallback harus aman (invariant #3 AGENTS.md).
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from scripts.app import Store as InMemoryStore
from scripts.conversation_state import ConversationState, State, Event, apply
from scripts.app import GlobalBotSwitch
from scripts.agent_runtime import (
    AgentRuntime,
    StaticLLM,
    system_prompt,
    build_context,
    COMPLETE_SENTENCE_RE,
)


def _conv(cid: str = "628111@s.whatsapp.net", state: State = State.BOT_ACTIVE) -> ConversationState:
    c = ConversationState(conversation_id=cid)
    # bypass __post_init__ guards for test fixtures
    object.__setattr__(c, "state", state)
    return c


def _store_with_pending(cid: str) -> InMemoryStore:
    store = InMemoryStore()
    store.get_conversation(cid)
    store.conversations[cid] = replace(
        store.conversations[cid], agent_run_active=True, state_version=3,
    )
    store.append_message(cid, "in", "user", "ping", wa_message_id=f"w-{uuid.uuid4()}")
    return store


def test_system_prompt_forbids_fabricated_numbers() -> None:
    prompt = system_prompt()
    assert "BPS" in prompt
    # invariant #3: angka statistik tanpa evidence dilarang keras
    assert "evidence" in prompt.lower()
    assert "jangan mengarang" in prompt.lower() or "dilarang" in prompt.lower()


def test_build_context_orders_messages_and_caps_length() -> None:
    store = _store_with_pending("628111@s.whatsapp.net")
    store.append_message("628111@s.whatsapp.net", "out", "bot", "halo")
    ctx = build_context(store, "628111@s.whatsapp.net", limit=2)
    assert len(ctx) <= 3  # system + max 2
    assert ctx[0]["role"] == "system"
    assert ctx[-1]["content"] == "ping"


def test_agent_runtime_happy_path_enqueues_bot_reply_and_completes_run() -> None:
    store = _store_with_pending("628111@s.whatsapp.net")
    runtime = AgentRuntime(store=store, llm=StaticLLM("Halo! Ada yang bisa dibantu?"))
    processed = runtime.process_pending(limit=5)
    assert processed == 1
    outbox = [r for r in store.outbox.values() if r.conversation_id == "628111@s.whatsapp.net"]
    assert len(outbox) == 1
    assert outbox[0].sender_type == "bot"
    assert outbox[0].body == "Halo! Ada yang bisa dibantu?"
    conv = store.get_conversation("628111@s.whatsapp.net")
    assert conv.agent_run_active is False

def test_agent_runtime_llm_failure_sends_safe_fallback_and_completes_run() -> None:
    store = _store_with_pending("628111@s.whatsapp.net")
    runtime = AgentRuntime(store=store, llm=StaticLLM(None))  # None = gagal
    processed = runtime.process_pending(limit=5)
    assert processed == 1
    outbox = [r for r in store.outbox.values() if r.conversation_id == "628111@s.whatsapp.net"]
    assert outbox[0].sender_type == "bot"
    assert "petugas" in outbox[0].body.lower()
    assert conv_agent_done(store, "628111@s.whatsapp.net")


def conv_agent_done(store: Store, cid: str) -> bool:
    return store.get_conversation(cid).agent_run_active is False


def test_agent_runtime_skips_when_bot_globally_disabled() -> None:
    store = _store_with_pending("628111@s.whatsapp.net")
    store.global_switch = GlobalBotSwitch(enabled=False)
    runtime = AgentRuntime(store=store, llm=StaticLLM("x"))
    assert runtime.process_pending(limit=5) == 0
    assert not [r for r in store.outbox.values() if r.conversation_id == "628111@s.whatsapp.net"]


def test_agent_runtime_respects_handover_admin_active() -> None:
    store = InMemoryStore()
    cid = "628111@s.whatsapp.net"
    store.get_conversation(cid)
    store.conversations[cid] = replace(
        store.conversations[cid], agent_run_active=True, state=State.ADMIN_ACTIVE,
    )
    runtime = AgentRuntime(store=store, llm=StaticLLM("x"))
    assert runtime.process_pending(limit=5) == 0
    assert not [r for r in store.outbox.values() if r.conversation_id == cid]


def test_agent_runtime_never_replies_twice_for_same_run() -> None:
    store = _store_with_pending("628111@s.whatsapp.net")
    runtime = AgentRuntime(store=store, llm=StaticLLM("Jawab"))
    assert runtime.process_pending(limit=5) == 1
    # run sudah selesai; tidak ada yang diproses lagi
    assert runtime.process_pending(limit=5) == 0


def test_complete_sentence_filter_strips_fragment_noise() -> None:
    from scripts.agent_runtime import _valid_reply
    assert _valid_reply("Halo, selamat datang.")
    assert _valid_reply("Apa kabar?")
    assert _valid_reply("Baris pertama.\nBaris kedua!")
    assert not _valid_reply("halo tanpa tanda baca")
    assert not _valid_reply("")


def test_agent_runtime_persists_bot_reply_to_transcript() -> None:
    store = _store_with_pending("628111@s.whatsapp.net")
    runtime = AgentRuntime(store=store, llm=StaticLLM("Halo! Ada yang bisa dibantu?"))
    runtime.process_pending(limit=5)
    msgs = store.messages(cid="628111@s.whatsapp.net") if callable(getattr(store, "messages", None)) else store.messages.get("628111@s.whatsapp.net", [])
    out_bodies = [m["body"] for m in msgs if m.get("direction") == "out"]
    assert "Halo! Ada yang bisa dibantu?" in out_bodies
