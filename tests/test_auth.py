"""
SecureTrack — Auth Tests
Registration, login, and token refresh.
"""
import pytest
from tests.conftest import _auth_header


class TestRegistration:
    def test_register_guard(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "name": "New Guard", "email": "newguard@test.com",
            "password": "Guard@1234", "role": "guard",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "newguard@test.com"
        assert data["role"] == "guard"

    def test_register_supervisor(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "name": "New Supervisor", "email": "newsup@test.com",
            "password": "Super@1234", "role": "supervisor",
        })
        assert resp.status_code == 201
        assert resp.json()["role"] == "supervisor"

    def test_register_duplicate_email(self, client):
        client.post("/api/v1/auth/register", json={
            "name": "User1", "email": "dup@test.com",
            "password": "Pass@1234", "role": "guard",
        })
        resp = client.post("/api/v1/auth/register", json={
            "name": "User2", "email": "dup@test.com",
            "password": "Pass@1234", "role": "guard",
        })
        assert resp.status_code == 409

    def test_register_weak_password(self, client):
        resp = client.post("/api/v1/auth/register", json={
            "name": "Weak", "email": "weak@test.com",
            "password": "nospecial1", "role": "guard",
        })
        assert resp.status_code == 422


class TestLogin:
    def test_login_success(self, client, guard_user):
        resp = client.post("/api/v1/auth/login", data={
            "username": "guard@test.com", "password": "Test@1234",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["role"] == "guard"

    def test_login_wrong_password(self, client, guard_user):
        resp = client.post("/api/v1/auth/login", data={
            "username": "guard@test.com", "password": "WrongPass@1",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_email(self, client):
        resp = client.post("/api/v1/auth/login", data={
            "username": "nobody@test.com", "password": "Test@1234",
        })
        assert resp.status_code == 401


class TestTokenRefresh:
    def test_refresh_token(self, client, admin_user):
        login = client.post("/api/v1/auth/login", data={
            "username": "admin@test.com", "password": "Test@1234",
        })
        refresh = login.json()["refresh_token"]
        resp = client.post(f"/api/v1/auth/refresh?refresh_token={refresh}")
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_invalid_token(self, client):
        resp = client.post("/api/v1/auth/refresh?refresh_token=invalid_token")
        assert resp.status_code == 401
