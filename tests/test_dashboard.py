"""
SecureTrack — Dashboard Tests
"""
from tests.conftest import _auth_header


class TestDashboard:
    def test_live_status(self, client, admin_user, admin_headers, test_site):
        resp = client.get("/api/v1/dashboard/live", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sites" in data
        assert "sites" in data

    def test_platform_stats(self, client, admin_user, admin_headers):
        resp = client.get("/api/v1/dashboard/stats", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "total_sites" in data

    def test_supervisor_progress(self, client, admin_user, admin_headers, supervisor_user, test_route):
        resp = client.get(
            f"/api/v1/dashboard/supervisor/{supervisor_user.user_id}/progress",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_assigned"] >= 1

    def test_guard_cannot_access_dashboard(self, client, guard_user, guard_headers):
        resp = client.get("/api/v1/dashboard/live", headers=guard_headers)
        assert resp.status_code == 403
