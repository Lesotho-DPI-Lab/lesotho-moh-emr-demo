"""Child EMR REST API, mounted at /api/v1.

Clients sign in with POST /auth/token and send the token as
`Authorization: Bearer <token>`. Children are addressed by EMR number
(CH-2026-000001), never by a Home Affairs identifier, so identifiers stay out
of URLs and web logs.
"""

from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, clinical, services
from .db import get_db
from .models import Child, User, VerificationAttempt, Visit
from .reason_codes import REASON_CODES

api = FastAPI(
    title="MoH Child EMR API (demo)",
    version="0.1.0",
    description="Child health records for the Ministry of Health. Identity verification with the Ministry of "
                "Home Affairs civil registry runs over the Lesotho Exchange (X-Road). "
                "**Demonstration only: synthetic data.**",
    docs_url=None, redoc_url=None,
)


@api.exception_handler(services.Invalid)
async def _invalid(request: Request, exc: services.Invalid):
    return JSONResponse({"error": "INVALID_REQUEST", "errors": exc.errors}, status_code=422)


# ---------------------------------------------------------------- schemas


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    accessToken: str
    tokenType: str = "Bearer"
    expiresIn: int = auth.TOKEN_SECONDS


class ChildCreate(BaseModel):
    firstName: str = Field(max_length=80)
    surname: str = Field(max_length=80)
    sex: str = Field(description="F or M")
    dateOfBirth: date
    birthWeightKg: float | None = None
    district: str | None = Field(None, description="District code, e.g. MS")
    village: str | None = None
    guardianName: str | None = None
    guardianRelationship: str | None = None
    guardianPhone: str | None = None
    birthRegistrationNumber: str | None = Field(None, max_length=24)
    nationalId: str | None = Field(None, max_length=20)
    guardianConsent: bool = Field(False, description="Guardian consented to verification with Home Affairs")
    consentReference: str | None = Field(None, max_length=60, description="Generated when omitted")


class LinkRequest(BaseModel):
    birthRegistrationNumber: str | None = Field(None, max_length=24)
    nationalId: str | None = Field(None, max_length=20)
    guardianConsent: bool = False
    consentReference: str | None = Field(None, max_length=60)


class Verification(BaseModel):
    outcome: str
    reasonCode: str | None = None
    reasonName: str | None = None
    message: str | None = None
    remedy: dict | None = None
    trigger: str
    requestedBy: str
    requestedAt: datetime
    consentReference: str | None = None
    identifierType: str
    registryVerificationId: str | None = None
    xroadId: str | None = None
    durationMs: int | None = None


class ChildOut(BaseModel):
    emrNumber: str
    firstName: str
    surname: str
    sex: str
    dateOfBirth: date
    age: str
    birthWeightKg: float | None = None
    district: str | None = None
    village: str | None = None
    guardianName: str | None = None
    guardianRelationship: str | None = None
    guardianPhone: str | None = None
    birthRegistrationNumber: str | None = None
    nationalId: str | None = None
    verificationStatus: str
    verificationReason: str | None = None
    verifiedAt: datetime | None = None
    lastVerification: Verification | None = None


class VisitCreate(BaseModel):
    visitDate: date | None = None
    visitType: str = Field(description="WELL_CHILD, GROWTH_MONITORING, IMMUNISATION, SICK_CHILD, FOLLOW_UP")
    complaint: str | None = None
    weightKg: float | None = None
    heightCm: float | None = None
    muacCm: float | None = None
    headCircumferenceCm: float | None = None
    temperatureC: float | None = None
    respiratoryRate: int | None = None
    dangerSigns: list[str] = []
    feeding: str | None = None
    vitaminAGiven: bool = False
    dewormingGiven: bool = False
    assessment: str | None = None
    plan: str | None = None
    referred: bool = False
    nextVisitDate: date | None = None
    vaccines: list[str] = Field([], description="Vaccines given at this visit (EPI keys, e.g. PENTA1)")


class VisitOut(BaseModel):
    id: int
    visitDate: date
    visitType: str
    complaint: str | None
    weightKg: float | None
    heightCm: float | None
    muacCm: float | None
    headCircumferenceCm: float | None
    temperatureC: float | None
    respiratoryRate: int | None
    dangerSigns: list[str]
    assessment: str | None
    plan: str | None
    referred: bool
    nextVisitDate: date | None
    vaccines: list[str]
    createdBy: str


class ImmunisationCreate(BaseModel):
    vaccine: str
    dateGiven: date | None = None
    batchNumber: str | None = None


class ImmunisationRow(BaseModel):
    vaccine: str
    name: str
    contact: str
    dueDate: date
    state: str = Field(description="GIVEN, DUE, OVERDUE or UPCOMING")
    dateGiven: date | None = None


# ---------------------------------------------------------------- mapping


def verification_out(a: VerificationAttempt) -> Verification:
    rc = REASON_CODES.get(a.reason_code) if a.reason_code else None
    return Verification(
        outcome=a.outcome, reasonCode=a.reason_code, reasonName=rc.name if rc else None, message=a.message,
        remedy={"en": rc.remedy_en, "st": rc.remedy_st} if rc else None, trigger=a.trigger,
        requestedBy=a.requested_by, requestedAt=a.requested_at, consentReference=a.consent_reference,
        identifierType=a.identifier_type, registryVerificationId=a.registry_verification_id,
        xroadId=a.xroad_id, durationMs=a.duration_ms,
    )


def child_out(c: Child) -> ChildOut:
    return ChildOut(
        emrNumber=c.emr_number, firstName=c.first_name, surname=c.surname, sex=c.sex, dateOfBirth=c.date_of_birth,
        age=c.age_text(), birthWeightKg=c.birth_weight_kg, district=c.district, village=c.village,
        guardianName=c.guardian_name, guardianRelationship=c.guardian_relationship,
        guardianPhone=c.guardian_phone, birthRegistrationNumber=c.birth_registration_number,
        nationalId=c.national_id, verificationStatus=c.verification_status,
        verificationReason=c.verification_reason, verifiedAt=c.verified_at,
        lastVerification=verification_out(c.verifications[0]) if c.verifications else None,
    )


def visit_out(v: Visit) -> VisitOut:
    return VisitOut(
        id=v.id, visitDate=v.visit_date, visitType=v.visit_type, complaint=v.complaint, weightKg=v.weight_kg,
        heightCm=v.height_cm, muacCm=v.muac_cm, headCircumferenceCm=v.head_circumference_cm,
        temperatureC=v.temperature_c, respiratoryRate=v.respiratory_rate, dangerSigns=v.danger_sign_list,
        assessment=v.assessment, plan=v.plan, referred=v.referred, nextVisitDate=v.next_visit_date,
        vaccines=[i.vaccine for i in v.immunisations], createdBy=v.created_by,
    )


def get_child(db: Session, emr_number: str) -> Child:
    child = db.scalar(select(Child).where(Child.emr_number == emr_number.upper()))
    if child is None:
        raise HTTPException(404, "No child with that EMR number")
    return child


# ---------------------------------------------------------------- endpoints


@api.post("/auth/token", response_model=TokenResponse, tags=["auth"])
def token(body: TokenRequest, db: Session = Depends(get_db)):
    user = auth.authenticate(db, body.username, body.password)
    if user is None:
        raise HTTPException(401, "Incorrect username or password")
    return TokenResponse(accessToken=auth.issue_token(user))


@api.get("/children", response_model=list[ChildOut], tags=["children"])
def list_children(q: str | None = None, status: str | None = None, db: Session = Depends(get_db),
                  user: User = Depends(auth.api_user)):
    return [child_out(c) for c in services.search_children(db, q, status)]


@api.post("/children", response_model=ChildOut, status_code=201, tags=["children"],
          summary="Register a child; verifies with the civil registry when an identifier and consent are given")
def create_child(body: ChildCreate, db: Session = Depends(get_db), user: User = Depends(auth.api_user)):
    data = {
        "first_name": body.firstName, "surname": body.surname, "sex": body.sex,
        "date_of_birth": body.dateOfBirth, "birth_weight_kg": body.birthWeightKg, "district": body.district,
        "village": body.village, "guardian_name": body.guardianName,
        "guardian_relationship": body.guardianRelationship, "guardian_phone": body.guardianPhone,
        "birth_registration_number": body.birthRegistrationNumber, "national_id": body.nationalId,
    }
    child, _ = services.create_child(db, data, user.username, body.guardianConsent, body.consentReference)
    db.refresh(child)
    return child_out(child)


@api.get("/children/{emr_number}", response_model=ChildOut, tags=["children"])
def read_child(emr_number: str, db: Session = Depends(get_db), user: User = Depends(auth.api_user)):
    return child_out(get_child(db, emr_number))


@api.post("/children/{emr_number}/verification", response_model=ChildOut, tags=["verification"],
          summary="Verify (or link) an existing record with the civil registry over X-Road")
def verify(emr_number: str, body: LinkRequest, db: Session = Depends(get_db), user: User = Depends(auth.api_user)):
    child = get_child(db, emr_number)
    if not body.guardianConsent:
        raise services.Invalid({"guardianConsent": "Record the guardian's consent before verifying"})
    try:
        services.verify_child(db, child, user.username, services.consent_reference_for(child, body.consentReference),
                              "API", body.birthRegistrationNumber, body.nationalId)
    except services.AlreadyVerified:
        raise HTTPException(409, "Identity already verified")
    db.refresh(child)
    return child_out(child)


@api.get("/children/{emr_number}/verifications", response_model=list[Verification], tags=["verification"])
def verifications(emr_number: str, db: Session = Depends(get_db), user: User = Depends(auth.api_user)):
    return [verification_out(a) for a in get_child(db, emr_number).verifications]


@api.get("/children/{emr_number}/visits", response_model=list[VisitOut], tags=["visits"])
def list_visits(emr_number: str, db: Session = Depends(get_db), user: User = Depends(auth.api_user)):
    return [visit_out(v) for v in get_child(db, emr_number).visits]


@api.post("/children/{emr_number}/visits", response_model=VisitOut, status_code=201, tags=["visits"])
def create_visit(emr_number: str, body: VisitCreate, db: Session = Depends(get_db),
                 user: User = Depends(auth.api_user)):
    child = get_child(db, emr_number)
    data = {
        "visit_date": body.visitDate or date.today(), "visit_type": body.visitType, "complaint": body.complaint,
        "weight_kg": body.weightKg, "height_cm": body.heightCm, "muac_cm": body.muacCm,
        "head_circumference_cm": body.headCircumferenceCm, "temperature_c": body.temperatureC,
        "respiratory_rate": body.respiratoryRate, "danger_signs": body.dangerSigns, "feeding": body.feeding,
        "vitamin_a_given": body.vitaminAGiven, "deworming_given": body.dewormingGiven,
        "assessment": body.assessment, "plan": body.plan, "referred": body.referred,
        "next_visit_date": body.nextVisitDate,
    }
    visit = services.add_visit(db, child, data, user.username, body.vaccines)
    db.refresh(visit)
    return visit_out(visit)


@api.get("/children/{emr_number}/immunisations", response_model=list[ImmunisationRow], tags=["immunisations"],
         summary="EPI schedule with the state of every vaccine")
def immunisations(emr_number: str, db: Session = Depends(get_db), user: User = Depends(auth.api_user)):
    child = get_child(db, emr_number)
    given = {i.vaccine: i.date_given for i in child.immunisations}
    return [ImmunisationRow(vaccine=r["vaccine"].key, name=r["vaccine"].name, contact=r["vaccine"].contact,
                            dueDate=r["due"], state=r["state"], dateGiven=r["given"])
            for r in clinical.immunisation_status(child.date_of_birth, given)]


@api.post("/children/{emr_number}/immunisations", response_model=list[ImmunisationRow], status_code=201,
          tags=["immunisations"])
def add_immunisation(emr_number: str, body: ImmunisationCreate, db: Session = Depends(get_db),
                     user: User = Depends(auth.api_user)):
    child = get_child(db, emr_number)
    services.record_immunisation(db, child, body.vaccine, body.dateGiven, user.username, body.batchNumber)
    db.refresh(child)
    return immunisations(emr_number, db, user)
