"""
SecureTrack — Route Tests
"""
from datetime import date
from tests.conftest import _auth_header


class TestRoutes:
    def test_assign_route(self, client, admin_user, admin_headers, supervisor_user, test_site):
        resp = client.post("/api/v1/routes", headers=admin_headers, json={
            "supervisor_id": supervisor_user.user_id,
            "assigned_date": date.today().isoformat(),
            "sites": [{"site_id": test_site.site_id, "visit_order": 1}],
        })
        assert resp.status_code == 201

    def test_get_my_route(self, client, supervisor_user, supervisor_headers, test_route):
        resp = client.get("/api/v1/routes/my", headers=supervisor_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_sites"] >= 1

    def test_guard_cannot_assign_route(self, client, guard_user, guard_headers, supervisor_user, test_site):
        resp = client.post("/api/v1/routes", headers=guard_headers, json={
            "supervisor_id": supervisor_user.user_id,
            "assigned_date": date.today().isoformat(),
            "sites": [{"site_id": test_site.site_id, "visit_order": 1}],
        })
        assert resp.status_code == 403
