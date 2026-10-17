"""FuelPilot API – FastAPI application.

Run from the project root (the folder that contains backend/ and frontend/):

    python -m venv .venv && source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
    pip install -r backend/requirements.txt
    uvicorn backend.main:app --reload --port 8000

Then open http://localhost:8000  (the API also serves the frontend, so the
camera works on localhost without HTTPS). Interactive API docs: /docs

Environment variables
    FUELPILOT_OWNER_PASSWORD      REQUIRED in production – owner login (full access). If unset, a random one is printed at startup.
    FUELPILOT_STAFF_PASSWORD      optional – employee login (shift entry only). Unset = employee login disabled.
    FUELPILOT_SECRET              optional extra secret mixed into the login-token signing key
    FUELPILOT_DB_URL              SQLAlchemy URL (default: SQLite file in backend/data)
    FUELPILOT_FACE_ENGINE         face_recognition | deepface | demo
    FUELPILOT_FACE_THRESHOLD      Euclidean match threshold (default 0.55)
    FUELPILOT_VARIANCE_TOLERANCE  ₹ per shift before cash short/over is flagged (default 100)
    FUELPILOT_SEED                1 = load demo tanks/nozzles/rates on an empty DB (default 1)
    FUELPILOT_CORS                comma-separated allowed origins (default *)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import re
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from . import face_utils, models as m, schemas as s
from .database import SessionLocal, get_db, init_db, now_ist

# ═════════════════════════ Business rules (tune per station) ═════════════════════════
VARIANCE_TOLERANCE = float(os.getenv("FUELPILOT_VARIANCE_TOLERANCE", "100"))   # ₹ per shift
CRITICAL_MULTIPLIER = 5            # short > 5 × tolerance → critical
DELIVERY_SHORT_PCT = 0.5           # received < invoiced by more than 0.5 % → flag
DENSITY_TOLERANCE = 0.003          # g/cc, OMC acceptance band between invoice and observed density
WATER_WARN_MM, WATER_CRIT_MM = 10, 25
REORDER_LEAD_DAYS = 2              # supplier lead time used for reorder level / dry-out risk
COMPLIANCE_YELLOW_DAYS = int(os.getenv("FUELPILOT_YELLOW_DAYS", "30"))   # red = expired
PM_DUE_SOON_DAYS = 7
AMC_WARN_DAYS = 30
LATE_GRACE_MIN = 10
MIN_SHIFT_MINUTES = 5              # ignore a "shift-out" scan within 5 min of shift-in
SHIFT_LOOKBACK_H = 18              # an un-closed attendance older than this is considered abandoned
JUMP_LIMIT = {"L": 12000.0, "KG": 6000.0}   # per nozzle per shift – beyond this suspect a totaliser jump
TICKET_FLOW = ["OPEN", "TECHNICIAN_ASSIGNED", "PARTS_REPLACED", "RESOLVED"]
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"



# ═════════════════════════ Authentication & access control ═════════════════════════
# Two logins: OWNER (everything) and STAFF (shift entry only). Face attendance stays public (kiosk).
OWNER_PW = os.getenv("FUELPILOT_OWNER_PASSWORD")
STAFF_PW = os.getenv("FUELPILOT_STAFF_PASSWORD")
if not OWNER_PW:
    OWNER_PW = secrets.token_urlsafe(9)
    print(f"\n[FuelPilot] FUELPILOT_OWNER_PASSWORD not set -> temporary owner password for this run: {OWNER_PW}\n")
_SECRET = hashlib.sha256(f"{os.getenv('FUELPILOT_SECRET', '')}|{OWNER_PW}|{STAFF_PW}".encode()).digest()
TOKEN_TTL = {"owner": 8 * 3600, "staff": 12 * 3600}          # seconds
LOGIN_MAX_FAILS, LOGIN_WINDOW = 5, 300                       # 5 wrong passwords -> locked for 5 min (per IP)
SCAN_MAX, SCAN_WINDOW = 40, 60                               # face-scan requests per IP per minute
_events: dict[str, list[float]] = defaultdict(list)


def _recent(key: str, window: int) -> list[float]:
    now = time.time()
    _events[key] = [t for t in _events[key] if now - t < window]
    return _events[key]


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _sign(role: str) -> str:
    body = f"{role}|{int(time.time()) + TOKEN_TTL[role]}"
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}|{sig}".encode()).decode()


def _verify_token(token: str) -> Optional[str]:
    try:
        role, exp, sig = base64.urlsafe_b64decode(token.encode()).decode().split("|")
        good = hmac.new(_SECRET, f"{role}|{exp}".encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, good) and int(exp) > time.time() and role in TOKEN_TTL:
            return role
    except Exception:  # noqa: BLE001 – any malformed token is simply invalid
        pass
    return None


PUBLIC = {("GET", "/api/health"), ("POST", "/api/auth/login"), ("GET", "/api/face/status"), ("POST", "/api/attendance/verify")}
# Owner-only (staff get 403): money history, rates, staff/biometrics, compliance, equipment edits, alerts, dashboard...
_OWNER_ONLY = [
    ("*", r"^/api/(dashboard|alerts|credit|compliance)"), ("*", r"^/api/attendance/scan-log"),
    ("POST", r"^/api/(rates|tanks|staff/register)"), ("DELETE", r"^/api/staff/"),
    ("POST", r"^/api/equipment"), ("PUT", r"^/api/equipment"), ("DELETE", r"^/api/equipment"),
    ("GET", r"^/api/shifts/\d+$"), ("GET", r"^/api/shifts/\d+/stock-reconciliation"),
]


def guard(request: Request):
    """App-wide dependency: every /api route needs a valid token unless listed in PUBLIC."""
    method, path = request.method, request.url.path
    if method == "OPTIONS" or (method, path) in PUBLIC:
        if (method, path) == ("POST", "/api/attendance/verify"):
            hits = _recent(f"scan:{_client(request)}", SCAN_WINDOW)
            if len(hits) >= SCAN_MAX:
                raise HTTPException(429, "Too many scans – wait a minute")
            hits.append(time.time())
        return
    header = request.headers.get("authorization", "")
    role = _verify_token(header[7:]) if header.lower().startswith("bearer ") else None
    if not role:
        raise HTTPException(401, "Login required")
    if role == "owner":
        return
    # staff: block owner-only routes; the shift list is allowed only for the open shift
    if method == "GET" and path == "/api/shifts" and request.query_params.get("status", "").upper() != "OPEN":
        raise HTTPException(403, "Owner access only")
    for m_, pat in _OWNER_ONLY:
        if m_ in ("*", method) and re.match(pat, path):
            raise HTTPException(403, "Owner access only")


# ═════════════════════════ App bootstrap ═════════════════════════
@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if os.getenv("FUELPILOT_SEED", "1") == "1":
        with SessionLocal() as db:
            seed_demo_data(db)
    yield


app = FastAPI(title="FuelPilot API", version="1.0.0", lifespan=lifespan, dependencies=[Depends(guard)],
              description="Forecourt ERP for petrol, diesel & CNG stations")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("FUELPILOT_CORS", "*").split(","),
    allow_methods=["*"], allow_headers=["*"],
)



@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                         "Cache-Control": "no-store" if request.url.path.startswith("/api") else "no-cache"})
    return resp


@app.post("/api/auth/login", response_model=s.LoginOut)
def login(body: s.LoginIn, request: Request):
    key = f"login:{_client(request)}"
    if len(_recent(key, LOGIN_WINDOW)) >= LOGIN_MAX_FAILS:
        raise HTTPException(429, "Too many wrong attempts – try again in 5 minutes")
    expected = OWNER_PW if body.role == "owner" else STAFF_PW
    if expected is None:
        raise HTTPException(403, "Employee login is not enabled (set FUELPILOT_STAFF_PASSWORD)")
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        _events[key].append(time.time())
        time.sleep(0.6)                              # slows scripted guessing
        raise HTTPException(401, "Wrong password")
    _events.pop(key, None)
    return s.LoginOut(token=_sign(body.role), role=body.role, expires_in=TOKEN_TTL[body.role])


@app.get("/api/auth/me")
def me(request: Request):
    return {"role": _verify_token(request.headers.get("authorization", "")[7:])}


@app.get("/api/health")
def health():
    return {"status": "ok", "time": now_ist()}


# ═════════════════════════ Helpers ═════════════════════════
def tank_volume(tank: m.Tank, dip_mm: float) -> float:
    """Dip (mm) → litres for a horizontal cylindrical tank (CASCADE: dip field already holds kg)."""
    if tank.kind == "CASCADE":
        return round(min(dip_mm, tank.capacity), 1)
    r = tank.diameter_mm / 2
    h = min(max(dip_mm, 0.0), tank.diameter_mm)
    length = tank.capacity / (math.pi * r * r)                     # nominal capacity fixes the length
    seg = r * r * math.acos((r - h) / r) - (r - h) * math.sqrt(max(2 * r * h - h * h, 0.0))
    return round(seg * length, 1)


def water_alert(mm: float) -> str:
    return "CRITICAL" if mm >= WATER_CRIT_MM else "WARNING" if mm >= WATER_WARN_MM else ""


def current_rate(db: Session, product: str) -> Optional[float]:
    r = (db.query(m.FuelRate).filter(m.FuelRate.product == product, m.FuelRate.effective_from <= now_ist())
         .order_by(m.FuelRate.effective_from.desc(), m.FuelRate.id.desc()).first())
    return r.rate if r else None


def last_closing(db: Session, nozzle_id: int) -> Optional[float]:
    r = (db.query(m.MeterReading).join(m.Shift).filter(m.MeterReading.nozzle_id == nozzle_id,
                                                      m.MeterReading.closing.isnot(None), m.Shift.status == "CLOSED")
         .order_by(m.Shift.closed_at.desc()).first())
    return r.closing if r else None


def avg_daily_sales(db: Session, product: str, days: int = 7) -> Optional[float]:
    rows = (db.query(m.Shift.day, func.sum(m.MeterReading.sold_qty))
            .join(m.MeterReading, m.MeterReading.shift_id == m.Shift.id)
            .join(m.Nozzle, m.Nozzle.id == m.MeterReading.nozzle_id)
            .filter(m.Shift.status == "CLOSED", m.Nozzle.product == product,
                    m.Shift.day >= now_ist().date() - timedelta(days=days)).group_by(m.Shift.day).all())
    return round(sum(r[1] or 0 for r in rows) / len(rows), 1) if rows else None


def open_attendance(db: Session, staff_id: int) -> Optional[m.Attendance]:
    return (db.query(m.Attendance).filter(m.Attendance.staff_id == staff_id, m.Attendance.out_time.is_(None),
                                          m.Attendance.in_time >= now_ist() - timedelta(hours=SHIFT_LOOKBACK_H))
            .order_by(m.Attendance.in_time.desc()).first())


def att_out(a: m.Attendance) -> s.AttendanceOut:
    return s.AttendanceOut(id=a.id, staff_id=a.staff_id, staff_name=a.staff.name, staff_role=a.staff.role,
                           day=a.day, in_time=a.in_time, out_time=a.out_time, status=a.status,
                           worked_minutes=a.worked_minutes)


def tank_unit(t: m.Tank) -> str:
    return "KG" if t.product == "CNG" else "L"


def latest_dip(db: Session, tank_id: int) -> Optional[m.DipReading]:
    return (db.query(m.DipReading).filter(m.DipReading.tank_id == tank_id)
            .order_by(m.DipReading.ts.desc(), m.DipReading.id.desc()).first())


# ═════════════════════════ 1. Staff & face attendance ═════════════════════════
@app.get("/api/face/status")
def face_status():
    try:
        eng = face_utils.get_engine()
        return {"ready": True, "engine": eng.name, "threshold": face_utils.engine_threshold(),
                "secure": eng.name != "demo"}
    except RuntimeError as exc:
        return {"ready": False, "engine": None, "error": str(exc)}


def _engine_or_503():
    try:
        return face_utils.get_engine()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


def _gallery(db: Session, exclude_staff: Optional[int] = None):
    rows = db.query(m.Staff).filter(m.Staff.is_active.is_(True)).all()
    return [(st.id, np.asarray(v, dtype=np.float64)) for st in rows if st.id != exclude_staff
            for v in (st.face_embeddings or [])]


@app.post("/api/face/check")
def face_check(body: s.FaceCheckIn):
    """Live photo-quality check used by the registration screen's auto-capture."""
    _engine_or_503()
    return face_utils.assess(body.frame)


@app.post("/api/staff/register", response_model=s.StaffRegisterOut, status_code=201)
def register_staff(body: s.StaffRegister, db: Session = Depends(get_db)):
    """Detect face + landmarks in each photo, compute 128-d embeddings, store with staff details."""
    eng = _engine_or_503()
    unknown = set(body.assigned_nozzles) - {n.code for n in db.query(m.Nozzle).all()}
    if unknown:
        raise HTTPException(422, f"Unknown nozzle(s): {', '.join(sorted(unknown))}")

    vectors, landmark_groups = [], 0
    for i, photo in enumerate(body.photos, 1):
        try:
            quality = face_utils.assess(photo)          # face-crop sharpness / light / size check
            if not quality["ok"]:
                raise face_utils.FaceError(quality["reason"])
            res = face_utils.embed_single(photo, jitters=2)   # extra jitter = steadier stored embedding
        except face_utils.FaceError as exc:
            raise HTTPException(422, f"Photo {i}: {exc}")
        landmark_groups = max(landmark_groups, res.landmarks)
        vectors.append(res.embedding)

    # all photos must be the same person
    thr = face_utils.engine_threshold()
    if any(np.linalg.norm(v - vectors[0]) > thr for v in vectors[1:]):
        raise HTTPException(422, "Photos appear to show different people – use photos of one person")

    # the face must not already belong to someone else (stops duplicate / buddy registrations)
    for v in vectors:
        hit = face_utils.match(v, _gallery(db))
        if hit.staff_id:
            other = db.get(m.Staff, hit.staff_id)
            raise HTTPException(409, f"This face is already registered as {other.name} ({other.employee_code})")

    st = m.Staff(name=body.name.strip(), role=body.role.strip(), phone=body.phone, shift_name=body.shift_name,
                 shift_start=body.shift_start, shift_end=body.shift_end, assigned_nozzles=body.assigned_nozzles,
                 face_embeddings=[v.tolist() for v in vectors], face_engine=eng.name, biometric_consent=True)
    db.add(st)
    db.flush()
    st.employee_code = f"EMP{st.id:03d}"
    db.commit()
    return s.StaffRegisterOut(staff=s.StaffOut.model_validate(st), embeddings_stored=len(vectors),
                              landmark_groups=landmark_groups, message=f"{st.name} registered as {st.employee_code}")


@app.get("/api/staff", response_model=list[s.StaffOut])
def list_staff(db: Session = Depends(get_db)):
    return db.query(m.Staff).filter(m.Staff.is_active.is_(True)).order_by(m.Staff.name).all()


@app.delete("/api/staff/{staff_id}", status_code=204)
def deactivate_staff(staff_id: int, db: Session = Depends(get_db)):
    """Soft-delete: attendance history is kept, biometric vectors are wiped."""
    st = db.get(m.Staff, staff_id) or _404("Staff")
    st.is_active, st.face_embeddings = False, []
    db.commit()


def _log_scan(db: Session, ok: bool, reason: str, staff_id=None, dist=None):
    db.add(m.FaceScanLog(success=ok, staff_id=staff_id, distance=None if dist is None or math.isinf(dist) else round(dist, 4),
                         reason=reason[:160]))
    db.commit()


@app.post("/api/attendance/verify", response_model=s.VerifyOut)
def verify_attendance(body: s.VerifyIn, db: Session = Depends(get_db)):
    """Match webcam frames against stored embeddings and log Shift-In / Shift-Out."""
    _engine_or_503()

    def fail(msg: str, dist: Optional[float] = None, staff_id: Optional[int] = None):
        _log_scan(db, False, msg, staff_id, dist)
        return s.VerifyOut(verified=False, message=msg, distance=dist)

    # Anti-replay: a live webcam never produces byte-identical JPEGs (sensor noise), so a payload
    # whose frames are all the same was re-sent or fabricated. (This is NOT liveness detection.)
    if len(body.frames) > 1 and len({hashlib.sha256(f.encode()).hexdigest() for f in body.frames}) == 1:
        return fail("Identical frames rejected – use the live camera")

    gallery = _gallery(db)
    if not gallery:
        raise HTTPException(409, "No staff registered yet")

    matches, last_err = [], ""
    for frame in body.frames:
        try:
            matches.append(face_utils.match(face_utils.embed_single(frame).embedding, gallery))
        except face_utils.FaceError as exc:
            last_err = str(exc)
    if len(matches) < min(2, len(body.frames)):
        return fail(last_err or "No usable face in frames")

    ids = {x.staff_id for x in matches}
    if None in ids or len(ids) != 1:
        worst = max(matches, key=lambda x: x.distance)
        return fail(worst.reason or "Frames did not agree on one person", worst.distance)

    staff = db.get(m.Staff, ids.pop())
    dist = round(float(np.mean([x.distance for x in matches])), 4)
    now = now_ist()
    open_rec = open_attendance(db, staff.id)
    action = body.action if body.action != "AUTO" else ("OUT" if open_rec else "IN")

    if action == "IN":
        if open_rec:
            _log_scan(db, True, "already checked in", staff.id, dist)
            return s.VerifyOut(verified=True, action="IN", staff=staff, distance=dist, attendance=att_out(open_rec),
                               message=f"{staff.name} is already on duty since {open_rec.in_time:%H:%M}")
        sh, sm = map(int, staff.shift_start.split(":"))
        late = now > now.replace(hour=sh, minute=sm, second=0, microsecond=0) + timedelta(minutes=LATE_GRACE_MIN)
        rec = m.Attendance(staff_id=staff.id, day=now.date(), in_time=now, in_distance=dist,
                           status="LATE" if late else "PRESENT")
        db.add(rec); db.commit(); db.refresh(rec)
        _log_scan(db, True, "shift in", staff.id, dist)
        return s.VerifyOut(verified=True, action="IN", staff=staff, distance=dist, attendance=att_out(rec), late=late,
                           message=f"Welcome {staff.name} – shift started {now:%H:%M}" + (" (late)" if late else ""))

    if not open_rec:
        return fail(f"{staff.name} has no active shift-in to close", dist, staff.id)
    if (now - open_rec.in_time).total_seconds() < MIN_SHIFT_MINUTES * 60:
        return fail(f"Shift-out blocked: {staff.name} checked in less than {MIN_SHIFT_MINUTES} min ago", dist, staff.id)
    open_rec.out_time, open_rec.out_distance = now, dist
    db.commit()
    _log_scan(db, True, "shift out", staff.id, dist)
    return s.VerifyOut(verified=True, action="OUT", staff=staff, distance=dist, attendance=att_out(open_rec),
                       message=f"Goodbye {staff.name} – worked {open_rec.worked_minutes // 60}h {open_rec.worked_minutes % 60}m")


@app.get("/api/attendance", response_model=list[s.AttendanceOut])
def list_attendance(day: Optional[date] = None, db: Session = Depends(get_db)):
    day = day or now_ist().date()
    rows = (db.query(m.Attendance).options(joinedload(m.Attendance.staff)).filter(m.Attendance.day == day)
            .order_by(m.Attendance.in_time).all())
    return [att_out(a) for a in rows]


@app.get("/api/attendance/active", response_model=list[s.AttendanceOut])
def active_attendance(db: Session = Depends(get_db)):
    rows = (db.query(m.Attendance).options(joinedload(m.Attendance.staff))
            .filter(m.Attendance.out_time.is_(None), m.Attendance.in_time >= now_ist() - timedelta(hours=SHIFT_LOOKBACK_H))
            .order_by(m.Attendance.in_time).all())
    return [att_out(a) for a in rows]


@app.get("/api/attendance/scan-log", response_model=list[s.ScanLogOut])
def scan_log(limit: int = Query(40, le=200), db: Session = Depends(get_db)):
    rows = db.query(m.FaceScanLog).order_by(m.FaceScanLog.ts.desc()).limit(limit).all()
    names = {st.id: st.name for st in db.query(m.Staff).all()}
    return [s.ScanLogOut(id=r.id, ts=r.ts, staff_name=names.get(r.staff_id), success=r.success,
                         distance=r.distance, reason=r.reason) for r in rows]


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


# ═════════════════════════ 2. Rates, nozzles, shifts & reconciliation ═════════════════════════
@app.get("/api/rates")
def get_rates(db: Session = Depends(get_db)):
    return {p: current_rate(db, p) for p in ("PETROL", "DIESEL", "CNG")}


@app.post("/api/rates", status_code=201)
def set_rate(body: s.RateIn, db: Session = Depends(get_db)):
    db.add(m.FuelRate(product=body.product, rate=body.rate)); db.commit()
    return get_rates(db)


@app.get("/api/nozzles", response_model=list[s.NozzleOut])
def list_nozzles(db: Session = Depends(get_db)):
    out = []
    for n in db.query(m.Nozzle).filter(m.Nozzle.is_active.is_(True)).order_by(m.Nozzle.code).all():
        out.append(s.NozzleOut(id=n.id, code=n.code, dispenser=n.dispenser, product=n.product, unit=n.unit,
                               last_closing=last_closing(db, n.id), rate=current_rate(db, n.product)))
    return out


def shift_out(sh: m.Shift, warnings: Optional[list[str]] = None) -> s.ShiftOut:
    return s.ShiftOut(
        id=sh.id, day=sh.day, shift_name=sh.shift_name, manager_name=sh.manager.name if sh.manager else None,
        status=sh.status, opened_at=sh.opened_at, closed_at=sh.closed_at, expected_revenue=sh.expected_revenue,
        cash_amount=sh.cash_amount, upi_amount=sh.upi_amount, card_amount=sh.card_amount, credit_amount=sh.credit_amount,
        total_collections=sh.total_collections, variance=sh.variance, variance_status=sh.variance_status,
        severity=sh.severity, tolerance=sh.tolerance, notes=sh.notes, warnings=warnings or [],
        readings=[s.ReadingOut(nozzle_code=r.nozzle.code, product=r.nozzle.product, unit=r.nozzle.unit, opening=r.opening,
                               closing=r.closing, test_litres=r.test_litres, sold_qty=r.sold_qty, rate=r.rate,
                               expected_amount=r.expected_amount, opening_mismatch=r.opening_mismatch)
                  for r in sorted(sh.readings, key=lambda r: r.nozzle.code)])


def _load_shift(db: Session, shift_id: int) -> m.Shift:
    sh = (db.query(m.Shift).options(joinedload(m.Shift.readings).joinedload(m.MeterReading.nozzle),
                                    joinedload(m.Shift.manager)).filter(m.Shift.id == shift_id).first())
    return sh or _404("Shift")


@app.post("/api/shifts", response_model=s.ShiftOut, status_code=201)
def open_shift(body: s.ShiftOpen, db: Session = Depends(get_db)):
    """Open a shift and capture opening totaliser readings (defaults to the previous closing)."""
    if db.query(m.Shift).filter(m.Shift.status == "OPEN").first():
        raise HTTPException(409, "A shift is already open – close it first")
    nozzles = db.query(m.Nozzle).filter(m.Nozzle.is_active.is_(True)).all()
    if not nozzles:
        raise HTTPException(409, "No nozzles configured")
    sh = m.Shift(day=now_ist().date(), shift_name=body.shift_name, manager_id=body.manager_id, tolerance=VARIANCE_TOLERANCE)
    db.add(sh)
    for n in nozzles:
        rate = current_rate(db, n.product)
        if rate is None:
            raise HTTPException(422, f"Set a {n.product} rate first")
        prev = last_closing(db, n.id)
        given = body.openings.get(n.code)
        if given is None and prev is None:
            raise HTTPException(422, f"Enter opening totaliser for {n.code} (first shift)")
        opening = given if given is not None else prev
        mismatch = prev is not None and given is not None and abs(given - prev) > 0.005
        sh.readings.append(m.MeterReading(nozzle_id=n.id, opening=opening, rate=rate, opening_mismatch=mismatch))
    db.commit()
    return shift_out(_load_shift(db, sh.id), [f"{r.nozzle.code}: opening differs from last closing – check for tampering"
                                              for r in _load_shift(db, sh.id).readings if r.opening_mismatch])


@app.get("/api/shifts", response_model=list[s.ShiftOut])
def list_shifts(limit: int = Query(20, le=100), status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(m.Shift)
    if status:
        q = q.filter(m.Shift.status == status.upper())
    return [shift_out(_load_shift(db, x.id)) for x in q.order_by(m.Shift.id.desc()).limit(limit).all()]


@app.get("/api/shifts/{shift_id}", response_model=s.ShiftOut)
def get_shift(shift_id: int, db: Session = Depends(get_db)):
    return shift_out(_load_shift(db, shift_id))


@app.post("/api/shifts/{shift_id}/close", response_model=s.ShiftOut)
def close_shift(shift_id: int, body: s.ShiftClose, db: Session = Depends(get_db)):
    """Compute litres/kg sold × rate = expected revenue, compare to collections, flag variance."""
    sh = _load_shift(db, shift_id)
    if sh.status != "OPEN":
        raise HTTPException(409, "Shift already closed")
    given = {r.nozzle_code: r for r in body.readings}
    missing = {r.nozzle.code for r in sh.readings} - set(given)
    if missing or set(given) - {r.nozzle.code for r in sh.readings}:
        raise HTTPException(422, f"Closing readings must cover every nozzle (missing: {', '.join(sorted(missing)) or 'none'})")

    warnings, expected = [], 0.0
    for r in sh.readings:
        g = given[r.nozzle.code]
        if g.closing < r.opening:
            raise HTTPException(422, f"{r.nozzle.code}: closing {g.closing} is below opening {r.opening}. "
                                     "Totaliser rollover or jumping – raise a 'Totaliser jumping' ticket.")
        sold = round(g.closing - r.opening - g.test_litres, 2)
        if sold < 0:
            raise HTTPException(422, f"{r.nozzle.code}: test quantity exceeds dispensed quantity")
        r.closing, r.test_litres, r.sold_qty = g.closing, g.test_litres, sold
        r.expected_amount = round(sold * r.rate, 2)
        expected += r.expected_amount
        if sold > JUMP_LIMIT[r.nozzle.unit]:
            warnings.append(f"{r.nozzle.code}: {sold:,.0f} {r.nozzle.unit} in one shift looks like a totaliser jump")
        if sold == 0:
            warnings.append(f"{r.nozzle.code}: zero sales recorded for the shift")
        if r.opening_mismatch:
            warnings.append(f"{r.nozzle.code}: opening reading did not match previous closing")

    vouchers = [m.CreditVoucher(**v.model_dump()) for v in body.vouchers]
    credit = round(sum(v.amount for v in vouchers), 2) if vouchers else body.credit
    sh.vouchers = vouchers
    sh.cash_amount, sh.upi_amount, sh.card_amount, sh.credit_amount = body.cash, body.upi, body.card, credit
    sh.total_collections = round(body.cash + body.upi + body.card + credit, 2)
    sh.expected_revenue = round(expected, 2)
    sh.variance = round(sh.total_collections - sh.expected_revenue, 2)       # + over / − short
    sh.tolerance = VARIANCE_TOLERANCE
    if abs(sh.variance) <= VARIANCE_TOLERANCE:
        sh.variance_status, sh.severity = "OK", "ok"
    elif sh.variance < 0:
        sh.variance_status = "SHORT"
        sh.severity = "critical" if -sh.variance > CRITICAL_MULTIPLIER * VARIANCE_TOLERANCE else "warning"
    else:
        sh.variance_status, sh.severity = "OVER", "warning"
    sh.status, sh.closed_at, sh.notes = "CLOSED", now_ist(), body.notes
    db.commit()
    return shift_out(_load_shift(db, sh.id), warnings)


@app.get("/api/shifts/{shift_id}/report")
def shift_report(shift_id: int, db: Session = Depends(get_db)):
    """Plain-text closing report (used for the WhatsApp message)."""
    sh = _load_shift(db, shift_id)
    by = defaultdict(float)
    for r in sh.readings:
        by[r.nozzle.product] += r.sold_qty
    flag = {"OK": "✅ within tolerance", "SHORT": "🔴 SHORT", "OVER": "🟠 OVER"}.get(sh.variance_status, "")
    text = (f"FuelPilot Shift Report – {sh.day:%d %b %Y}, {sh.shift_name}\n"
            f"Petrol {by['PETROL']:,.1f} L | Diesel {by['DIESEL']:,.1f} L | CNG {by['CNG']:,.1f} kg\n"
            f"Expected ₹{sh.expected_revenue:,.2f}\nCollected ₹{sh.total_collections:,.2f} "
            f"(Cash {sh.cash_amount:,.0f}, UPI {sh.upi_amount:,.0f}, Card {sh.card_amount:,.0f}, Credit {sh.credit_amount:,.0f})\n"
            f"Variance ₹{sh.variance:+,.2f} {flag}")
    return {"text": text}


@app.get("/api/shifts/{shift_id}/stock-reconciliation")
def stock_reconciliation(shift_id: int, db: Session = Depends(get_db)):
    """Per product: opening dip + receipts − metered sales = book closing, compared with the closing dip."""
    sh = _load_shift(db, shift_id)
    out = []
    for product in ("PETROL", "DIESEL"):
        tanks = db.query(m.Tank).filter(m.Tank.product == product).all()
        ids = [t.id for t in tanks]
        def dip_total(kind):
            tot, n = 0.0, 0
            for tid in ids:
                d = (db.query(m.DipReading).filter_by(tank_id=tid, shift_id=sh.id, reading_type=kind)
                     .order_by(m.DipReading.ts.desc()).first())
                if d:
                    tot, n = tot + d.volume, n + 1
            return tot, n
        op, n_op = dip_total("OPENING")
        cl, n_cl = dip_total("CLOSING")
        receipts = db.query(func.coalesce(func.sum(m.Delivery.received_qty), 0)).filter(
            m.Delivery.shift_id == sh.id, m.Delivery.tank_id.in_(ids)).scalar() if ids else 0
        sales = sum(r.sold_qty for r in sh.readings if r.nozzle.product == product)
        complete = n_op == len(ids) == n_cl and len(ids) > 0
        book = op + receipts - sales
        tol = max(20.0, 0.003 * sales)
        var = round(cl - book, 1)
        out.append({"product": product, "complete": complete, "opening_dip": round(op, 1), "receipts": round(receipts, 1),
                    "metered_sales": round(sales, 1), "book_closing": round(book, 1), "closing_dip": round(cl, 1),
                    "variance": var if complete else None, "tolerance": round(tol, 1),
                    "status": "INCOMPLETE" if not complete else "OK" if abs(var) <= tol else "LOSS" if var < 0 else "GAIN"})
    return out


@app.get("/api/credit/customers", response_model=list[s.CreditCustomerOut])
def credit_customers(db: Session = Depends(get_db)):
    rows = (db.query(m.CreditVoucher.customer_name, func.sum(m.CreditVoucher.amount), func.count(m.CreditVoucher.id),
                     func.min(m.CreditVoucher.created_at)).filter(m.CreditVoucher.settled.is_(False))
            .group_by(m.CreditVoucher.customer_name).order_by(func.sum(m.CreditVoucher.amount).desc()).all())
    return [s.CreditCustomerOut(customer_name=r[0], outstanding=round(r[1], 2), vouchers=r[2], oldest=r[3].date()) for r in rows]


@app.post("/api/credit/settle")
def settle_credit(customer_name: str, db: Session = Depends(get_db)):
    n = (db.query(m.CreditVoucher).filter(m.CreditVoucher.customer_name == customer_name, m.CreditVoucher.settled.is_(False))
         .update({"settled": True, "settled_at": now_ist()}))
    db.commit()
    return {"settled_vouchers": n}


# ═════════════════════════ 3. Tanks, dips, deliveries ═════════════════════════
@app.post("/api/tanks", response_model=s.TankOut, status_code=201)
def add_tank(body: s.TankIn, db: Session = Depends(get_db)):
    if db.query(m.Tank).filter_by(code=body.code).first():
        raise HTTPException(409, "Tank code exists")
    t = m.Tank(**body.model_dump()); db.add(t); db.commit()
    return tank_status(db, t)


def tank_status(db: Session, t: m.Tank) -> s.TankOut:
    d = latest_dip(db, t.id)
    stock = d.volume if d else None
    n_same = db.query(m.Tank).filter_by(product=t.product).count() or 1
    avg = avg_daily_sales(db, t.product)
    avg_tank = round(avg / n_same, 1) if avg else None
    usable = None if stock is None else max(stock - t.dead_stock, 0)
    days = round(usable / avg_tank, 1) if (usable is not None and avg_tank) else None
    reorder = round(t.min_stock + (avg_tank or 0) * REORDER_LEAD_DAYS, 0)
    if stock is None:
        status = "NO_DATA"
    elif usable <= t.min_stock * 0.5 or (days is not None and days < 1):
        status = "DRY_RISK"
    elif stock <= max(t.min_stock, reorder if avg_tank else 0) or (days is not None and days < REORDER_LEAD_DAYS):
        status = "LOW"
    else:
        status = "OK"
    return s.TankOut(id=t.id, code=t.code, product=t.product, kind=t.kind, capacity=t.capacity, min_stock=t.min_stock,
                     dead_stock=t.dead_stock, unit=tank_unit(t), last_dip_at=d.ts if d else None, stock=stock,
                     fill_pct=round(stock / t.capacity * 100, 1) if stock is not None else None,
                     water_mm=d.water_mm if d else 0, avg_daily_sales=avg_tank, days_left=days,
                     reorder_level=reorder, status=status, water_alert=water_alert(d.water_mm) if d else "")


@app.get("/api/tanks", response_model=list[s.TankOut])
def list_tanks(db: Session = Depends(get_db)):
    return [tank_status(db, t) for t in db.query(m.Tank).order_by(m.Tank.code).all()]


def _tank(db: Session, code: str) -> m.Tank:
    return db.query(m.Tank).filter_by(code=code).first() or _404(f"Tank {code}")


@app.post("/api/dips", response_model=s.DipOut, status_code=201)
def record_dip(body: s.DipIn, db: Session = Depends(get_db)):
    t = _tank(db, body.tank_code)
    if t.kind == "UNDERGROUND" and body.dip_mm > t.diameter_mm:
        raise HTTPException(422, f"Dip {body.dip_mm} mm exceeds tank diameter {t.diameter_mm:.0f} mm")
    if t.kind == "CASCADE" and body.dip_mm > t.capacity:
        raise HTTPException(422, f"Quantity exceeds cascade capacity {t.capacity:.0f}")
    open_shift = db.query(m.Shift).filter_by(status="OPEN").first()
    d = m.DipReading(tank_id=t.id, reading_type=body.reading_type, dip_mm=body.dip_mm, water_mm=body.water_mm,
                     volume=tank_volume(t, body.dip_mm), recorded_by_id=body.recorded_by_id,
                     shift_id=open_shift.id if open_shift else None)
    db.add(d); db.commit()
    return s.DipOut(id=d.id, tank_code=t.code, product=t.product, ts=d.ts, reading_type=d.reading_type, dip_mm=d.dip_mm,
                    water_mm=d.water_mm, volume=d.volume, water_alert=water_alert(d.water_mm))


@app.get("/api/dips", response_model=list[s.DipOut])
def list_dips(tank_code: Optional[str] = None, limit: int = Query(30, le=200), db: Session = Depends(get_db)):
    q = db.query(m.DipReading).options(joinedload(m.DipReading.tank))
    if tank_code:
        q = q.filter(m.DipReading.tank_id == _tank(db, tank_code).id)
    return [s.DipOut(id=d.id, tank_code=d.tank.code, product=d.tank.product, ts=d.ts, reading_type=d.reading_type,
                     dip_mm=d.dip_mm, water_mm=d.water_mm, volume=d.volume, water_alert=water_alert(d.water_mm))
            for d in q.order_by(m.DipReading.ts.desc(), m.DipReading.id.desc()).limit(limit).all()]


def delivery_out(d: m.Delivery) -> s.DeliveryOut:
    return s.DeliveryOut(id=d.id, tank_code=d.tank.code, product=d.tank.product, ts=d.ts, invoice_no=d.invoice_no,
                         supplier=d.supplier, vehicle_no=d.vehicle_no, invoiced_qty=d.invoiced_qty,
                         pre_volume=d.pre_volume, post_volume=d.post_volume, sales_during=d.sales_during,
                         received_qty=d.received_qty, variance_qty=d.variance_qty, variance_pct=d.variance_pct,
                         invoice_density=d.invoice_density, observed_density=d.observed_density,
                         status=d.status, notes=d.notes)


@app.post("/api/deliveries", response_model=s.DeliveryOut, status_code=201)
def log_delivery(body: s.DeliveryIn, db: Session = Depends(get_db)):
    """Tanker unloading: received = (post − pre dip volume) + sales during unloading, vs invoice."""
    t = _tank(db, body.tank_code)
    if t.kind != "UNDERGROUND":
        raise HTTPException(422, "Tanker deliveries apply to underground tanks")
    if body.post_dip_mm <= body.pre_dip_mm:
        raise HTTPException(422, "Post-unloading dip must be higher than pre-unloading dip")
    if body.post_dip_mm > t.diameter_mm:
        raise HTTPException(422, "Post-unloading dip exceeds tank diameter")
    pre, post = tank_volume(t, body.pre_dip_mm), tank_volume(t, body.post_dip_mm)
    if post > t.capacity * 1.001:
        raise HTTPException(422, "Post-unloading volume exceeds tank capacity")
    received = round(post - pre + body.sales_during, 1)
    var = round(received - body.invoiced_qty, 1)
    pct = round(var / body.invoiced_qty * 100, 2)
    short = pct < -DELIVERY_SHORT_PCT
    dens = (body.invoice_density is not None and body.observed_density is not None
            and abs(body.observed_density - body.invoice_density) > DENSITY_TOLERANCE)
    status = "SHORT_DENSITY" if short and dens else "SHORT" if short else "DENSITY_FLAG" if dens else "OK"
    open_shift = db.query(m.Shift).filter_by(status="OPEN").first()
    d = m.Delivery(tank_id=t.id, shift_id=open_shift.id if open_shift else None, invoice_no=body.invoice_no,
                   supplier=body.supplier, vehicle_no=body.vehicle_no, invoiced_qty=body.invoiced_qty,
                   pre_dip_mm=body.pre_dip_mm, post_dip_mm=body.post_dip_mm, pre_volume=pre, post_volume=post,
                   sales_during=body.sales_during, received_qty=received, variance_qty=var, variance_pct=pct,
                   invoice_density=body.invoice_density, observed_density=body.observed_density, status=status,
                   notes=body.notes)
    db.add(d)
    db.add(m.DipReading(tank_id=t.id, reading_type="ADHOC", dip_mm=body.post_dip_mm, volume=post,
                        shift_id=open_shift.id if open_shift else None))      # refresh stock after unloading
    db.commit(); db.refresh(d)
    return delivery_out(d)


@app.get("/api/deliveries", response_model=list[s.DeliveryOut])
def list_deliveries(limit: int = Query(20, le=100), db: Session = Depends(get_db)):
    rows = db.query(m.Delivery).options(joinedload(m.Delivery.tank)).order_by(m.Delivery.ts.desc()).limit(limit).all()
    return [delivery_out(d) for d in rows]


# ═════════════════════════ 4. Equipment & maintenance (CMMS) ═════════════════════════
def equipment_out(db: Session, e: m.Equipment) -> s.EquipmentOut:
    today = now_ist().date()
    o = s.EquipmentOut.model_validate(e)
    if e.last_service:
        o.next_pm_due = e.last_service + timedelta(days=e.pm_interval_days)
        o.days_to_pm = (o.next_pm_due - today).days
        o.pm_state = "OVERDUE" if o.days_to_pm < 0 else "DUE_SOON" if o.days_to_pm <= PM_DUE_SOON_DAYS else "OK"
    if e.amc_expiry:
        o.amc_days_left = (e.amc_expiry - today).days
        o.amc_state = "EXPIRED" if o.amc_days_left < 0 else "EXPIRING" if o.amc_days_left <= AMC_WARN_DAYS else "OK"
    o.open_tickets = db.query(m.Ticket).filter(m.Ticket.equipment_id == e.id, m.Ticket.status != "RESOLVED").count()
    return o


@app.get("/api/equipment", response_model=list[s.EquipmentOut])
def list_equipment(db: Session = Depends(get_db)):
    return [equipment_out(db, e) for e in db.query(m.Equipment).order_by(m.Equipment.code).all()]


@app.post("/api/equipment", response_model=s.EquipmentOut, status_code=201)
def add_equipment(body: s.EquipmentIn, db: Session = Depends(get_db)):
    if db.query(m.Equipment).filter_by(code=body.code).first():
        raise HTTPException(409, "Equipment code exists")
    e = m.Equipment(**body.model_dump()); db.add(e); db.commit()
    return equipment_out(db, e)


@app.post("/api/equipment/{eid}/service", response_model=s.EquipmentOut)
def mark_serviced(eid: int, body: s.ServiceDone, db: Session = Depends(get_db)):
    """Record a preventive-maintenance visit – pushes the next PM date forward."""
    e = db.get(m.Equipment, eid) or _404("Equipment")
    e.last_service = body.service_date or now_ist().date()
    db.commit()
    return equipment_out(db, e)


@app.get("/api/maintenance/calendar")
def pm_calendar(db: Session = Depends(get_db)):
    """Preventive-maintenance calendar + AMC expiries as one date-sorted list."""
    events = []
    for e in db.query(m.Equipment).all():
        o = equipment_out(db, e)
        if o.next_pm_due:
            events.append({"date": o.next_pm_due, "kind": "PM", "equipment": e.name, "equipment_id": e.id,
                           "state": o.pm_state, "days": o.days_to_pm})
        if o.amc_days_left is not None:
            events.append({"date": e.amc_expiry, "kind": "AMC", "equipment": e.name, "equipment_id": e.id,
                           "state": o.amc_state, "days": o.amc_days_left, "vendor": e.amc_vendor})
    return sorted(events, key=lambda x: x["date"])


def ticket_out(t: m.Ticket) -> s.TicketOut:
    o = s.TicketOut.model_validate(t)
    o.equipment_name = t.equipment.name if t.equipment else None
    o.age_hours = round(((t.resolved_at or now_ist()) - t.opened_at).total_seconds() / 3600, 1)
    return o


@app.post("/api/tickets", response_model=s.TicketOut, status_code=201)
def create_ticket(body: s.TicketIn, db: Session = Depends(get_db)):
    eq = db.get(m.Equipment, body.equipment_id) if body.equipment_id else None
    if body.equipment_id and not eq:
        _404("Equipment")
    t = m.Ticket(**body.model_dump())
    db.add(t); db.flush()
    t.ticket_no = f"TKT-{t.id:04d}"
    t.events.append(m.TicketEvent(from_status=None, to_status="OPEN", note="Ticket raised"))
    if eq:   # a high-priority breakdown takes the equipment out of service
        eq.status = "DOWN" if body.priority in ("HIGH", "CRITICAL") else ("WATCH" if eq.status == "OPERATIONAL" else eq.status)
    db.commit(); db.refresh(t)
    return ticket_out(t)


@app.get("/api/tickets", response_model=list[s.TicketOut])
def list_tickets(status: Optional[s.TicketStatus] = None, db: Session = Depends(get_db)):
    q = db.query(m.Ticket).options(joinedload(m.Ticket.equipment), joinedload(m.Ticket.events))
    if status:
        q = q.filter(m.Ticket.status == status)
    return [ticket_out(t) for t in q.order_by(m.Ticket.opened_at.desc()).all()]


@app.post("/api/tickets/{tid}/advance", response_model=s.TicketOut)
def advance_ticket(tid: int, body: s.TicketAdvance, db: Session = Depends(get_db)):
    """OPEN → TECHNICIAN_ASSIGNED → PARTS_REPLACED → RESOLVED (parts step skippable if no parts needed)."""
    t = db.get(m.Ticket, tid) or _404("Ticket")
    cur, new = TICKET_FLOW.index(t.status), TICKET_FLOW.index(body.to_status)
    skip_ok = body.to_status == "RESOLVED" and cur == 1 and not t.parts_required
    if t.status == "RESOLVED":
        raise HTTPException(409, "Ticket already resolved")
    if new != cur + 1 and not skip_ok:
        raise HTTPException(409, f"Invalid transition {t.status} → {body.to_status}; next step is {TICKET_FLOW[cur + 1]}")
    if body.to_status == "TECHNICIAN_ASSIGNED":
        if not body.technician_name:
            raise HTTPException(422, "technician_name is required")
        t.technician_name, t.technician_phone = body.technician_name, body.technician_phone
    if body.to_status == "PARTS_REPLACED":
        if not body.parts_replaced:
            raise HTTPException(422, "parts_replaced is required")
        t.parts_replaced = body.parts_replaced
    if body.to_status == "RESOLVED":
        t.resolved_at, t.resolution_notes = now_ist(), body.note
        if t.equipment and not db.query(m.Ticket).filter(m.Ticket.equipment_id == t.equipment_id, m.Ticket.id != t.id,
                                                          m.Ticket.status != "RESOLVED").count():
            t.equipment.status = "OPERATIONAL"
    t.events.append(m.TicketEvent(from_status=t.status, to_status=body.to_status, note=body.note))
    t.status = body.to_status
    db.commit(); db.refresh(t)
    return ticket_out(t)


# ═════════════════════════ 5. Compliance tracker ═════════════════════════
def compliance_out(c: m.ComplianceDoc) -> s.ComplianceOut:
    o = s.ComplianceOut.model_validate(c)
    o.days_left = (c.expiry_date - now_ist().date()).days
    o.status = "RED" if o.days_left < 0 else "YELLOW" if o.days_left <= COMPLIANCE_YELLOW_DAYS else "GREEN"
    return o


@app.get("/api/compliance", response_model=list[s.ComplianceOut])
def list_compliance(db: Session = Depends(get_db)):
    return [compliance_out(c) for c in db.query(m.ComplianceDoc).order_by(m.ComplianceDoc.expiry_date).all()]


@app.post("/api/compliance", response_model=s.ComplianceOut, status_code=201)
def add_compliance(body: s.ComplianceIn, db: Session = Depends(get_db)):
    c = m.ComplianceDoc(**body.model_dump()); db.add(c); db.commit()
    return compliance_out(c)


@app.put("/api/compliance/{cid}/renew", response_model=s.ComplianceOut)
def renew_compliance(cid: int, body: s.ComplianceRenew, db: Session = Depends(get_db)):
    c = db.get(m.ComplianceDoc, cid) or _404("Document")
    c.expiry_date = body.expiry_date
    c.number = body.number or c.number
    c.issue_date = body.issue_date or now_ist().date()
    db.commit()
    return compliance_out(c)


@app.delete("/api/compliance/{cid}", status_code=204)
def delete_compliance(cid: int, db: Session = Depends(get_db)):
    db.delete(db.get(m.ComplianceDoc, cid) or _404("Document")); db.commit()


# ═════════════════════════ 6. Alerts & owner dashboard ═════════════════════════
def build_alerts(db: Session) -> list[s.AlertOut]:
    out, now = [], now_ist()
    A = s.AlertOut
    for sh in db.query(m.Shift).filter(m.Shift.status == "CLOSED", m.Shift.day >= now.date() - timedelta(days=7),
                                       m.Shift.variance_status.in_(["SHORT", "OVER"])).all():
        out.append(A(severity="critical" if sh.severity == "critical" else "warning", category="CASH",
                     title=f"Cash {sh.variance_status.lower()} ₹{abs(sh.variance):,.0f} – {sh.shift_name} {sh.day:%d %b}",
                     detail=f"Expected ₹{sh.expected_revenue:,.0f}, collected ₹{sh.total_collections:,.0f}", ts=sh.closed_at))
    for r in (db.query(m.MeterReading).join(m.Shift).filter(m.MeterReading.opening_mismatch.is_(True),
                                                           m.Shift.day >= now.date() - timedelta(days=7)).all()):
        out.append(A(severity="warning", category="TOTALISER", title=f"Totaliser mismatch on {r.nozzle.code}",
                     detail="Opening reading differs from the previous shift's closing", ts=r.shift.opened_at))
    for t in db.query(m.Tank).all():
        ts_ = tank_status(db, t)
        if ts_.status in ("LOW", "DRY_RISK"):
            left = f" – about {ts_.days_left} day(s) left" if ts_.days_left is not None else ""
            out.append(A(severity="critical" if ts_.status == "DRY_RISK" else "warning", category="STOCK",
                         title=f"{t.product.title()} tank {t.code} {'dry-out risk' if ts_.status == 'DRY_RISK' else 'low'}",
                         detail=f"{ts_.stock:,.0f} {ts_.unit} in stock{left}; reorder at {ts_.reorder_level:,.0f}", ts=ts_.last_dip_at))
        if ts_.water_alert:
            out.append(A(severity="critical" if ts_.water_alert == "CRITICAL" else "warning", category="WATER",
                         title=f"Water in tank {t.code}: {ts_.water_mm:.0f} mm", detail="Stop sales from this tank if above limit",
                         ts=ts_.last_dip_at))
    for d in db.query(m.Delivery).filter(m.Delivery.status != "OK", m.Delivery.ts >= now - timedelta(days=14)).all():
        bits = []
        if "SHORT" in d.status: bits.append(f"short {abs(d.variance_qty):,.0f} L ({d.variance_pct}%)")
        if "DENSITY" in d.status: bits.append(f"density {d.observed_density} vs invoice {d.invoice_density}")
        out.append(A(severity="critical" if "SHORT" in d.status else "warning", category="DELIVERY",
                     title=f"Delivery {d.invoice_no} flagged", detail="; ".join(bits), ts=d.ts))
    for c in db.query(m.ComplianceDoc).all():
        co = compliance_out(c)
        if co.status != "GREEN":
            out.append(A(severity="critical" if co.status == "RED" else "warning", category="COMPLIANCE",
                         title=f"{c.title} {'expired' if co.status == 'RED' else 'expiring'}",
                         detail=f"{abs(co.days_left)} day(s) {'ago' if co.days_left < 0 else 'left'} ({c.expiry_date:%d %b %Y})"))
    for e in db.query(m.Equipment).all():
        eo = equipment_out(db, e)
        if eo.pm_state == "OVERDUE":
            out.append(A(severity="warning", category="MAINTENANCE", title=f"{e.name}: preventive service overdue",
                         detail=f"Was due {eo.next_pm_due:%d %b %Y}"))
        if eo.amc_state in ("EXPIRING", "EXPIRED"):
            out.append(A(severity="critical" if eo.amc_state == "EXPIRED" else "warning", category="AMC",
                         title=f"{e.name}: AMC {eo.amc_state.lower()}", detail=f"{e.amc_vendor or 'vendor'} – {e.amc_expiry:%d %b %Y}"))
    for t in db.query(m.Ticket).filter(m.Ticket.status != "RESOLVED", m.Ticket.priority.in_(["HIGH", "CRITICAL"])).all():
        out.append(A(severity="critical" if t.priority == "CRITICAL" else "warning", category="TICKET",
                     title=f"{t.ticket_no}: {t.title}", detail=f"{t.priority} priority, {t.status.replace('_', ' ').title()}",
                     ts=t.opened_at))
    fails = (db.query(m.FaceScanLog).filter(m.FaceScanLog.success.is_(False), m.FaceScanLog.ts >= now - timedelta(hours=24)).count())
    if fails >= 3:
        out.append(A(severity="warning", category="ATTENDANCE", title=f"{fails} failed face scans in 24 h",
                     detail="Possible proxy attendance attempts – review the scan log"))
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda a: (order[a.severity], -(a.ts or datetime(2000, 1, 1)).timestamp()))


@app.get("/api/alerts", response_model=list[s.AlertOut])
def alerts(db: Session = Depends(get_db)):
    return build_alerts(db)


@app.get("/api/dashboard", response_model=s.DashboardOut)
def dashboard(db: Session = Depends(get_db)):
    today = now_ist().date()
    fuel = defaultdict(float)
    rows = (db.query(m.Nozzle.product, func.sum(m.MeterReading.sold_qty)).join(m.MeterReading, m.MeterReading.nozzle_id == m.Nozzle.id)
            .join(m.Shift, m.Shift.id == m.MeterReading.shift_id).filter(m.Shift.day == today, m.Shift.status == "CLOSED")
            .group_by(m.Nozzle.product).all())
    for p, q in rows:
        fuel[p] = round(q or 0, 2)
    for p in ("PETROL", "DIESEL", "CNG"):
        fuel.setdefault(p, 0.0)
    closed = db.query(m.Shift).filter(m.Shift.day == today, m.Shift.status == "CLOSED").all()
    cash, upi, card, credit, exp, var = (sum(getattr(x, f) for x in closed) for f in
                                         ("cash_amount", "upi_amount", "card_amount", "credit_amount", "expected_revenue", "variance"))
    gross = cash + upi + card + credit
    active = [att_out(a) for a in (db.query(m.Attendance).options(joinedload(m.Attendance.staff))
              .filter(m.Attendance.out_time.is_(None), m.Attendance.in_time >= now_ist() - timedelta(hours=SHIFT_LOOKBACK_H)).all())]
    tickets = db.query(m.Ticket).filter(m.Ticket.status != "RESOLVED")
    os_ = db.query(m.Shift).filter_by(status="OPEN").first()
    last7 = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        last7.append({"day": d, "collections": round(sum(x.total_collections for x in db.query(m.Shift).filter(m.Shift.day == d, m.Shift.status == "CLOSED")), 2)})
    return s.DashboardOut(
        as_of=now_ist(), day=today, fuel_sold=dict(fuel), total_fuel_litres=round(fuel["PETROL"] + fuel["DIESEL"], 2),
        cng_kg=fuel["CNG"], expected_revenue=round(exp, 2), gross_collections=round(gross, 2), cash=cash, upi=upi, card=card,
        credit=credit, cash_pct=round(cash / gross * 100, 1) if gross else 0,
        digital_pct=round((upi + card) / gross * 100, 1) if gross else 0, variance_today=round(var, 2),
        outstanding_receivables=round(db.query(func.coalesce(func.sum(m.CreditVoucher.amount), 0)).filter(m.CreditVoucher.settled.is_(False)).scalar(), 2),
        active_staff=[{"name": a.staff_name, "role": a.staff_role, "since": a.in_time, "status": a.status} for a in active],
        active_staff_count=len(active), open_tickets=tickets.count(), critical_tickets=tickets.filter(m.Ticket.priority.in_(["HIGH", "CRITICAL"])).count(),
        open_shift={"id": os_.id, "shift_name": os_.shift_name, "opened_at": os_.opened_at} if os_ else None,
        alert_count=len([a for a in build_alerts(db) if a.severity != "info"]), last7=last7,
        tank_status=[t.model_dump(include={"code", "product", "stock", "fill_pct", "status", "unit", "days_left"}) for t in list_tanks(db)])


# ═════════════════════════ Demo seed data ═════════════════════════
def seed_demo_data(db: Session) -> None:
    """Station hardware/config only – no fake sales or staff (staff must be face-registered)."""
    if db.query(m.Nozzle).count():
        return
    today = now_ist().date()
    for p, r in (("PETROL", 94.69), ("DIESEL", 87.80), ("CNG", 90.00)):
        db.add(m.FuelRate(product=p, rate=r))
    for code, disp, prod in (("D1-N1", "Dispenser 01", "PETROL"), ("D1-N2", "Dispenser 01", "PETROL"),
                             ("D2-N1", "Dispenser 02", "DIESEL"), ("D2-N2", "Dispenser 02", "DIESEL"),
                             ("D3-N1", "CNG Dispenser 03", "CNG"), ("D3-N2", "CNG Dispenser 03", "CNG")):
        db.add(m.Nozzle(code=code, dispenser=disp, product=prod, unit="KG" if prod == "CNG" else "L"))
    db.add_all([
        m.Tank(code="UG-P1", product="PETROL", capacity=20000, diameter_mm=2500, min_stock=3000, dead_stock=300),
        m.Tank(code="UG-D1", product="DIESEL", capacity=25000, diameter_mm=2500, min_stock=4000, dead_stock=400),
        m.Tank(code="CSC-1", product="CNG", kind="CASCADE", capacity=6500, min_stock=1000),
    ])
    db.add_all([
        m.Equipment(code="DSP-01", name="Dispenser 01 (Petrol)", category="DISPENSER", amc_vendor="Tokheim Service",
                    amc_expiry=today + timedelta(days=200), last_service=today - timedelta(days=40), pm_interval_days=90),
        m.Equipment(code="DSP-02", name="Dispenser 02 (Diesel)", category="DISPENSER", amc_vendor="Tokheim Service",
                    amc_expiry=today + timedelta(days=200), last_service=today - timedelta(days=60), pm_interval_days=90),
        m.Equipment(code="CNG-COMP", name="CNG Compressor", category="COMPRESSOR", amc_vendor="Atlas Copco",
                    amc_expiry=today + timedelta(days=22), last_service=today - timedelta(days=85), pm_interval_days=90),
        m.Equipment(code="ATG-1", name="ATG Tank Gauge", category="ATG", amc_vendor="Veeder-Root",
                    amc_expiry=today + timedelta(days=300), last_service=today - timedelta(days=10), pm_interval_days=180),
    ])
    db.add_all([
        m.ComplianceDoc(doc_type="WM_CALIBRATION", title="Weights & Measures dispenser stamping", authority="Legal Metrology",
                        expiry_date=today + timedelta(days=60)),
        m.ComplianceDoc(doc_type="FIRE_NOC", title="Fire Safety NOC", authority="Fire Department", expiry_date=today + timedelta(days=120)),
        m.ComplianceDoc(doc_type="EXPLOSIVES_LICENSE", title="PESO Explosives Licence (Form XIV)", authority="PESO",
                        expiry_date=today + timedelta(days=12)),
        m.ComplianceDoc(doc_type="POLLUTION", title="Pollution Control Board consent", authority="UPPCB", expiry_date=today - timedelta(days=5)),
    ])
    db.commit()


# ═════════════════════════ Serve the frontend (must be last) ═════════════════════════
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
