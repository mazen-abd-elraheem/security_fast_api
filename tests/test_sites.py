"""
SecureTrack — Site Management Tests
"""
from tests.conftest import _auth_header


class TestSiteCRUD:
    def test_create_site(self, client, admin_user, admin_headers):
        resp = client.post("/api/v1/sites", headers=admin_headers, json={
            "name": "HQ Building", "address": "456 Main St",
            "latitude": 30.0444, "longitude": 31.2357,
            "radius_meters": 150, "region": "Cairo",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "HQ Building"
        assert data["radius_meters"] == 150
        assert data["region"] == "Cairo"

    def test_list_sites(self, client, admin_user, admin_headers, test_site):
        resp = client.get("/api/v1/sites", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_get_site(self, client, admin_user, admin_headers, test_site):
        resp = client.get(f"/api/v1/sites/{test_site.site_id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Site Alpha"

    def test_update_site(self, client, admin_user, admin_headers, test_site):
        resp = client.put(f"/api/v1/sites/{test_site.site_id}", headers=admin_headers, json={
            "radius_meters": 200,
        })
        assert resp.status_code == 200
        assert resp.json()["radius_meters"] == 200

    def test_delete_site(self, client, admin_user, admin_headers, test_site):
        resp = client.delete(f"/api/v1/sites/{test_site.site_id}", headers=admin_headers)
        assert resp.status_code == 200

    def test_guard_cannot_create_site(self, client, guard_user, guard_headers):
        resp = client.post("/api/v1/sites", headers=guard_headers, json={
            "name": "Hack", "latitude": 0, "longitude": 0,
        })
        assert resp.status_code == 403

    def test_supervisor_can_view_site(self, client, supervisor_user, supervisor_headers, test_site):
        resp = client.get(f"/api/v1/sites/{test_site.site_id}", headers=supervisor_headers)
        assert resp.status_code == 200
