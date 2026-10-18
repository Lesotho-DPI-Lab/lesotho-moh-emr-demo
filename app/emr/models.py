"""Child health record: children, identity verifications over X-Road, visits
with observations, and immunisations. No delete anywhere: records are
clinical history."""

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

SEXES = {"F": "Female", "M": "Male"}

DISTRICTS = {
    "BB": "Butha-Buthe", "BE": "Berea", "LE": "Leribe", "MF": "Mafeteng", "MS": "Maseru",
    "MH": "Mohale's Hoek", "MK": "Mokhotlong", "QN": "Qacha's Nek", "QT": "Quthing", "TT": "Thaba-Tseka",
}

GUARDIAN_RELATIONSHIPS = ("MOTHER", "FATHER", "GRANDMOTHER", "GRANDFATHER", "AUNT", "UNCLE", "SIBLING",
                          "OTHER_GUARDIAN")

VISIT_TYPES = {
    "WELL_CHILD": "Well-child check",
    "GROWTH_MONITORING": "Growth monitoring",
    "IMMUNISATION": "Immunisation",
    "SICK_CHILD": "Sick child",
    "FOLLOW_UP": "Follow-up",
}

# IMCI general danger signs: any one means urgent referral.
DANGER_SIGNS = {
    "UNABLE_TO_DRINK": "Unable to drink or breastfeed",
    "VOMITS_EVERYTHING": "Vomits everything",
    "CONVULSIONS": "Convulsions",
    "LETHARGIC": "Lethargic or unconscious",
}

# Identity verification state of a child record.
#   UNVERIFIED  no identifier given, or never checked
#   VERIFIED    civil registry confirmed the identity (attributes locked)
#   DECLINED    registry answered with RC-01, RC-02 or RC-05
#   PENDING     registry or X-Road unreachable (RC-06); retried automatically
#   ERROR       request refused (RC-03, RC-04, X-Road access denied): needs an administrator
VERIFICATION_STATES = ("UNVERIFIED", "VERIFIED", "DECLINED", "PENDING", "ERROR")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))  # NURSE | ADMIN
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    emr_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    first_name: Mapped[str] = mapped_column(String(80))
    surname: Mapped[str] = mapped_column(String(80))
    sex: Mapped[str] = mapped_column(String(1))
    date_of_birth: Mapped[date] = mapped_column(Date)
    birth_weight_kg: Mapped[float | None] = mapped_column(Float)
    district: Mapped[str | None] = mapped_column(String(2))
    village: Mapped[str | None] = mapped_column(String(80))
    guardian_name: Mapped[str | None] = mapped_column(String(120))
    guardian_relationship: Mapped[str | None] = mapped_column(String(20))
    guardian_phone: Mapped[str | None] = mapped_column(String(30))

    # Identifiers issued by Home Affairs (optional; checked over X-Road).
    birth_registration_number: Mapped[str | None] = mapped_column(String(24), index=True)
    national_id: Mapped[str | None] = mapped_column(String(20), index=True)

    verification_status: Mapped[str] = mapped_column(String(12), default="UNVERIFIED")
    verification_reason: Mapped[str | None] = mapped_column(String(8))  # RC-xx of the latest decline
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(60))
    consent_reference: Mapped[str | None] = mapped_column(String(60))

    facility: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(60))

    visits: Mapped[list["Visit"]] = relationship(back_populates="child", order_by="Visit.visit_date.desc()")
    immunisations: Mapped[list["Immunisation"]] = relationship(back_populates="child",
                                                               order_by="Immunisation.date_given")
    verifications: Mapped[list["VerificationAttempt"]] = relationship(
        back_populates="child", order_by="VerificationAttempt.requested_at.desc()")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.surname}"

    @property
    def has_identifier(self) -> bool:
        return bool(self.birth_registration_number or self.national_id)

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "VERIFIED"

    def age_days(self, on: date | None = None) -> int:
        return ((on or date.today()) - self.date_of_birth).days

    def age_text(self, on: date | None = None) -> str:
        on = on or date.today()
        months = (on.year - self.date_of_birth.year) * 12 + on.month - self.date_of_birth.month
        if on.day < self.date_of_birth.day:
            months -= 1
        if months < 1:
            return f"{self.age_days(on) // 7} weeks"
        if months < 24:
            return f"{months} months"
        return f"{months // 12} years {months % 12} months"


class VerificationAttempt(Base):
    """One identity check against the civil registry over X-Road.

    The X-Road message id (xroad_id) reconciles this line with the moh-ss
    message log and the registry's own audit trail."""

    __tablename__ = "verification_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(12))  # REGISTRATION | LINK | RETRY | API
    requested_by: Mapped[str] = mapped_column(String(60))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    consent_reference: Mapped[str | None] = mapped_column(String(60))
    identifier_type: Mapped[str] = mapped_column(String(24))  # registrationNumber | nationalId
    identifier: Mapped[str] = mapped_column(String(24))
    outcome: Mapped[str] = mapped_column(String(12))  # VERIFIED | DECLINED | PENDING | ERROR
    reason_code: Mapped[str | None] = mapped_column(String(8))
    message: Mapped[str | None] = mapped_column(Text)
    registry_verification_id: Mapped[str | None] = mapped_column(String(64))
    xroad_id: Mapped[str | None] = mapped_column(String(120), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    child: Mapped[Child] = relationship(back_populates="verifications")


class Visit(Base):
    __tablename__ = "visits"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    visit_date: Mapped[date] = mapped_column(Date)
    visit_type: Mapped[str] = mapped_column(String(20))
    complaint: Mapped[str | None] = mapped_column(Text)
    # Observations
    weight_kg: Mapped[float | None] = mapped_column(Float)
    height_cm: Mapped[float | None] = mapped_column(Float)
    muac_cm: Mapped[float | None] = mapped_column(Float)
    head_circumference_cm: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    respiratory_rate: Mapped[int | None] = mapped_column(Integer)
    danger_signs: Mapped[str | None] = mapped_column(String(200))  # comma-separated DANGER_SIGNS keys
    feeding: Mapped[str | None] = mapped_column(String(40))
    vitamin_a_given: Mapped[bool] = mapped_column(Boolean, default=False)
    deworming_given: Mapped[bool] = mapped_column(Boolean, default=False)
    assessment: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str | None] = mapped_column(Text)
    referred: Mapped[bool] = mapped_column(Boolean, default=False)
    next_visit_date: Mapped[date | None] = mapped_column(Date)
    facility: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    child: Mapped[Child] = relationship(back_populates="visits")
    immunisations: Mapped[list["Immunisation"]] = relationship(back_populates="visit")

    @property
    def danger_sign_list(self) -> list[str]:
        return [s for s in (self.danger_signs or "").split(",") if s]


class Immunisation(Base):
    __tablename__ = "immunisations"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"))
    vaccine: Mapped[str] = mapped_column(String(12))  # key of epi.SCHEDULE
    date_given: Mapped[date] = mapped_column(Date)
    batch_number: Mapped[str | None] = mapped_column(String(40))
    given_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    child: Mapped[Child] = relationship(back_populates="immunisations")
    visit: Mapped[Visit | None] = relationship(back_populates="immunisations")
