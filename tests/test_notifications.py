"""
SecureTrack — Notification Tests
"""
from tests.conftest import _auth_header


class TestNotifications:
    def test_get_notifications(self, client, guard_user, guard_headers):
        resp = client.get("/api/v1/notifications", headers=guard_headers)
        assert resp.status_code == 200
        assert "notifications" in resp.json()
        assert "unread_count" in resp.json()

    def test_admin_send_notification(self, client, admin_user, admin_headers, guard_user):
        resp = client.post("/api/v1/notifications/send", headers=admin_headers, json={
            "target_user_id": guard_user.user_id,
            "title": "Schedule Update",
            "message": "Your shift has been changed",
            "notification_type": "schedule_change",
        })
        assert resp.status_code == 201

        # Verify guard receives it
        from tests.conftest import _auth_header
        guard_h = _auth_header(guard_user)
        notifs = client.get("/api/v1/notifications", headers=guard_h)
        assert notifs.json()["total"] >= 1

    def test_mark_as_read(self, client, admin_user, admin_headers, guard_user):
        # Send a notification
        client.post("/api/v1/notifications/send", headers=admin_headers, json={
            "target_user_id": guard_user.user_id,
            "title": "Test", "notification_type": "system",
        })

        guard_h = _auth_header(guard_user)
        notifs = client.get("/api/v1/notifications", headers=guard_h)
        notif_id = notifs.json()["notifications"][0]["notification_id"]

        resp = client.put(f"/api/v1/notifications/{notif_id}/read", headers=guard_h)
        assert resp.status_code == 200

    def test_mark_all_as_read(self, client, admin_user, admin_headers, guard_user):
        guard_h = _auth_header(guard_user)
        resp = client.put("/api/v1/notifications/read-all", headers=guard_h)
        assert resp.status_code == 200
