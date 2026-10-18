"""Load synthetic children with visits and immunisations:  python -m emr.seed

All seeded children are UNVERIFIED and carry no Home Affairs identifier, so
verification over X-Road is shown live in the demo (register a child from the
civil registry's demo cases, or link one of these). Runs once: it does
nothing if children already exist.
"""

import random
from datetime import date, timedelta

from sqlalchemy import func, select

from . import clinical
from .db import SessionLocal, init_db
from .models import Child, Immunisation, Visit

FIRST_F = ["Lineo", "Mpho", "Refiloe", "Limpho", "Keneuoe", "Boitumelo", "Masechaba", "Ntsoaki", "Mamello"]
FIRST_M = ["Teboho", "Lehlohonolo", "Retšelisitsoe", "Neo", "Tšepo", "Kabelo", "Motlatsi", "Bokang", "Hlompho"]
# Distinct from the civil registry's demo personas, so a seeded child is never mistaken for one.
SURNAMES = ["Makara", "Phiri", "Lebona", "Tau", "Mofokeng", "Nthako", "Sello", "Khoabane", "Ntšekhe", "Lerotholi"]
VILLAGES = {"MS": ["Ha Thetsane", "Ha Abia", "Lithabaneng"], "BE": ["Teyateyaneng", "Ha Mokhathi"],
            "LE": ["Hlotse", "Maputsoe"], "MF": ["Ha Ramohapi"], "MH": ["Ha Mokhethi"]}
USER = "seed"


def run(n: int = 12, seed: int = 20261020) -> int:
    init_db()
    rng = random.Random(seed)
    db = SessionLocal()
    try:
        if db.scalar(select(func.count(Child.id))):
            return 0
        today = date.today()
        for i in range(n):
            sex = "F" if i % 2 == 0 else "M"
            dob = today - timedelta(days=rng.randint(10, 700))
            district = rng.choice(list(VILLAGES))
            child = Child(
                first_name=rng.choice(FIRST_F if sex == "F" else FIRST_M), surname=rng.choice(SURNAMES), sex=sex,
                date_of_birth=dob, birth_weight_kg=round(rng.uniform(2.4, 3.9), 2), district=district,
                village=rng.choice(VILLAGES[district]), guardian_name=f"'Ma{rng.choice(FIRST_F).lower()} {rng.choice(SURNAMES)}",
                guardian_relationship=rng.choice(["MOTHER", "MOTHER", "MOTHER", "GRANDMOTHER", "AUNT"]),
                guardian_phone=f"+266 5{rng.randint(1000000, 9999999)}", created_by=USER,
                facility="Queen 'Mamohato Memorial Hospital", verification_status="UNVERIFIED",
            )
            db.add(child)
            db.flush()
            child.emr_number = f"CH-{today.year}-{child.id:06d}"
            weight = child.birth_weight_kg
            # Visits at the EPI contacts the child has reached, sometimes skipping one.
            for contact_days in (0, 42, 70, 98, 182, 274, 365, 548):
                visit_date = dob + timedelta(days=contact_days + rng.randint(0, 6))
                if visit_date > today or (contact_days and rng.random() < 0.15):
                    continue
                months = contact_days / 30.4
                weight = round(child.birth_weight_kg + 0.75 * min(months, 4) + 0.35 * max(0, min(months, 12) - 4)
                               + 0.2 * max(0, months - 12) + rng.uniform(-0.3, 0.3), 2)
                visit = Visit(
                    child=child, visit_date=visit_date,
                    visit_type="IMMUNISATION" if contact_days in (0, 42, 70, 98, 274, 548) else "GROWTH_MONITORING",
                    weight_kg=weight, height_cm=round(49 + 4 * min(months, 3) + 1.6 * max(0, months - 3), 1),
                    muac_cm=round(rng.uniform(12.2, 15.5), 1) if months >= 6 else None,
                    temperature_c=round(rng.uniform(36.2, 37.3), 1), feeding=(
                        "EXCLUSIVE_BREASTFEEDING" if months < 6 else "COMPLEMENTARY_FOODS"),
                    vitamin_a_given=months >= 6, assessment="Growing well", plan="Continue routine care",
                    next_visit_date=None, facility=child.facility, created_by=USER,
                )
                db.add(visit)
                for v in clinical.SCHEDULE.values():
                    if v.due_days == contact_days:
                        db.add(Immunisation(child=child, visit=visit, vaccine=v.key, date_given=visit_date,
                                            batch_number=f"LS{rng.randint(10000, 99999)}", given_by=USER))
        db.commit()
        return n
    finally:
        db.close()


if __name__ == "__main__":
    print(f"seeded {run()} children")
