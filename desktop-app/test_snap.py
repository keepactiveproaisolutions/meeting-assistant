import httpx

r = httpx.get("http://127.0.0.1:8000/api/v1/meetings/620f7320-f0f0-4879-8412-7ed37ec3cece")
print("Status:", r.status_code)
data = r.json()
print("Keys:", list(data.keys()))
print("Total Utterances:", len(data.get("utterances", [])))
for u in data.get("utterances", []):
    print(f"  [{u['start_offset_ms']}ms - {u['end_offset_ms']}ms] (is_question={u['is_question']}):\n    \"{u['text']}\"")
print("Total Questions in DB:", len(data.get("questions", [])))
for q in data.get("questions", []):
    print(f"  Question: {q['text']}")
