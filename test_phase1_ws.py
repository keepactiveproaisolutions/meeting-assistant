import json
import urllib.request
import websocket

BASE_URL = "http://127.0.0.1:8000/api/v1"
WS_BASE = "ws://127.0.0.1:8000/ws/meetings"

# Register user and create meeting
req = urllib.request.Request(f"{BASE_URL}/auth/register", method="POST")
req.add_header("Content-Type", "application/json")
data = json.dumps({"email": "ws_tester@example.com", "password": "Password123!", "full_name": "WS Tester"}).encode()
try:
    with urllib.request.urlopen(req, data=data) as resp:
        res = json.loads(resp.read().decode())
        token = res["access_token"]
except urllib.error.HTTPError as e:
    # If already registered, login
    req_login = urllib.request.Request(f"{BASE_URL}/auth/login", method="POST")
    req_login.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req_login, data=data) as resp:
        res = json.loads(resp.read().decode())
        token = res["access_token"]

# Get workspace and create meeting
req_ws = urllib.request.Request(f"{BASE_URL}/workspaces", method="GET")
req_ws.add_header("Authorization", f"Bearer {token}")
with urllib.request.urlopen(req_ws) as resp:
    workspaces = json.loads(resp.read().decode())
    ws_id = workspaces[0]["id"]

req_m = urllib.request.Request(f"{BASE_URL}/workspaces/{ws_id}/meetings", method="POST")
req_m.add_header("Authorization", f"Bearer {token}")
req_m.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req_m, data=json.dumps({"title": "WS Test Meeting"}).encode()) as resp:
    meeting = json.loads(resp.read().decode())
    meeting_id = meeting["id"]

print(f"Meeting created for WS test: {meeting_id}")

# 1. Test invalid token
bad_ws_url = f"{WS_BASE}/{meeting_id}?token=invalid_token"
try:
    ws_bad = websocket.create_connection(bad_ws_url)
    print("ERROR: Connection with invalid token should have been closed")
    ws_bad.close()
    assert False, "Unauthorized connection accepted!"
except Exception as e:
    print(f"Invalid token correctly rejected: {e}")

# 2. Test valid token
ws_url = f"{WS_BASE}/{meeting_id}?token={token}"
ws = websocket.create_connection(ws_url, timeout=5)
print("Connected to WebSocket successfully!")

# Receive first message (snapshot)
msg1 = json.loads(ws.recv())
print(f"Received event 1: {msg1.get('type')}")
assert msg1.get("type") == "session.snapshot"
assert msg1.get("data", {}).get("meeting", {}).get("id") == meeting_id

# Receive presence event
msg2 = json.loads(ws.recv())
print(f"Received event 2: {msg2.get('type')}, connections: {msg2.get('data', {}).get('connections')}")
assert msg2.get("type") == "session.presence"

# Send ping
ws.send(json.dumps({"type": "ping"}))
msg3 = json.loads(ws.recv())
print(f"Received ping response: {msg3.get('type')}")
assert msg3.get("type") == "pong"

ws.close()
print("WEBSOCKET AUTH & EVENT TEST PASSED!")
