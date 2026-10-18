"""Child EMR web UI: login, dashboard, child register, child profile with
civil registry verification, visits with observations, immunisations, and
(administrators) the verification log and X-Road status."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, clinical, services
from .config import settings
from .db import get_db
from .models import (DANGER_SIGNS, DISTRICTS, GUARDIAN_RELATIONSHIPS, SEXES, VISIT_TYPES, Child, User,
                     VerificationAttempt, Visit)
from .reason_codes import REASON_CODES
from .xroad import registry

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

STATUS_LABELS = {
    "UNVERIFIED": ("", "Not verified"),
    "VERIFIED": ("ok", "Verified with Home Affairs"),
    "DECLINED": ("bad", "Not verified"),
    "PENDING": ("warn", "Verification pending"),
    "ERROR": ("warn", "Verification failed"),
}


def humanise(value: str | None) -> str:
    return (value or "").replace("_", " ").capitalize()


def local_time(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone(timedelta(hours=2))).strftime("%d %b %Y %H:%M")


def nice_date(value: date | None) -> str:
    return value.strftime("%d %b %Y") if value else ""


templates.env.globals.update(
    DISTRICTS=DISTRICTS, SEXES=SEXES, GUARDIAN_RELATIONSHIPS=GUARDIAN_RELATIONSHIPS, VISIT_TYPES=VISIT_TYPES,
    DANGER_SIGNS=DANGER_SIGNS, REASON_CODES=REASON_CODES, STATUS_LABELS=STATUS_LABELS, SCHEDULE=clinical.SCHEDULE,
    ROLES=auth.ROLES, settings=settings, today=date.today,
)
templates.env.filters.update(human=humanise, localtime=local_time, nicedate=nice_date)


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **ctx):
    flashes = request.session.pop("flash", [])
    return templates.TemplateResponse(
        request, name,
        {"user": user, "csrf": auth.csrf_token(request), "flashes": flashes, **ctx},
        status_code=status_code,
    )


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


async def read_form(request: Request) -> tuple[dict, object]:
    form = await request.form()
    auth.check_csrf(request, str(form.get("csrf", "")))
    return {k: str(v) for k, v in form.items() if k != "csrf"}, form


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def login_redirect(request: Request) -> RedirectResponse:
    return redirect("/login?next=" + quote(request.url.path))


def get_child(db: Session, emr_number: str) -> Child:
    child = db.scalar(select(Child).where(Child.emr_number == emr_number.upper()))
    if child is None:
        raise HTTPException(404, "No child with that EMR number")
    return child


def flash_verification(request: Request, attempt: VerificationAttempt) -> None:
    if attempt.outcome == "VERIFIED":
        flash(request, "Identity verified with the Home Affairs civil registry over X-Road.")
        return
    rc = REASON_CODES.get(attempt.reason_code) if attempt.reason_code else None
    text = f"{rc.code} {humanise(rc.name)}: {rc.remedy_en}" if rc else (attempt.message or attempt.outcome)
    flash(request, f"Identity not verified. {text}", "error" if attempt.outcome == "ERROR" else "warn")


# ---------------------------------------------------------------- session


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return render(request, "login.html", next=next)


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form, _ = await read_form(request)
    nxt = form.get("next") or "/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    user = auth.authenticate(db, form.get("username", ""), form.get("password", ""))
    if user is None:
        msg = ("Too many failed attempts; try again in 5 minutes."
               if auth.locked_out(form.get("username", "").strip().lower()) else "Incorrect username or password.")
        return render(request, "login.html", next=nxt, error=msg, status_code=401)
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect(nxt)


@router.post("/logout")
async def logout(request: Request):
    await read_form(request)
    request.session.clear()
    return redirect("/login")


# ---------------------------------------------------------------- dashboard


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    counts = dict(db.execute(select(Child.verification_status, func.count()).group_by(Child.verification_status)).all())
    stats = {
        "children": sum(counts.values()),
        "verified": counts.get("VERIFIED", 0),
        "pending": counts.get("PENDING", 0),
        "not_verified": counts.get("UNVERIFIED", 0) + counts.get("DECLINED", 0) + counts.get("ERROR", 0),
        "visits_today": db.scalar(select(func.count(Visit.id)).where(Visit.visit_date == date.today())),
    }
    recent = db.scalars(select(Child).order_by(Child.created_at.desc()).limit(8)).all()
    pending = db.scalars(select(Child).where(Child.verification_status == "PENDING")).all()
    due_visits = db.scalars(select(Visit).where(Visit.next_visit_date.between(date.today(),
                                                                              date.today() + timedelta(days=7)))
                            .order_by(Visit.next_visit_date)).all()
    return render(request, "dashboard.html", user, stats=stats, recent=recent, pending=pending, due_visits=due_visits)


@router.post("/verifications/retry")
async def retry(request: Request, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    await read_form(request)
    counts = services.retry_pending(db)
    if not counts:
        flash(request, "No pending verifications.")
    else:
        flash(request, "Retried pending verifications: " + ", ".join(f"{v} {k.lower()}" for k, v in counts.items()),
              "ok" if set(counts) == {"VERIFIED"} else "warn")
    return redirect("/")


# ---------------------------------------------------------------- children


@router.get("/children", response_class=HTMLResponse)
def children(request: Request, q: str = "", status: str = "", db: Session = Depends(get_db),
             user: User = Depends(auth.current_user)):
    rows = services.search_children(db, q, status or None, limit=200)
    return render(request, "children_list.html", user, rows=rows, q=q, status=status)


@router.get("/children/new", response_class=HTMLResponse)
def new_child(request: Request, user: User = Depends(auth.current_user)):
    return render(request, "child_form.html", user, values={}, errors={}, child=None)


@router.post("/children")
async def create_child(request: Request, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    form, _ = await read_form(request)
    consent = form.get("guardian_consent") == "on"
    try:
        child, attempt = services.create_child(db, form, user.username, consent, form.get("consent_reference"))
    except services.Invalid as exc:
        return render(request, "child_form.html", user, values=form, errors=exc.errors, child=None, status_code=422)
    flash(request, f"Child registered as {child.emr_number}.")
    if attempt:
        flash_verification(request, attempt)
    elif child.has_identifier:
        flash(request, "Identifier saved but not verified: guardian consent was not recorded.", "warn")
    return redirect(f"/children/{child.emr_number}")


@router.get("/children/{emr_number}", response_class=HTMLResponse)
def child_view(request: Request, emr_number: str, db: Session = Depends(get_db),
               user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    given = {i.vaccine: i.date_given for i in child.immunisations}
    schedule = clinical.immunisation_status(child.date_of_birth, given)
    return render(request, "child_view.html", user, child=child, schedule=schedule, growth=growth_chart(child),
                  last=child.verifications[0] if child.verifications else None)


@router.get("/children/{emr_number}/edit", response_class=HTMLResponse)
def edit_child(request: Request, emr_number: str, db: Session = Depends(get_db),
               user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    values = {k: getattr(child, k) for k in (
        "first_name", "surname", "sex", "date_of_birth", "birth_weight_kg", "district", "village", "guardian_name",
        "guardian_relationship", "guardian_phone", "birth_registration_number", "national_id")}
    return render(request, "child_form.html", user, values=values, errors={}, child=child)


@router.post("/children/{emr_number}/edit")
async def update_child(request: Request, emr_number: str, db: Session = Depends(get_db),
                       user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    form, _ = await read_form(request)
    try:
        services.update_child(db, child, form)
    except services.Invalid as exc:
        db.rollback()
        return render(request, "child_form.html", user, values=form, errors=exc.errors, child=child, status_code=422)
    flash(request, "Record updated.")
    return redirect(f"/children/{child.emr_number}")


@router.post("/children/{emr_number}/verify")
async def verify_child(request: Request, emr_number: str, db: Session = Depends(get_db),
                       user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    form, _ = await read_form(request)
    if form.get("guardian_consent") != "on":
        flash(request, "Record the guardian's consent before verifying with Home Affairs.", "error")
        return redirect(f"/children/{child.emr_number}#identity")
    trigger = "RETRY" if child.verification_status == "PENDING" else "LINK"
    try:
        attempt = services.verify_child(
            db, child, user.username, services.consent_reference_for(child, form.get("consent_reference")), trigger,
            form.get("birth_registration_number"), form.get("national_id"))
    except services.AlreadyVerified:
        flash(request, "This child's identity is already verified.")
    except services.Invalid as exc:
        db.rollback()
        flash(request, " ".join(exc.errors.values()), "error")
    else:
        flash_verification(request, attempt)
    return redirect(f"/children/{child.emr_number}#identity")


# ---------------------------------------------------------------- visits


@router.get("/children/{emr_number}/visits/new", response_class=HTMLResponse)
def new_visit(request: Request, emr_number: str, type: str = "WELL_CHILD", db: Session = Depends(get_db),
              user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    return render(request, "visit_form.html", user, child=child, values={"visit_type": type}, errors={},
                  due=due_vaccines(child))


@router.post("/children/{emr_number}/visits")
async def create_visit(request: Request, emr_number: str, db: Session = Depends(get_db),
                       user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    form, raw = await read_form(request)
    form["danger_signs"] = raw.getlist("danger_signs")
    vaccines = raw.getlist("vaccines")
    batches = {k[6:]: v for k, v in form.items() if k.startswith("batch_")}
    try:
        visit = services.add_visit(db, child, form, user.username, vaccines, batches)
    except services.Invalid as exc:
        return render(request, "visit_form.html", user, child=child, values=form, errors=exc.errors,
                      due=due_vaccines(child), chosen=vaccines, status_code=422)
    flash(request, f"{VISIT_TYPES[visit.visit_type]} recorded.")
    if visit.danger_sign_list:
        flash(request, "Danger signs recorded: refer urgently.", "error")
    return redirect(f"/children/{child.emr_number}/visits/{visit.id}")


@router.get("/children/{emr_number}/visits/{visit_id}", response_class=HTMLResponse)
def visit_view(request: Request, emr_number: str, visit_id: int, db: Session = Depends(get_db),
               user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    visit = db.get(Visit, visit_id)
    if visit is None or visit.child_id != child.id:
        raise HTTPException(404, "No such visit")
    months = clinical.age_months(child.date_of_birth, visit.visit_date)
    flags = {
        "muac": clinical.muac_status(visit.muac_cm, months),
        "temperature": clinical.temperature_status(visit.temperature_c),
        "breathing": clinical.breathing_status(visit.respiratory_rate, months),
    }
    return render(request, "visit_view.html", user, child=child, visit=visit, flags=flags,
                  age=child.age_text(visit.visit_date))


@router.post("/children/{emr_number}/immunisations")
async def add_immunisation(request: Request, emr_number: str, db: Session = Depends(get_db),
                           user: User = Depends(auth.current_user)):
    child = get_child(db, emr_number)
    form, _ = await read_form(request)
    try:
        imm = services.record_immunisation(db, child, form.get("vaccine", ""), form.get("date_given"),
                                           user.username, form.get("batch_number"))
    except services.Invalid as exc:
        flash(request, " ".join(exc.errors.values()), "error")
    else:
        flash(request, f"{clinical.SCHEDULE[imm.vaccine].name} recorded.")
    return redirect(f"/children/{child.emr_number}#immunisations")


def due_vaccines(child: Child) -> list[dict]:
    given = {i.vaccine: i.date_given for i in child.immunisations}
    return [r for r in clinical.immunisation_status(child.date_of_birth, given) if r["state"] in ("DUE", "OVERDUE")]


def growth_chart(child: Child) -> dict | None:
    """Weight-for-age points scaled into a 600x220 SVG."""
    points = sorted(
        ((child.age_days(v.visit_date) / 30.4375, v.weight_kg, v.visit_date) for v in child.visits if v.weight_kg),
        key=lambda p: p[0],
    )
    if child.birth_weight_kg:
        points.insert(0, (0.0, child.birth_weight_kg, child.date_of_birth))
    if len(points) < 2:
        return None
    w, h, pad = 600, 220, 36
    max_x = max(6.0, max(p[0] for p in points) * 1.1)
    max_y = max(5.0, max(p[1] for p in points) * 1.15)
    sx = lambda x: pad + (w - 2 * pad) * x / max_x  # noqa: E731
    sy = lambda y: h - pad - (h - 2 * pad) * y / max_y  # noqa: E731
    step = 3 if max_x <= 24 else 6
    return {
        "w": w, "h": h, "pad": pad,
        "line": " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y, _ in points),
        "dots": [{"x": sx(x), "y": sy(y), "label": f"{y:g} kg at {x:.1f} months ({d:%d %b %Y})"}
                 for x, y, d in points],
        "xticks": [{"x": sx(m), "label": str(m)} for m in range(0, int(max_x) + 1, step)],
        "yticks": [{"y": sy(k), "label": str(k)} for k in range(0, int(max_y) + 1, 2 if max_y <= 14 else 5)],
    }


# ---------------------------------------------------------------- administrator


@router.get("/verifications", response_class=HTMLResponse)
def verification_log(request: Request, q: str = "", db: Session = Depends(get_db), user: User = Depends(auth.admin)):
    stmt = select(VerificationAttempt).order_by(VerificationAttempt.requested_at.desc()).limit(300)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(VerificationAttempt.xroad_id.like(like) | VerificationAttempt.consent_reference.like(like)
                          | VerificationAttempt.requested_by.like(like))
    return render(request, "verifications.html", user, rows=db.scalars(stmt).all(), q=q)


@router.get("/xroad", response_class=HTMLResponse)
def xroad_status(request: Request, user: User = Depends(auth.admin)):
    return render(request, "xroad.html", user, check=None)


@router.post("/xroad", response_class=HTMLResponse)
async def xroad_check(request: Request, user: User = Depends(auth.admin)):
    await read_form(request)
    return render(request, "xroad.html", user, check=registry().check_connection())
