"""
SecureTrack — Admin Tests
"""
from tests.conftest import _auth_header


class TestAdminAuditLogs:
    def test_get_audit_logs(self, client, admin_user, admin_headers):
        resp = client.get("/api/v1/admin/audit-logs", headers=admin_headers)
        assert resp.status_code == 200
        assert "logs" in resp.json()

    def test_guard_cannot_access_audit_logs(self, client, guard_user, guard_headers):
        resp = client.get("/api/v1/admin/audit-logs", headers=guard_headers)
        assert resp.status_code == 403
