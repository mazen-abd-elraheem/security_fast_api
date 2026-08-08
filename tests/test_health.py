"""
SecureTrack — Health Check Test
"""


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "SecureTrack"
    assert data["status"] == "healthy"
    assert "database" in data
