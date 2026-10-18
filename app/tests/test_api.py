"""REST API: tokens, children with verification, visits, immunisations."""

from .conftest import token


def test_token_required(client):
    assert client.get("/api/v1/children").status_code == 401
    assert client.get("/api/v1/children", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.post("/api/v1/auth/token", json={"username": "nurse1", "password": "nope"}).status_code == 401


def test_openapi_published(client):
    spec = client.get("/api/v1/openapi.json").json()
    assert "/children/{emr_number}/verification" in spec["paths"]


def test_create_child_with_verification(client, registry):
    h = token(client)
    r = client.post("/api/v1/children", headers=h, json={
        "firstName": "Lerato", "surname": "Mokoena", "sex": "F", "dateOfBirth": "2025-08-24",
        "birthRegistrationNumber": "B-MSU-2025-000001", "guardianConsent": True, "consentReference": "API-C-1"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["verificationStatus"] == "VERIFIED"
    assert body["firstName"] == "Lerato Palesa"
    assert body["lastVerification"]["xroadId"].startswith("LS-DEMO-")
    assert registry.requests[-1].headers["X-Road-Issue"] == "API-C-1"
    again = client.post(f"/api/v1/children/{body['emrNumber']}/verification", headers=h,
                        json={"guardianConsent": True})
    assert again.status_code == 409


def test_link_and_decline(client):
    h = token(client)
    r = client.post("/api/v1/children", headers=h, json={
        "firstName": "Nthabiseng", "surname": "Letsie", "sex": "F", "dateOfBirth": "2025-05-02"})
    emr = r.json()["emrNumber"]
    assert r.json()["verificationStatus"] == "UNVERIFIED"
    no_consent = client.post(f"/api/v1/children/{emr}/verification", headers=h,
                             json={"birthRegistrationNumber": "B-MSU-2025-999999"})
    assert no_consent.status_code == 422
    r = client.post(f"/api/v1/children/{emr}/verification", headers=h,
                    json={"birthRegistrationNumber": "B-MSU-2025-999999", "guardianConsent": True})
    body = r.json()
    assert body["verificationStatus"] == "DECLINED" and body["verificationReason"] == "RC-01"
    assert body["lastVerification"]["remedy"]["en"].startswith("Refer the guardian")
    history = client.get(f"/api/v1/children/{emr}/verifications", headers=h).json()
    assert len(history) == 1 and history[0]["trigger"] == "API"


def test_visits_and_immunisations(client):
    h = token(client)
    emr = client.post("/api/v1/children", headers=h, json={
        "firstName": "Neo", "surname": "Tau", "sex": "M", "dateOfBirth": "2026-01-10"}).json()["emrNumber"]
    r = client.post(f"/api/v1/children/{emr}/visits", headers=h, json={
        "visitDate": "2026-02-21", "visitType": "IMMUNISATION", "weightKg": 4.8, "vaccines": ["OPV1", "PENTA1"]})
    assert r.status_code == 201, r.text
    assert set(r.json()["vaccines"]) == {"OPV1", "PENTA1"}
    assert len(client.get(f"/api/v1/children/{emr}/visits", headers=h).json()) == 1
    r = client.post(f"/api/v1/children/{emr}/immunisations", headers=h, json={"vaccine": "BCG", "dateGiven": "2026-01-10"})
    assert r.status_code == 201
    states = {row["vaccine"]: row["state"] for row in r.json()}
    assert states["BCG"] == "GIVEN" and states["PENTA1"] == "GIVEN" and states["MR2"] == "UPCOMING"
    bad = client.post(f"/api/v1/children/{emr}/visits", headers=h, json={"visitType": "NONSENSE", "weightKg": 900})
    assert bad.status_code == 422 and {"visit_type", "weight_kg"} <= set(bad.json()["errors"])
    assert client.get("/api/v1/children/CH-0000-000000", headers=h).status_code == 404
