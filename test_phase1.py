import urllib.request
import json
import uuid

BASE_URL = "http://127.0.0.1:8000/api/v1"

def api(method, path, body=None, token=None):
    req = urllib.request.Request(f"{BASE_URL}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body else None
    try:
        with urllib.request.urlopen(req, data=data) as resp:
            content = resp.read().decode()
            return resp.status, json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        content = e.read().decode()
        return e.code, json.loads(content) if content else {}

# 1. Register test user
test_email = f"test_{uuid.uuid4().hex[:6]}@example.com"
test_password = "Password123!"
reg_status, reg_data = api("POST", "/auth/register", {
    "email": test_email,
    "password": test_password,
    "full_name": "Test User",
    "workspace_name": "Test Engineering Workspace"
})
print(f"Register status: {reg_status}, user_id: {reg_data.get('user', {}).get('id')}")
token = reg_data.get("access_token")
assert reg_status == 201, f"Register failed: {reg_data}"

# 2. Login test
login_status, login_data = api("POST", "/auth/login", {
    "email": test_email,
    "password": test_password
})
print(f"Login status: {login_status}, has_token: {'access_token' in login_data}")
assert login_status == 200

# 3. Check /auth/me
me_status, me_data = api("GET", "/auth/me", token=token)
print(f"Me status: {me_status}, email: {me_data.get('email')}")
assert me_status == 200

# 4. List workspaces
ws_status, ws_list = api("GET", "/workspaces", token=token)
print(f"Workspaces status: {ws_status}, count: {len(ws_list)}, name: {ws_list[0]['name'] if ws_list else None}")
assert ws_status == 200 and len(ws_list) > 0
workspace_id = ws_list[0]["id"]

# 5. Create a dedicated workspace
new_ws_status, new_ws = api("POST", "/workspaces", {"name": "Sprint Planning Workspace"}, token=token)
print(f"Create workspace status: {new_ws_status}, id: {new_ws.get('id')}")
assert new_ws_status == 201
active_ws_id = new_ws["id"]

# 6. Create meeting
meeting_status, meeting_data = api("POST", f"/workspaces/{active_ws_id}/meetings", {
    "title": "Q4 Sprint Sync"
}, token=token)
print(f"Create meeting status: {meeting_status}, code: {meeting_data.get('code')}, id: {meeting_data.get('id')}")
assert meeting_status == 201
meeting_id = meeting_data["id"]
meeting_code = meeting_data["code"]

# 7. Test unauthorized access (Phase 1 step 7)
# Unauthenticated request
unauth_status, unauth_data = api("GET", f"/meetings/{meeting_id}")
print(f"Unauthenticated meeting access status: {unauth_status} (Expected 401)")
assert unauth_status == 401

# Another user with no access to this workspace or meeting
other_email = f"other_{uuid.uuid4().hex[:6]}@example.com"
_, other_reg = api("POST", "/auth/register", {
    "email": other_email,
    "password": "Password123!",
    "full_name": "Other User"
})
other_token = other_reg["access_token"]
other_access_status, other_access_data = api("GET", f"/meetings/{meeting_id}", token=other_token)
print(f"Other user meeting access status: {other_access_status} (Expected 403: {other_access_data.get('detail')})")
assert other_access_status == 403

# Now let other user join with the meeting code
join_status, join_data = api("POST", "/meetings/join", {"code": meeting_code}, token=other_token)
print(f"Join with code status: {join_status}, code: {join_data.get('code')}")
assert join_status == 200

# After joining with code, other user can now view the meeting
allowed_status, allowed_data = api("GET", f"/meetings/{meeting_id}", token=other_token)
print(f"Joined user meeting access status: {allowed_status}")
assert allowed_status == 200

print("PHASE 1 API TESTS PASSED SUCCESSFULLY!")
