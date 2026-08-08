"""
SecureTrack — Attendance Tests
"""
from tests.conftest import _auth_header


class TestAttendance:
    def _do_checkin(self, client, supervisor_headers, site):
        resp = client.post("/api/v1/visits/check-in", headers=supervisor_headers, json={
            "site_id": site.site_id,
            "latitude": 30.0444, "longitude": 31.2357,
        })
        return resp.json()["visit_id"]

    def test_record_attendance(self, client, supervisor_user, supervisor_headers,
                                test_site, test_route, test_roster):
        visit_id = self._do_checkin(client, supervisor_headers, test_site)
        resp = client.post(
            f"/api/v1/attendance?visit_id={visit_id}",
            headers=supervisor_headers,
            json={
                "roster_id": test_roster.roster_id,
                "status": "present",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "present"

    def test_bulk_attendance(self, client, supervisor_user, supervisor_headers,
                              test_site, test_route, test_roster):
        visit_id = self._do_checkin(client, supervisor_headers, test_site)
        resp = client.post("/api/v1/attendance/bulk", headers=supervisor_headers, json={
            "visit_id": visit_id,
            "records": [
                {"roster_id": test_roster.roster_id, "status": "present"},
            ],
        })
        assert resp.status_code == 201

    def test_guard_can_view_own_attendance(self, client, guard_user, guard_headers):
        resp = client.get(f"/api/v1/attendance/guard/{guard_user.user_id}", headers=guard_headers)
        assert resp.status_code == 200
