"""
SecureTrack — Roster Tests
"""
from datetime import date
from tests.conftest import _auth_header


class TestRoster:
    def test_assign_guard(self, client, admin_user, admin_headers, guard_user, test_shift):
        resp = client.post("/api/v1/roster", headers=admin_headers, json={
            "guard_id": guard_user.user_id,
            "shift_id": test_shift.shift_id,
            "assigned_date": date.today().isoformat(),
        })
        assert resp.status_code == 201

    def test_get_roster_for_site(self, client, admin_user, admin_headers, test_site, test_roster):
        resp = client.get(
            f"/api/v1/roster/site/{test_site.site_id}?target_date={date.today().isoformat()}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_guard_can_view_own_schedule(self, client, guard_user, guard_headers, test_roster):
        resp = client.get(f"/api/v1/roster/guard/{guard_user.user_id}", headers=guard_headers)
        assert resp.status_code == 200

    def test_guard_cannot_view_other_schedule(self, client, db, guard_user, guard_headers):
        from tests.conftest import _create_user
        other = _create_user(db, "guard", "Other", "other@test.com", badge="G-002")
        resp = client.get(f"/api/v1/roster/guard/{other.user_id}", headers=guard_headers)
        assert resp.status_code == 403
