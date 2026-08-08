"""
SecureTrack — Shift Tests
"""
from tests.conftest import _auth_header


class TestShiftCRUD:
    def test_create_shift(self, client, admin_user, admin_headers, test_site):
        resp = client.post(f"/api/v1/sites/{test_site.site_id}/shifts", headers=admin_headers, json={
            "site_id": test_site.site_id,
            "start_time": "08:00:00", "end_time": "16:00:00",
            "required_headcount": 3, "label": "Day Shift",
        })
        assert resp.status_code == 201
        assert resp.json()["required_headcount"] == 3

    def test_list_shifts_for_site(self, client, admin_user, admin_headers, test_site, test_shift):
        resp = client.get(f"/api/v1/sites/{test_site.site_id}/shifts", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_guard_cannot_create_shift(self, client, guard_user, guard_headers, test_site):
        resp = client.post(f"/api/v1/sites/{test_site.site_id}/shifts", headers=guard_headers, json={
            "site_id": test_site.site_id,
            "start_time": "08:00:00", "end_time": "16:00:00",
        })
        assert resp.status_code == 403
