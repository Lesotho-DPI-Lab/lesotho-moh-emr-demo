"""Child health record rules, shared by the web UI and the REST API.

Identity verification: a child may be registered without any Home Affairs
identifier. When a birth registration number or national ID is given and the
guardian has consented, the EMR verifies it with the civil registry over
X-Road, at registration or later ("link to civil registry"). On VERIFIED the
registry's names, date of birth and sex replace what was typed and are locked.
"""

import logging
import re
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import clinical
from .config import settings
from .models import (DANGER_SIGNS, DISTRICTS, GUARDIAN_RELATIONSHIPS, SEXES, VISIT_TYPES, Child, Immunisation,
                     VerificationAttempt, Visit, utcnow)
from .xroad import registry

log = logging.getLogger("emr.services")

MAX_AGE_YEARS = 12
BRN_RE = re.compile(r"^[A-Z]-[A-Z]{3}-\d{4}-\d{6}$")


class Invalid(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class AlreadyVerified(Exception):
    pass


# ---------------------------------------------------------------- parsing


def clean(value) -> str | None:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value or None


def normalise_brn(value) -> str | None:
    value = clean(value)
    return value.upper().replace(" ", "") if value else None


def normalise_nin(value) -> str | None:
    value = clean(value)
    return re.sub(r"[\s-]", "", value) if value else None


def parse_date(value, name: str, errors: dict, required: bool = False) -> date | None:
    if isinstance(value, date):
        return value
    value = clean(value)
    if not value:
        if required:
            errors[name] = "Required"
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[name] = "Use the format YYYY-MM-DD"
        return None


def parse_number(value, name: str, lo: float, hi: float, errors: dict, integer: bool = False):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = int(value) if integer else round(float(value), 2)
    except (TypeError, ValueError):
        errors[name] = "Must be a number"
        return None
    if not lo <= number <= hi:
        errors[name] = f"Must be between {lo:g} and {hi:g}"
        return None
    return number


def child_fields(data: dict, partial_locked: bool = False) -> dict:
    """Validate the child demographics form. Raises Invalid."""
    errors: dict[str, str] = {}
    out = {}
    if not partial_locked:
        out["first_name"] = clean(data.get("first_name"))
        out["surname"] = clean(data.get("surname"))
        for key in ("first_name", "surname"):
            if not out[key]:
                errors[key] = "Required"
        out["sex"] = clean(data.get("sex"))
        if out["sex"] not in SEXES:
            errors["sex"] = "Choose female or male"
        dob = parse_date(data.get("date_of_birth"), "date_of_birth", errors, required=True)
        if dob:
            if dob > date.today():
                errors["date_of_birth"] = "Cannot be in the future"
            elif (date.today() - dob).days > MAX_AGE_YEARS * 366:
                errors["date_of_birth"] = f"This EMR is for children up to {MAX_AGE_YEARS} years"
        out["date_of_birth"] = dob
        brn = normalise_brn(data.get("birth_registration_number"))
        if brn and not BRN_RE.match(brn):
            errors["birth_registration_number"] = "Expected a number like B-MSU-2025-000001"
        out["birth_registration_number"] = brn
        nin = normalise_nin(data.get("national_id"))
        if nin and not (nin.isdigit() and len(nin) == 12):
            errors["national_id"] = "Expected 12 digits"
        out["national_id"] = nin
    out["birth_weight_kg"] = parse_number(data.get("birth_weight_kg"), "birth_weight_kg", 0.4, 7, errors)
    out["district"] = clean(data.get("district"))
    if out["district"] and out["district"] not in DISTRICTS:
        errors["district"] = "Unknown district"
    out["village"] = clean(data.get("village"))
    out["guardian_name"] = clean(data.get("guardian_name"))
    out["guardian_relationship"] = clean(data.get("guardian_relationship"))
    if out["guardian_relationship"] and out["guardian_relationship"] not in GUARDIAN_RELATIONSHIPS:
        errors["guardian_relationship"] = "Unknown relationship"
    out["guardian_phone"] = clean(data.get("guardian_phone"))
    if errors:
        raise Invalid(errors)
    return out


def consent_reference_for(child: Child, given: str | None = None) -> str:
    """The guardian consent reference sent as X-Road-Issue."""
    given = clean(given)
    if given:
        return given[:60]
    return f"CONSENT-{child.emr_number}-{datetime.now().strftime('%Y%m%d%H%M%S')}"


# ---------------------------------------------------------------- children


def create_child(db: Session, data: dict, username: str, consent_given: bool = False,
                 consent_reference: str | None = None) -> tuple[Child, VerificationAttempt | None]:
    fields = child_fields(data)
    child = Child(**fields, created_by=username, facility=settings.facility, verification_status="UNVERIFIED")
    db.add(child)
    db.flush()
    child.emr_number = f"CH-{child.created_at.year if child.created_at else date.today().year}-{child.id:06d}"
    db.commit()
    attempt = None
    if child.has_identifier and consent_given:
        attempt = verify_child(db, child, username, consent_reference_for(child, consent_reference), "REGISTRATION")
    return child, attempt


def update_child(db: Session, child: Child, data: dict) -> Child:
    """Edit demographics. Once verified, identity attributes are locked."""
    fields = child_fields(data, partial_locked=child.is_verified)
    if not child.is_verified and (fields.get("birth_registration_number") != child.birth_registration_number
                                  or fields.get("national_id") != child.national_id):
        # A changed identifier invalidates any earlier decline.
        child.verification_status, child.verification_reason = "UNVERIFIED", None
    for key, value in fields.items():
        setattr(child, key, value)
    db.commit()
    return child


def search_children(db: Session, q: str | None = None, status: str | None = None, limit: int = 100):
    stmt = select(Child).order_by(Child.created_at.desc()).limit(limit)
    q = clean(q)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(
            func.lower(Child.first_name + " " + Child.surname).like(like),
            func.lower(Child.emr_number).like(like),
            func.lower(Child.birth_registration_number).like(like),
            Child.national_id.like(f"%{q}%"),
            func.lower(Child.guardian_name).like(like),
        ))
    if status:
        stmt = stmt.where(Child.verification_status == status)
    return db.scalars(stmt).all()


# ---------------------------------------------------------------- verification


def verify_child(db: Session, child: Child, username: str, consent_reference: str, trigger: str,
                 registration_number: str | None = None, national_id: str | None = None) -> VerificationAttempt:
    """Verify the child's identity with the civil registry over X-Road.

    New identifiers (when linking an existing record) are saved first. Raises
    Invalid when there is nothing to verify with, AlreadyVerified when done."""
    if child.is_verified:
        raise AlreadyVerified()
    if not clean(consent_reference):
        raise Invalid({"consent": "Record the guardian's consent before verifying"})
    errors: dict[str, str] = {}
    brn, nin = normalise_brn(registration_number), normalise_nin(national_id)
    if brn and not BRN_RE.match(brn):
        errors["birth_registration_number"] = "Expected a number like B-MSU-2025-000001"
    if nin and not (nin.isdigit() and len(nin) == 12):
        errors["national_id"] = "Expected 12 digits"
    if errors:
        raise Invalid(errors)
    if brn:
        child.birth_registration_number = brn
    if nin:
        child.national_id = nin
    if not child.has_identifier:
        raise Invalid({"birth_registration_number": "Give a birth registration number or national ID"})

    id_type, identifier = (("registrationNumber", child.birth_registration_number) if child.birth_registration_number
                           else ("nationalId", child.national_id))
    result = registry().verify(identifier_type=id_type, identifier=identifier, first_name=child.first_name,
                               surname=child.surname, date_of_birth=child.date_of_birth, user_id=username,
                               consent_reference=consent_reference)
    attempt = VerificationAttempt(
        child=child, trigger=trigger, requested_by=username, consent_reference=consent_reference,
        identifier_type=id_type, identifier=identifier, outcome=result.outcome, reason_code=result.reason_code,
        message=result.message, registry_verification_id=result.registry_verification_id,
        xroad_id=result.xroad_id, http_status=result.http_status, duration_ms=result.duration_ms,
    )
    db.add(attempt)
    child.consent_reference = consent_reference
    child.verification_status = result.outcome
    child.verification_reason = result.reason_code
    if result.outcome == "VERIFIED":
        person = result.person or {}
        # The registry is authoritative for identity attributes.
        child.first_name = person.get("firstName") or child.first_name
        child.surname = person.get("surname") or child.surname
        if person.get("dateOfBirth"):
            child.date_of_birth = date.fromisoformat(person["dateOfBirth"])
        child.sex = person.get("sex") or child.sex
        child.birth_registration_number = person.get("registrationNumber") or child.birth_registration_number
        child.verified_at = utcnow()
        child.verified_by = username
        child.verification_reason = None
    db.commit()
    return attempt


def retry_pending(db: Session) -> dict:
    """Retry every PENDING verification with its original consent and nurse."""
    counts: dict[str, int] = {}
    for child in db.scalars(select(Child).where(Child.verification_status == "PENDING")).all():
        last = child.verifications[0] if child.verifications else None
        attempt = verify_child(db, child, last.requested_by if last else "system",
                               child.consent_reference or consent_reference_for(child), "RETRY")
        counts[attempt.outcome] = counts.get(attempt.outcome, 0) + 1
    return counts


# ---------------------------------------------------------------- visits


def add_visit(db: Session, child: Child, data: dict, username: str, vaccines: list[str] | None = None,
              batches: dict[str, str] | None = None) -> Visit:
    errors: dict[str, str] = {}
    visit_date = parse_date(data.get("visit_date") or date.today().isoformat(), "visit_date", errors, True)
    if visit_date and (visit_date > date.today() or visit_date < child.date_of_birth):
        errors["visit_date"] = "Must be between the date of birth and today"
    visit_type = clean(data.get("visit_type"))
    if visit_type not in VISIT_TYPES:
        errors["visit_type"] = "Choose a visit type"
    danger = [d for d in (data.get("danger_signs") or []) if d in DANGER_SIGNS]
    next_visit = parse_date(data.get("next_visit_date"), "next_visit_date", errors)
    if next_visit and visit_date and next_visit <= visit_date:
        errors["next_visit_date"] = "Must be after the visit date"
    visit = Visit(
        child=child, visit_date=visit_date, visit_type=visit_type, complaint=clean(data.get("complaint")),
        weight_kg=parse_number(data.get("weight_kg"), "weight_kg", 0.4, 60, errors),
        height_cm=parse_number(data.get("height_cm"), "height_cm", 25, 170, errors),
        muac_cm=parse_number(data.get("muac_cm"), "muac_cm", 5, 30, errors),
        head_circumference_cm=parse_number(data.get("head_circumference_cm"), "head_circumference_cm", 20, 65, errors),
        temperature_c=parse_number(data.get("temperature_c"), "temperature_c", 30, 44, errors),
        respiratory_rate=parse_number(data.get("respiratory_rate"), "respiratory_rate", 5, 150, errors, integer=True),
        danger_signs=",".join(danger) or None, feeding=clean(data.get("feeding")),
        vitamin_a_given=_truthy(data.get("vitamin_a_given")), deworming_given=_truthy(data.get("deworming_given")),
        assessment=clean(data.get("assessment")), plan=clean(data.get("plan")),
        referred=_truthy(data.get("referred")) or bool(danger), next_visit_date=next_visit,
        facility=settings.facility, created_by=username,
    )
    given = {i.vaccine for i in child.immunisations}
    for key in vaccines or []:
        if key not in clinical.SCHEDULE:
            errors["vaccines"] = f"Unknown vaccine {key}"
        elif key in given:
            errors["vaccines"] = f"{clinical.SCHEDULE[key].name} is already recorded"
    if errors:
        db.rollback()
        raise Invalid(errors)
    db.add(visit)
    for key in vaccines or []:
        db.add(Immunisation(child=child, visit=visit, vaccine=key, date_given=visit_date,
                            batch_number=clean((batches or {}).get(key)), given_by=username))
    db.commit()
    return visit


def record_immunisation(db: Session, child: Child, vaccine: str, date_given, username: str,
                        batch_number: str | None = None) -> Immunisation:
    errors: dict[str, str] = {}
    when = parse_date(date_given or date.today().isoformat(), "date_given", errors, True)
    if vaccine not in clinical.SCHEDULE:
        errors["vaccine"] = "Unknown vaccine"
    elif any(i.vaccine == vaccine for i in child.immunisations):
        errors["vaccine"] = "Already recorded"
    if when and (when > date.today() or when < child.date_of_birth):
        errors["date_given"] = "Must be between the date of birth and today"
    if errors:
        raise Invalid(errors)
    imm = Immunisation(child=child, vaccine=vaccine, date_given=when, batch_number=clean(batch_number),
                       given_by=username)
    db.add(imm)
    db.commit()
    return imm


def _truthy(value) -> bool:
    return value in (True, "1", "on", "true", "yes")
