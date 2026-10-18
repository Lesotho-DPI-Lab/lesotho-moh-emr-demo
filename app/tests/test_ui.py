"""Web UI: registration with and without verification, linking, visits, admin pages."""

import re

from emr.db import SessionLocal
from emr.models import Child
from emr import services

from .conftest import csrf_of, login

CHILD = {"first_name": "Lerato", "surname": "Mokoena", "sex": "F", "date_of_birth": "2025-08-24",
         "guardian_name": "'Malerato Mokoena", "guardian_relationship": "MOTHER", "district": "MS"}


def register(client, **extra):
    data = {**CHILD, **extra, "csrf": csrf_of(client, "/children/new")}
    r = client.post("/children", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    emr = r.headers["location"].rsplit("/", 1)[1]
    return emr, client.get(r.headers["location"])


def child(emr):
    db = SessionLocal()
    try:
        from sqlalchemy import select
        return db.scalar(select(Child).where(Child.emr_number == emr))
    finally:
        db.close()


def test_login_required(client):
    client.cookies.clear()
    r = client.get("/children", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_wrong_password(client):
    r, _ = login(client, password="wrong-password")
    assert r.status_code == 401


def test_register_without_identifier_does_not_call_registry(client, registry):
    login(client)
    before = len(registry.requests)
    emr, page = register(client, first_name="Neo", surname="Tau", sex="M")
    assert len(registry.requests) == before
    assert child(emr).verification_status == "UNVERIFIED"
    assert "Link to civil registry" in page.text


def test_identifier_without_consent_is_not_sent(client, registry):
    login(client)
    before = len(registry.requests)
    emr, page = register(client, birth_registration_number="B-MSU-2025-000001")
    assert len(registry.requests) == before
    assert child(emr).verification_status == "UNVERIFIED"
    assert "consent was not recorded" in page.text


def test_register_with_identifier_and_consent_verifies(client, registry):
    login(client)
    emr, page = register(client, birth_registration_number="b-msu-2025-000001", guardian_consent="on")
    c = child(emr)
    assert c.verification_status == "VERIFIED"
    assert c.first_name == "Lerato Palesa"  # the registry is authoritative
    assert c.verified_by == "nurse1"
    assert "Verified with Home Affairs" in page.text
    req = registry.requests[-1]
    assert req.headers["X-Road-UserId"] == "nurse1"
    assert req.headers["X-Road-Issue"].startswith(f"CONSENT-{emr}-")
    assert re.search(r"LS-DEMO-\d{4}", page.text)  # X-Road message id shown


def test_verified_identity_is_locked(client):
    login(client)
    emr, _ = register(client, birth_registration_number="B-MSU-2025-000001", guardian_consent="on")
    data = {**CHILD, "first_name": "Changed", "village": "Ha Abia", "csrf": csrf_of(client, f"/children/{emr}/edit")}
    assert client.post(f"/children/{emr}/edit", data=data, follow_redirects=False).status_code == 303
    c = child(emr)
    assert c.first_name == "Lerato Palesa" and c.village == "Ha Abia"


def test_declines_show_reason_and_remedy(client):
    login(client)
    for brn, code, status in (("B-MSU-2025-999999", "RC-01", "DECLINED"), ("B-MFT-2025-000001", "RC-05", "DECLINED")):
        emr, page = register(client, birth_registration_number=brn, guardian_consent="on")
        assert (child(emr).verification_status, child(emr).verification_reason) == (status, code)
        assert code in page.text and "What to do" in page.text
    emr, page = register(client, first_name="Palesa", birth_registration_number="B-MSU-2025-000001",
                         guardian_consent="on")
    assert child(emr).verification_reason == "RC-02"


def test_link_existing_record_later(client, registry):
    login(client)
    emr, _ = register(client)
    url = f"/children/{emr}/verify"
    before = len(registry.requests)
    client.post(url, data={"csrf": csrf_of(client, f"/children/{emr}"), "national_id": "1234 5678 9012"})
    assert len(registry.requests) == before  # no consent: nothing sent
    r = client.post(url, data={"csrf": csrf_of(client, f"/children/{emr}"), "national_id": "1234 5678 9012",
                               "guardian_consent": "on", "consent_reference": "CONSENT-PAPER-77"})
    assert r.status_code == 200
    c = child(emr)
    assert c.verification_status == "VERIFIED" and c.national_id == "123456789012"
    assert registry.requests[-1].headers["X-Road-Issue"] == "CONSENT-PAPER-77"


def test_unavailable_registry_goes_pending_then_retries(client, registry):
    login(client)
    registry.down = True
    try:
        emr, page = register(client, birth_registration_number="B-MSU-2025-000001", guardian_consent="on")
    finally:
        registry.down = False
    assert child(emr).verification_status == "PENDING"
    assert "Verification pending" in client.get("/").text
    db = SessionLocal()
    try:
        counts = services.retry_pending(db)
    finally:
        db.close()
    assert counts.get("VERIFIED", 0) >= 1
    assert child(emr).verification_status == "VERIFIED"


def test_xroad_faults(client):
    login(client)
    emr, page = register(client, birth_registration_number="B-MSU-2025-000007", guardian_consent="on")
    assert child(emr).verification_status == "ERROR"
    assert "AccessDenied" in page.text
    emr, _ = register(client, birth_registration_number="B-MSU-2025-000008", guardian_consent="on")
    assert (child(emr).verification_status, child(emr).verification_reason) == ("PENDING", "RC-06")


def test_invalid_form_rerenders(client):
    login(client)
    data = {**CHILD, "date_of_birth": "2099-01-01", "national_id": "12", "csrf": csrf_of(client, "/children/new")}
    r = client.post("/children", data=data)
    assert r.status_code == 422
    assert "Cannot be in the future" in r.text and "Expected 12 digits" in r.text


def test_visit_with_observations_and_vaccines(client):
    login(client)
    emr, _ = register(client, date_of_birth="2025-08-24")
    page = client.get(f"/children/{emr}/visits/new")
    assert "BCG" in page.text
    data = {"csrf": csrf_of(client, f"/children/{emr}/visits/new"), "visit_type": "SICK_CHILD",
            "visit_date": "2025-12-01", "weight_kg": "5.6", "muac_cm": "11.2", "temperature_c": "39.2",
            "respiratory_rate": "55", "danger_signs": "CONVULSIONS", "vaccines": ["BCG", "OPV0"],
            "batch_BCG": "LS123"}
    r = client.post(f"/children/{emr}/visits", data=data, follow_redirects=True)
    assert r.status_code == 200
    assert "High fever" in r.text and "Fast breathing" in r.text and "Refer urgently" in r.text
    c = child(emr)
    db = SessionLocal()
    try:
        c = db.merge(c)
        assert {i.vaccine for i in c.immunisations} == {"BCG", "OPV0"}
        assert c.visits[0].referred
    finally:
        db.close()
    view = client.get(f"/children/{emr}")
    assert "Given" in view.text
    # A vaccine cannot be recorded twice.
    r = client.post(f"/children/{emr}/immunisations",
                    data={"csrf": csrf_of(client, f"/children/{emr}"), "vaccine": "BCG", "date_given": "2025-12-02"})
    assert "Already recorded" in r.text


def test_growth_chart_renders(client):
    login(client)
    emr, _ = register(client, date_of_birth="2025-08-24", birth_weight_kg="3.1")
    data = {"csrf": csrf_of(client, f"/children/{emr}/visits/new"), "visit_type": "GROWTH_MONITORING",
            "visit_date": "2025-11-01", "weight_kg": "5.9"}
    client.post(f"/children/{emr}/visits", data=data)
    assert "<polyline" in client.get(f"/children/{emr}").text


def test_admin_pages(client):
    login(client)
    assert client.get("/verifications").status_code == 403
    login(client, "admin1", "admin-pass-12345")
    page = client.get("/verifications")
    assert page.status_code == 200 and "LS-DEMO-" in page.text
    r = client.post("/xroad", data={"csrf": csrf_of(client, "/xroad")})
    assert "child-verification" in r.text


def test_csrf_enforced(client):
    login(client)
    r = client.post("/children", data={**CHILD, "csrf": "forged"})
    assert r.status_code == 400
