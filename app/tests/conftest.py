import json
import os
import re
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.update({
    "EMR_DATABASE_URL": f"sqlite:///{_db.name}",
    "EMR_SECRET_KEY": "test-secret-key-not-for-production",
    "EMR_SECURE_COOKIES": "false",
    "EMR_RETRY_INTERVAL": "0",
    "EMR_XROAD_SS_URL": "https://moh-ss.test:8443",
    "EMR_USERS": "nurse1:NURSE:Test Nurse,admin1:ADMIN:Test Admin",
    "EMR_PASSWORD_NURSE1": "nurse-pass-12345",
    "EMR_PASSWORD_ADMIN1": "admin-pass-12345",
})

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from emr import xroad  # noqa: E402
from emr.main import app  # noqa: E402

CLIENT = "LS-DEMO/GOV/MOH/CHILD-EMR"
SERVICE_PATH = "/r1/LS-DEMO/GOV/MOHA/CIVIL-REGISTRY/child-verification/verifications"

# Fake civil registry behind a fake moh-ss, keyed by birth registration number.
VERIFIED_BRN = "B-MSU-2025-000001"
REMEDY = {"en": "remedy", "st": "pheko"}


def decline(code, status=200):
    return status, {"verificationId": "v-" + code, "outcome": "DECLINED", "purpose": "CHILD_HEALTH_SERVICE",
                    "reasonCode": code, "reasonName": code, "message": "declined", "remedy": REMEDY}


class FakeRegistry:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.counter = 0
        self.down = False  # registry database unreachable: every answer is RC-06

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.counter += 1
        xid = f"LS-DEMO-{self.counter:04d}"
        if request.url.path.endswith("/allowedMethods"):
            return httpx.Response(200, json={"service": [{"service_code": "child-verification"}]},
                                  headers={"X-Road-Id": xid})
        assert request.url.path == SERVICE_PATH
        body = json.loads(request.content)
        brn = body.get("registrationNumber")
        if brn == "B-MSU-2025-000008":
            raise httpx.ConnectError("connection refused", request=request)
        if brn == "B-MSU-2025-000007":
            return httpx.Response(500, json={"type": "Server.ServerProxy.AccessDenied",
                                             "message": "Request is not allowed", "detail": "x"},
                                  headers={"X-Road-Error": "Server.ServerProxy.AccessDenied", "X-Road-Id": xid})
        if self.down:
            status, payload = decline("RC-06", 503)
        elif brn == VERIFIED_BRN or body.get("nationalId") == "123456789012":
            if body["firstName"].lower().startswith("lerato") and body["surname"].lower() == "mokoena":
                status, payload = 200, {
                    "verificationId": "v-ok", "outcome": "VERIFIED", "purpose": "CHILD_HEALTH_SERVICE",
                    "person": {"verificationStatus": "VERIFIED", "registrationNumber": VERIFIED_BRN,
                               "firstName": "Lerato Palesa", "surname": "Mokoena", "dateOfBirth": "2025-08-24",
                               "sex": "F"}}
            else:
                status, payload = decline("RC-02")
        elif brn == "B-MFT-2025-000001":
            status, payload = decline("RC-05")
        else:
            status, payload = decline("RC-01")
        return httpx.Response(status, json=payload, headers={"X-Road-Id": xid})


@pytest.fixture(scope="session")
def registry():
    fake = FakeRegistry()
    xroad.use_transport(httpx.MockTransport(fake))
    return fake


@pytest.fixture(scope="session")
def client(registry):
    with TestClient(app) as c:
        yield c


def login(client, username="nurse1", password="nurse-pass-12345"):
    client.cookies.clear()
    page = client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = client.post("/login", data={"csrf": csrf, "username": username, "password": password, "next": "/"},
                    follow_redirects=False)
    return r, csrf


def csrf_of(client, path="/"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def token(client, username="nurse1", password="nurse-pass-12345"):
    r = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["accessToken"]}
