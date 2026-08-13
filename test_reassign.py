"""
Test: Assign guard → Delete assignment → Re-assign same guard
Tests against the production Railway API.
"""
import requests
import sys

BASE = "https://securityfastapi-production.up.railway.app/api/v1"

# 1. Login as admin
print("=" * 60)
print("STEP 1: Login as admin")
resp = requests.post(f"{BASE}/auth/login", data={"username": "admin@securetrack.com", "password": "admin123"})
if resp.status_code != 200:
    print(f"  Login failed ({resp.status_code}): {resp.text}")
    # Try with capital A
    resp = requests.post(f"{BASE}/auth/login", data={"username": "admin@securetrack.com", "password": "Admin123"})
    if resp.status_code != 200:
        resp = requests.post(f"{BASE}/auth/login", data={"username": "admin@securetrack.com", "password": "Admin@123"})
        if resp.status_code != 200:
            print(f"  All login attempts failed ({resp.status_code}): {resp.text}")
            sys.exit(1)

token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
print(f"  ✅ Logged in. Token: {token[:20]}...")

# 2. Get sites
print("\nSTEP 2: Get sites")
resp = requests.get(f"{BASE}/sites", headers=headers)
sites = resp.json()
if isinstance(sites, dict) and "sites" in sites:
    sites = sites["sites"]
print(f"  Found {len(sites)} site(s)")
if not sites:
    print("  ❌ No sites found")
    sys.exit(1)
site_id = sites[0]["site_id"]
site_name = sites[0].get("name", "Unknown")
print(f"  Using site: {site_name} ({site_id})")

# 3. Get shifts for the site
print("\nSTEP 3: Get shifts")
resp = requests.get(f"{BASE}/sites/{site_id}/shifts", headers=headers)
shifts_data = resp.json()
if isinstance(shifts_data, dict) and "shifts" in shifts_data:
    shifts = shifts_data["shifts"]
else:
    shifts = shifts_data if isinstance(shifts_data, list) else []
print(f"  Found {len(shifts)} shift(s)")
if not shifts:
    print("  ❌ No shifts found")
    sys.exit(1)
shift_id = shifts[0]["shift_id"]
shift_label = shifts[0].get("label", "Unknown")
print(f"  Using shift: {shift_label} ({shift_id})")

# 4. Get guards
print("\nSTEP 4: Get guards")
resp = requests.get(f"{BASE}/users", headers=headers, params={"role": "guard"})
users_data = resp.json()
if isinstance(users_data, dict) and "users" in users_data:
    guards = users_data["users"]
else:
    guards = users_data if isinstance(users_data, list) else []
print(f"  Found {len(guards)} guard(s)")
if not guards:
    print("  ❌ No guards found")
    sys.exit(1)
guard_id = guards[0]["user_id"]
guard_name = guards[0].get("name", "Unknown")
print(f"  Using guard: {guard_name} ({guard_id})")

test_date = "2026-08-10"

# 5. ASSIGN guard
print(f"\nSTEP 5: Assign guard to shift on {test_date}")
resp = requests.post(f"{BASE}/roster", headers=headers, json={
    "guard_id": guard_id,
    "shift_id": shift_id,
    "assigned_date": test_date,
})
print(f"  Status: {resp.status_code}")
print(f"  Response: {resp.json()}")
if resp.status_code == 201:
    roster_id = resp.json().get("roster_id")
    print(f"  ✅ Assigned! roster_id: {roster_id}")
else:
    print(f"  ❌ Assignment failed")
    sys.exit(1)

# 6. DELETE (cancel) assignment
print(f"\nSTEP 6: Delete (cancel) assignment {roster_id}")
resp = requests.delete(f"{BASE}/roster/{roster_id}", headers=headers)
print(f"  Status: {resp.status_code}")
print(f"  Response: {resp.json()}")
if resp.status_code == 200:
    print(f"  ✅ Deleted!")
else:
    print(f"  ❌ Delete failed")
    sys.exit(1)

# 7. RE-ASSIGN same guard (this was the bug)
print(f"\nSTEP 7: RE-ASSIGN same guard to same shift on {test_date}")
resp = requests.post(f"{BASE}/roster", headers=headers, json={
    "guard_id": guard_id,
    "shift_id": shift_id,
    "assigned_date": test_date,
})
print(f"  Status: {resp.status_code}")
print(f"  Response: {resp.json()}")
if resp.status_code == 201:
    new_roster_id = resp.json().get("roster_id")
    print(f"  ✅ RE-ASSIGNED! new roster_id: {new_roster_id}")
    print(f"\n{'=' * 60}")
    print(f"  🎉 TEST PASSED: Guard re-assignment works correctly!")
    print(f"{'=' * 60}")
else:
    print(f"  ❌ RE-ASSIGNMENT FAILED — this is the bug!")
    print(f"\n{'=' * 60}")
    print(f"  💥 TEST FAILED")
    print(f"{'=' * 60}")

# 8. Cleanup — delete the re-assigned entry
if resp.status_code == 201:
    print(f"\nSTEP 8: Cleanup — deleting test assignment")
    cleanup = requests.delete(f"{BASE}/roster/{new_roster_id}", headers=headers)
    print(f"  Cleanup: {cleanup.status_code}")
