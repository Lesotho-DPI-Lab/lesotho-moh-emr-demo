"""The X-Road client: what it sends to moh-ss and how it reads the answers."""

import httpx

from emr import xroad
from emr.xroad import interpret

from .conftest import CLIENT, SERVICE_PATH


def test_request_carries_xroad_headers_and_keeps_identifiers_out_of_url(registry):
    from datetime import date
    result = xroad.registry().verify(identifier_type="registrationNumber", identifier="B-MSU-2025-000001",
                                     first_name="Lerato", surname="Mokoena", date_of_birth=date(2025, 8, 24),
                                     user_id="nurse.mpho", consent_reference="CONSENT-1")
    req = registry.requests[-1]
    assert req.url.path == SERVICE_PATH
    assert "B-MSU" not in str(req.url)
    assert req.headers["X-Road-Client"] == CLIENT
    assert req.headers["X-Road-UserId"] == "nurse.mpho"
    assert req.headers["X-Road-Issue"] == "CONSENT-1"
    assert b'"purpose":"CHILD_HEALTH_SERVICE"' in req.content.replace(b" ", b"")
    assert result.outcome == "VERIFIED"
    assert result.person["firstName"] == "Lerato Palesa"
    assert result.xroad_id.startswith("LS-DEMO-")


def _resp(status, payload, headers=None):
    return httpx.Response(status, json=payload, headers=headers or {})


def test_interpret_declines_and_errors():
    assert interpret(_resp(200, {"verificationId": "x", "outcome": "DECLINED", "reasonCode": "RC-01"})).outcome == "DECLINED"
    assert interpret(_resp(200, {"verificationId": "x", "outcome": "DECLINED", "reasonCode": "RC-05"})).outcome == "DECLINED"
    assert interpret(_resp(400, {"verificationId": "x", "outcome": "DECLINED", "reasonCode": "RC-04"})).outcome == "ERROR"
    assert interpret(_resp(400, {"outcome": "ERROR", "error": "INVALID_REQUEST", "message": "bad"})).outcome == "ERROR"
    pending = interpret(_resp(503, {"verificationId": "x", "outcome": "DECLINED", "reasonCode": "RC-06"}))
    assert (pending.outcome, pending.reason_code) == ("PENDING", "RC-06")


def test_interpret_xroad_faults():
    denied = interpret(_resp(500, {"type": "Server.ServerProxy.AccessDenied", "message": "no"},
                             {"X-Road-Error": "Server.ServerProxy.AccessDenied"}))
    assert denied.outcome == "ERROR" and "AccessDenied" in denied.message
    network = interpret(_resp(500, {"type": "Server.ClientProxy.NetworkError", "message": "down"},
                              {"X-Road-Error": "Server.ClientProxy.NetworkError"}))
    assert (network.outcome, network.reason_code) == ("PENDING", "RC-06")
    garbage = httpx.Response(502, text="<html>bad gateway</html>")
    assert interpret(garbage).outcome == "PENDING"


def test_check_connection(registry):
    check = xroad.registry().check_connection()
    assert check["ok"] and check["services"] == ["child-verification"]
    assert registry.requests[-1].url.path == "/r1/LS-DEMO/GOV/MOHA/CIVIL-REGISTRY/allowedMethods"
