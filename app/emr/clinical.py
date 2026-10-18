"""Clinical reference for the demo: an illustrative EPI schedule and simple
IMCI-style flags (MUAC, fever, fast breathing).

The schedule is illustrative and must be confirmed against the current
Ministry of Health EPI schedule before any real use.
"""

from dataclasses import dataclass
from datetime import date, timedelta

OVERDUE_AFTER_DAYS = 28


@dataclass(frozen=True)
class Vaccine:
    key: str
    name: str
    due_days: int
    contact: str


SCHEDULE = {
    v.key: v
    for v in (
        Vaccine("BCG", "BCG", 0, "Birth"),
        Vaccine("OPV0", "OPV 0 (polio)", 0, "Birth"),
        Vaccine("OPV1", "OPV 1", 42, "6 weeks"),
        Vaccine("PENTA1", "Pentavalent 1 (DTP-HepB-Hib)", 42, "6 weeks"),
        Vaccine("PCV1", "PCV 1 (pneumococcal)", 42, "6 weeks"),
        Vaccine("ROTA1", "Rotavirus 1", 42, "6 weeks"),
        Vaccine("OPV2", "OPV 2", 70, "10 weeks"),
        Vaccine("PENTA2", "Pentavalent 2", 70, "10 weeks"),
        Vaccine("PCV2", "PCV 2", 70, "10 weeks"),
        Vaccine("ROTA2", "Rotavirus 2", 70, "10 weeks"),
        Vaccine("OPV3", "OPV 3", 98, "14 weeks"),
        Vaccine("PENTA3", "Pentavalent 3", 98, "14 weeks"),
        Vaccine("PCV3", "PCV 3", 98, "14 weeks"),
        Vaccine("IPV", "IPV (inactivated polio)", 98, "14 weeks"),
        Vaccine("MR1", "Measles-Rubella 1", 274, "9 months"),
        Vaccine("MR2", "Measles-Rubella 2", 548, "18 months"),
        Vaccine("DTPB", "DTP booster", 548, "18 months"),
    )
}


def immunisation_status(dob: date, given: dict[str, date], today: date | None = None) -> list[dict]:
    """Every scheduled vaccine with GIVEN, OVERDUE, DUE or UPCOMING."""
    today = today or date.today()
    rows = []
    for v in SCHEDULE.values():
        due = dob + timedelta(days=v.due_days)
        if v.key in given:
            state = "GIVEN"
        elif today >= due + timedelta(days=OVERDUE_AFTER_DAYS):
            state = "OVERDUE"
        elif today >= due:
            state = "DUE"
        else:
            state = "UPCOMING"
        rows.append({"vaccine": v, "due": due, "state": state, "given": given.get(v.key)})
    return rows


def muac_status(muac_cm: float | None, age_months: int) -> tuple[str, str] | None:
    """MUAC classification for children 6-59 months: (class, label)."""
    if muac_cm is None or not 6 <= age_months < 60:
        return None
    if muac_cm < 11.5:
        return "bad", "Severe acute malnutrition (MUAC < 11.5 cm)"
    if muac_cm < 12.5:
        return "warn", "Moderate acute malnutrition (MUAC 11.5–12.4 cm)"
    return "ok", "Normal (MUAC ≥ 12.5 cm)"


def temperature_status(temp_c: float | None) -> tuple[str, str] | None:
    if temp_c is None:
        return None
    if temp_c >= 39.0:
        return "bad", "High fever"
    if temp_c >= 37.5:
        return "warn", "Fever"
    if temp_c < 35.5:
        return "bad", "Hypothermia"
    return "ok", "Normal"


def breathing_status(rate: int | None, age_months: int) -> tuple[str, str] | None:
    if rate is None:
        return None
    limit = 60 if age_months < 2 else 50 if age_months < 12 else 40
    if rate >= limit:
        return "bad", f"Fast breathing (≥ {limit}/min for age)"
    return "ok", "Normal"


def age_months(dob: date, on: date) -> int:
    months = (on.year - dob.year) * 12 + on.month - dob.month
    return months - 1 if on.day < dob.day else months
