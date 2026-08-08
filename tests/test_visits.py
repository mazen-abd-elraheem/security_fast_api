"""
SecureTrack — Visit (Geofence Check-in) Tests
The most critical tests in the system.
"""
from tests.conftest import _auth_header


class TestCheckIn:
    def test_check_in_inside_geofence(self, client, supervisor_user, supervisor_headers, test_site, test_route):
        """Supervisor checks in within 100m of site center → 201."""
        resp = client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": test_site.site_id,
            "latitude": 30.0444,   # Same as site center
            "longitude": 31.2357,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_verified"] is True
        assert data["site_id"] == test_site.site_id
        assert data["distance_from_site"] is not None

    def test_check_in_outside_geofence(self, client, supervisor_user, supervisor_headers, test_site):
        """Supervisor checks in 500m from site → 403 geofence violation."""
        resp = client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": test_site.site_id,
            "latitude": 30.0490,   # ~511m north
            "longitude": 31.2357,
        })
        assert resp.status_code == 403
        assert "Geofence violation" in resp.json()["detail"]

    def test_check_in_with_photo(self, client, supervisor_user, supervisor_headers, test_site, test_route):
        """Check-in with photo URL."""
        resp = client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": test_site.site_id,
            "latitude": 30.0444, "longitude": 31.2357,
            "photo_url": "/static/uploads/checkin_123.jpg",
        })
        assert resp.status_code == 201
        assert resp.json()["photo_url"] == "/static/uploads/checkin_123.jpg"

    def test_guard_cannot_check_in(self, client, guard_user, guard_headers, test_site):
        """Guards are not supervisors and cannot check in."""
        resp = client.post("/api/v1/visits/check-in", headers=guard_headers, json={
            "site_id": test_site.site_id,
            "latitude": 30.0444, "longitude": 31.2357,
        })
        assert resp.status_code == 403

    def test_check_in_invalid_site(self, client, supervisor_user, supervisor_headers):
        """Non-existent site → 404."""
        resp = client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": "nonexistent-id",
            "latitude": 30.0444, "longitude": 31.2357,
        })
        assert resp.status_code == 404


class TestCheckOut:
    def test_checkout_flow(self, client, supervisor_user, supervisor_headers, test_site, test_route):
        """Full check-in → check-out flow."""
        # Check in
        checkin_resp = client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": test_site.site_id,
            "latitude": 30.0444, "longitude": 31.2357,
        })
        assert checkin_resp.status_code == 201
        visit_id = checkin_resp.json()["visit_id"]

        # Check out
        checkout_resp = client.post(f"/api/v1/visits/{visit_id}/check-out", headers=supervisor_headers, json={
            "latitude": 30.0444, "longitude": 31.2357,
        })
        assert checkout_resp.status_code == 200
        assert checkout_resp.json()["check_out_time"] is not None

    def test_double_checkout_rejected(self, client, supervisor_user, supervisor_headers, test_site, test_route):
        """Cannot check out twice from the same visit."""
        checkin = client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": test_site.site_id,
            "latitude": 30.0444, "longitude": 31.2357,
        })
        visit_id = checkin.json()["visit_id"]

        # First checkout
        client.post(f"/api/v1/visits/{visit_id}/check-out", headers=supervisor_headers, json={
            "latitude": 30.0444, "longitude": 31.2357,
        })
        # Second checkout
        resp = client.post(f"/api/v1/visits/{visit_id}/check-out", headers=supervisor_headers, json={
            "latitude": 30.0444, "longitude": 31.2357,
        })
        assert resp.status_code == 400


class TestMyVisits:
    def test_get_my_visits(self, client, supervisor_user, supervisor_headers, test_site, test_route):
        """Supervisor can see their visits for today."""
        # Create a visit first
        client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": test_site.site_id,
            "latitude": 30.0444, "longitude": 31.2357,
        })
        resp = client.get("/api/v1/visits/my", headers=supervisor_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1
