"""
SecureTrack — Password Reset Tests
"""


class TestPasswordReset:
    def test_request_reset(self, client, guard_user):
        resp = client.post("/api/v1/auth/password/reset-request?email=guard@test.com")
        assert resp.status_code == 200

    def test_request_reset_nonexistent_email(self, client):
        resp = client.post("/api/v1/auth/password/reset-request?email=nobody@test.com")
        # Should always return 200 to prevent email enumeration
        assert resp.status_code == 200

    def test_confirm_reset(self, client, guard_user):
        resp = client.post(
            "/api/v1/auth/password/reset-confirm?email=guard@test.com&new_password=NewPass@1234"
        )
        assert resp.status_code == 200

        # Verify new password works
        login = client.post("/api/v1/auth/login", data={
            "username": "guard@test.com", "password": "NewPass@1234",
        })
        assert login.status_code == 200
