"""
SecureTrack — User Management Tests
"""
from tests.conftest import _auth_header


class TestUserProfile:
    def test_get_my_profile(self, client, supervisor_user, supervisor_headers):
        resp = client.get("/api/v1/users/me", headers=supervisor_headers)
        assert resp.status_code == 200
        assert resp.json()["email"] == "supervisor@test.com"

    def test_update_my_profile(self, client, guard_user, guard_headers):
        resp = client.put("/api/v1/users/me", headers=guard_headers, json={
            "name": "Updated Guard",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Guard"

    def test_update_location_supervisor_only(self, client, supervisor_user, supervisor_headers):
        resp = client.put("/api/v1/users/me/location", headers=supervisor_headers, json={
            "latitude": 30.0444, "longitude": 31.2357,
        })
        assert resp.status_code == 200
        assert resp.json()["latitude"] == 30.0444

    def test_update_location_guard_forbidden(self, client, guard_user, guard_headers):
        resp = client.put("/api/v1/users/me/location", headers=guard_headers, json={
            "latitude": 30.0, "longitude": 31.0,
        })
        assert resp.status_code == 403


class TestAdminUserManagement:
    def test_list_users(self, client, admin_user, admin_headers, guard_user):
        resp = client.get("/api/v1/users", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 2

    def test_list_users_filter_by_role(self, client, admin_user, admin_headers, guard_user):
        resp = client.get("/api/v1/users?role=guard", headers=admin_headers)
        assert resp.status_code == 200
        for u in resp.json()["users"]:
            assert u["role"] == "guard"

    def test_admin_create_user(self, client, admin_user, admin_headers):
        resp = client.post("/api/v1/users", headers=admin_headers, json={
            "name": "New Sup", "email": "newsup2@test.com",
            "password": "Super@1234", "role": "supervisor",
        })
        assert resp.status_code == 201
        assert resp.json()["role"] == "supervisor"

    def test_admin_deactivate_user(self, client, admin_user, admin_headers, guard_user):
        resp = client.delete(f"/api/v1/users/{guard_user.user_id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_guard_cannot_list_users(self, client, guard_user, guard_headers):
        resp = client.get("/api/v1/users", headers=guard_headers)
        assert resp.status_code == 403
