from pathlib import Path


DASHBOARD = Path(__file__).parents[1] / "apps" / "dashboard" / "index.html"
WORKER = Path(__file__).parents[1] / "apps" / "whatsapp-worker" / "src" / "worker.js"


def source() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_modern_operate_surface_landmarks() -> None:
    html = source()
    for landmark in (
        'class="app-rail"',
        'class="queue-pane"',
        'id="conversation_view"',
        'id="conversation_empty"',
        'id="queue_search"',
        'data-tab="inbox"',
        'data-tab="settings"',
        'data-tab="audit"',
    ):
        assert landmark in html


def test_dashboard_keeps_all_runtime_api_contracts() -> None:
    html = source()
    for route in (
        "/admin/login",
        "/admin/session",
        "/conversations",
        "/handover/",
        "/messages",
        "/settings/timeouts",
        "/settings/admin-contacts",
        "/settings/whatsapp",
        "/settings/whatsapp-qr.png",
        "/settings/agent",
        "/status/whatsapp",
        "/audit-log",
    ):
        assert route in html
    assert "client_request_id: PENDING_REQUEST_ID" in html
    assert "PENDING_REQUEST_ID = uuid()" in html
    assert "expected_version: VERSION" in html


def test_dashboard_is_self_contained_and_safe_by_default() -> None:
    html = source()
    assert "https://fonts.googleapis.com" not in html
    assert "cdn.jsdelivr" not in html
    assert "tailwindcss.com" not in html
    assert "onclick=" not in html
    assert "sessionStorage" in html
    assert "prefers-reduced-motion" in html
    assert ":focus-visible" in html


def test_dashboard_avoids_known_ai_design_slop() -> None:
    html = source().lower()
    assert "linear-gradient" not in html
    assert "backdrop-filter" not in html
    assert "glass" not in html
    assert "hero" not in html


def test_whatsapp_worker_calls_final_gate_before_send() -> None:
    js = WORKER.read_text(encoding="utf-8")
    gate = js.index("/internal/outbox/authorize")
    send = js.index("sock.sendMessage")
    assert gate < send
    assert "if (!gate.allowed) continue" in js


def test_whatsapp_worker_poll_timer_is_guarded_against_reconnect_stacking() -> None:
    """Reconnect memanggil main() lagi; interval baru wajib replace, bukan nambah.

    Tanpa guard, tiap reconnect (setTimeout(main, 3000)) menumpuk satu
    claimAndSend interval → outbox claim hammering tak terbatas. Terukur live:
    21800 claim per 7 menit (51,9/s) sebelum fix → 293 per 7 menit (0,68/s)
    setelah fix. Guard harus pakai handle module-scope + clearInterval.
    """
    js = WORKER.read_text(encoding="utf-8")
    assert "let pollTimer = null;" in js
    clear = js.index("if (pollTimer) clearInterval(pollTimer);")
    set_ = js.index("pollTimer = setInterval(() => claimAndSend(sock), 5000);")
    assert clear < set_
    assert js.index("setTimeout(main, 3000)") >= 0


def test_dashboard_uses_user_wording_and_has_account_management() -> None:
    html = source()
    assert "warga" not in html.lower()
    assert "/admin/accounts" in html
    assert 'value="admin"' in html
    assert 'value="superadmin"' in html
    assert 'id="account_form"' in html
    assert 'id="accounts_list"' in html
