import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

os.environ["API_KEY"] = "test-public-key"
os.environ["UPSTREAM_API_URL"] = "https://upstream.example/birth"
os.environ["UPSTREAM_API_KEY"] = "test-upstream-key"
os.environ["ALLOW_QUERY_API_KEY"] = "true"

from api import index

client = index.app.test_client()

assert client.get("/health").status_code == 200
assert client.get("/docs").status_code == 200
assert client.get("/birth?brn=123&dob=2012-02-14").status_code == 401

captured = {}


def fake_get(url, params, headers, timeout):
    captured.update({"url": url, "params": params, "headers": headers, "timeout": timeout})
    return SimpleNamespace(
        content=json.dumps({"data": {"ok": True}, "success": True}).encode(),
        status_code=200,
        headers={"Content-Type": "application/json"},
    )

index.requests.get = fake_get
response = client.get("/birth?brn=20125017177107116&dob=2012-02-14&api=test-public-key")
assert response.status_code == 200
assert response.get_json()["success"] is True
assert captured["url"] == "https://upstream.example/birth"
assert captured["params"] == {"brn": "20125017177107116", "dob": "2012-02-14"}
assert captured["headers"]["X-API-Key"] == "test-upstream-key"

print("vercel_bridge_tests_passed")
