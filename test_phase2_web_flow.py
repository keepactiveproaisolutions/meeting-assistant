import json
import urllib.request
import websocket
import time

BASE_URL = "http://127.0.0.1:8000/api/v1"
WS_URL = "ws://127.0.0.1:8000/ws/meetings"

def request(method, path, body=None, token=None):
    req = urllib.request.Request(f"{BASE_URL}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", "http://localhost:5173") # Test CORS header!
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body else None
    with urllib.request.urlopen(req, data=data) as resp:
        content = resp.read().decode()
        headers = dict(resp.headers)
        return resp.status, json.loads(content) if content else {}, headers

print("--- Testing CORS and Web App API Integration ---")
# 1. Login with test user
status, login_res, headers = request("POST", "/auth/login", {
    "email": "ws_tester@example.com",
    "password": "Password123!"
})
token = login_res["access_token"]
print(f"Login from web origin: status={status}, CORS allow-origin={headers.get('access-control-allow-origin')}")
assert headers.get("access-control-allow-origin") == "http://localhost:5173", "CORS header mismatch!"

# 2. Get workspaces
status, ws_list, _ = request("GET", "/workspaces", token=token)
print(f"Workspace selection: found {len(ws_list)} workspaces, using {ws_list[0]['id']}")
ws_id = ws_list[0]["id"]

# 3. Create a meeting
status, meeting, _ = request("POST", f"/workspaces/{ws_id}/meetings", {"title": "Web Sync Meeting"}, token=token)
meeting_id = meeting["id"]
code = meeting["code"]
print(f"Created meeting: {meeting_id}, code: {code}")

# 4. Join meeting by code (Web app Join flow)
status, join_res, _ = request("POST", "/meetings/join", {"code": code}, token=token)
print(f"Joined meeting by code: status={status}, title={join_res['title']}")
assert status == 200

# 5. Connect WebSocket matching useMeetingSocket
ws_uri = f"{WS_URL}/{meeting_id}?token={token}"
ws = websocket.create_connection(ws_uri, header=["Origin: http://localhost:5173"])
snap1 = json.loads(ws.recv())
print(f"Initial WS connection received: type={snap1.get('type')}, segment_count={len(snap1.get('data', {}).get('segments', []))}")
assert snap1.get("type") == "session.snapshot"
presence1 = json.loads(ws.recv())
print(f"Presence event: connections={presence1.get('data', {}).get('connections')}")

# 6. Simulate network disconnect
print("Simulating WebSocket disconnection...")
ws.close()

# 7. Add persisted transcript segments directly while client is disconnected
print("Injecting transcript segments during offline period...")
# Use an internal helper or audio endpoint to add a segment
from app.core.database import SessionLocal
from app.models import TranscriptSegment
import asyncio

async def add_offline_segment():
    async with SessionLocal() as db:
        seg = TranscriptSegment(
            meeting_id=meeting_id,
            chunk_index=0,
            start_offset_ms=0,
            end_offset_ms=2000,
            text="Hello from the offline period!",
            is_question=False
        )
        db.add(seg)
        await db.commit()

asyncio.run(add_offline_segment())

# 8. Reconnect WebSocket (matching useMeetingSocket reconnect)
print("Reconnecting WebSocket (reconnect behavior test)...")
ws_reconnect = websocket.create_connection(ws_uri, header=["Origin: http://localhost:5173"])
snap2 = json.loads(ws_reconnect.recv())
print(f"Reconnected WS received: type={snap2.get('type')}")
assert snap2.get("type") == "session.snapshot"

recovered_segments = snap2.get("data", {}).get("segments", [])
print(f"Recovered segments after reconnect: {len(recovered_segments)}")
assert len(recovered_segments) == 1
assert recovered_segments[0]["text"] == "Hello from the offline period!"
print("Recovered text matches perfectly:", recovered_segments[0]["text"])

ws_reconnect.close()
print("PHASE 2 WEB APP INTEGRATION & RECONNECT TESTS PASSED!")
