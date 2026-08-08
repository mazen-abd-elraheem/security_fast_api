"""
SecureTrack — Incident Tests
"""
from tests.conftest import _auth_header


class TestIncidents:
    def test_create_incident(self, client, supervisor_user, supervisor_headers, test_site):
        resp = client.post("/api/v1/incidents", headers=supervisor_headers, json={
            "site_id": test_site.site_id,
            "title": "Broken Gate Lock",
            "description": "The main gate lock is damaged",
            "category": "equipment_damage",
            "severity": "high",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Broken Gate Lock"
        assert data["severity"] == "high"
        assert data["status"] == "open"

    def test_list_incidents(self, client, admin_user, admin_headers, supervisor_user, supervisor_headers, test_site):
        # Create an incident first
        client.post("/api/v1/incidents", headers=supervisor_headers, json={
            "site_id": test_site.site_id, "title": "Test Incident",
            "category": "other", "severity": "low",
        })
        resp = client.get("/api/v1/incidents", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_update_incident(self, client, admin_user, admin_headers, supervisor_user, supervisor_headers, test_site):
        create = client.post("/api/v1/incidents", headers=supervisor_headers, json={
            "site_id": test_site.site_id, "title": "Breach",
            "category": "security_breach", "severity": "critical",
        })
        incident_id = create.json()["incident_id"]

        resp = client.put(f"/api/v1/incidents/{incident_id}", headers=admin_headers, json={
            "status": "resolved",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"
        assert resp.json()["resolved_at"] is not None

    def test_guard_cannot_create_incident(self, client, guard_user, guard_headers, test_site):
        resp = client.post("/api/v1/incidents", headers=guard_headers, json={
            "site_id": test_site.site_id, "title": "Hack",
            "category": "other", "severity": "low",
        })
        assert resp.status_code == 403
