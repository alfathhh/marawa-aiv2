"""Admin account management contract.

Runs in-memory by default; Postgres persistence is exercised in
`test_dashboard_postgres_e2e.py` when MARAWA_TEST_DSN is set.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from scripts.app import Store, app, get_store
from scripts.password_auth import verify_password


def client_with(store: Store) -> TestClient:
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def test_superadmin_can_create_service_admin() -> None:
    store = Store()
    client = client_with(store)
    try:
        response = client.post(
            "/admin/accounts",
            headers={"X-Admin-Id": "seed-super-1"},
            json={
                "admin_id": "petugas-loket-1",
                "name": "Petugas Loket Satu",
                "role": "admin",
                "password": "aman-sekali-123",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json() == {
            "admin_id": "petugas-loket-1",
            "name": "Petugas Loket Satu",
            "role": "admin",
            "active": True,
        }
        assert verify_password("aman-sekali-123", store.admins["petugas-loket-1"]["password_hash"])
    finally:
        app.dependency_overrides.clear()


def test_superadmin_can_create_another_superadmin_and_list_accounts() -> None:
    store = Store()
    client = client_with(store)
    try:
        created = client.post(
            "/admin/accounts",
            headers={"X-Admin-Id": "seed-super-1"},
            json={
                "admin_id": "super-ops-2",
                "name": "Superadmin Operasional",
                "role": "superadmin",
                "password": "aman-sekali-456",
            },
        )
        assert created.status_code == 201, created.text
        listed = client.get("/admin/accounts", headers={"X-Admin-Id": "seed-super-1"})
        assert listed.status_code == 200
        account = next(row for row in listed.json() if row["admin_id"] == "super-ops-2")
        assert account["role"] == "superadmin"
        assert "password_hash" not in account
    finally:
        app.dependency_overrides.clear()


def test_admin_cannot_manage_accounts() -> None:
    store = Store()
    store.admins["petugas"] = {
        "name": "Petugas", "role": "admin", "password_hash": "unused",
    }
    client = client_with(store)
    try:
        assert client.get("/admin/accounts", headers={"X-Admin-Id": "petugas"}).status_code == 403
        response = client.post(
            "/admin/accounts",
            headers={"X-Admin-Id": "petugas"},
            json={"admin_id": "baru", "name": "Baru", "role": "admin", "password": "12345678"},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_duplicate_username_is_conflict() -> None:
    store = Store()
    client = client_with(store)
    payload = {
        "admin_id": "duplikat", "name": "Nama Pertama",
        "role": "admin", "password": "aman-sekali-123",
    }
    try:
        assert client.post("/admin/accounts", headers={"X-Admin-Id": "seed-super-1"}, json=payload).status_code == 201
        assert client.post("/admin/accounts", headers={"X-Admin-Id": "seed-super-1"}, json=payload).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_account_fields_are_validated_server_side() -> None:
    store = Store()
    client = client_with(store)
    base = {"admin_id": "petugas-1", "name": "Petugas Satu", "role": "admin", "password": "aman-sekali"}
    bad = [
        {**base, "admin_id": "Ada Spasi"},
        {**base, "admin_id": "x"},
        {**base, "name": " "},
        {**base, "role": "operator"},
        {**base, "password": "pendek"},
    ]
    try:
        for payload in bad:
            response = client.post("/admin/accounts", headers={"X-Admin-Id": "seed-super-1"}, json=payload)
            assert response.status_code == 422, (payload, response.text)
    finally:
        app.dependency_overrides.clear()
