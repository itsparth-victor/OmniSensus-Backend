"""
OmniSensus Backend · app/routers/doctors.py
Doctor profile endpoints — GET/PUT for doctor's own profile,
doctor list for dropdowns, availability status updates.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database import get_db
from app.security import get_current_user, require_admin, require_doctor
from app.services.db_service import log_audit
from typing import Optional
from datetime import datetime, timezone

router = APIRouter(prefix="/doctors", tags=["Doctors"])


# ── DOCTOR: OWN PROFILE ───────────────────────────────────────────────────
@router.get("/me")
async def my_doctor_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    if current_user["role"] != "doctor":
        raise HTTPException(403, "Only doctors can access this endpoint.")

    r = await db.execute(text("""
        SELECT d.*,
               u.email, u.username, u.mfa_enabled,
               u.last_login_at, u.status AS account_status,
               u.email_notifications, u.push_notifications,
               u.theme, u.avatar_url
        FROM doctors d
        JOIN users u ON d.user_id = u.user_id
        WHERE d.user_id = :uid
    """), {"uid": str(current_user["user_id"])})
    doc = r.mappings().first()
    if not doc:
        raise HTTPException(404, "Doctor profile not found.")

    # Stats
    stats = await db.execute(text("""
        SELECT
            COUNT(DISTINCT dr.patient_id)                               AS patients_scanned,
            COUNT(dr.run_id)                                            AS total_runs,
            COUNT(a.appointment_id) FILTER (
                WHERE a.status IN ('booked','confirmed')
                AND a.scheduled_at >= NOW()
            )                                                           AS upcoming_appointments,
            COUNT(a.appointment_id) FILTER (
                WHERE a.status = 'completed'
                AND a.completed_at >= NOW() - INTERVAL '30 days'
            )                                                           AS completed_last_30d
        FROM doctors d
        LEFT JOIN diagnostic_runs dr ON d.doctor_id = dr.doctor_id
        LEFT JOIN appointments    a  ON d.doctor_id = a.doctor_id
        WHERE d.user_id = :uid
    """), {"uid": str(current_user["user_id"])})
    stat_row = stats.mappings().first()

    return {
        "status":  "success",
        "doctor":  dict(doc),
        "stats":   dict(stat_row) if stat_row else {},
    }


# ── DOCTOR: UPDATE OWN PROFILE ────────────────────────────────────────────
@router.put("/me")
async def update_my_profile(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    if current_user["role"] != "doctor":
        raise HTTPException(403, "Only doctors can update their profile.")

    allowed_doctor = {
        "phone", "work_email", "department", "hospital",
        "bio", "languages", "availability_status"
    }
    allowed_user = {
        "email_notifications", "push_notifications", "theme"
    }

    doc_updates  = {k: v for k, v in body.items() if k in allowed_doctor}
    user_updates = {k: v for k, v in body.items() if k in allowed_user}
    now          = datetime.now(timezone.utc)

    if doc_updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in doc_updates)
        doc_updates["uid"] = str(current_user["user_id"])
        doc_updates["now"] = now
        await db.execute(
            text(f"UPDATE doctors SET {set_clause}, updated_at = :now "
                 f"WHERE user_id = :uid"),
            doc_updates
        )

    if user_updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in user_updates)
        user_updates["uid"] = str(current_user["user_id"])
        user_updates["now"] = now
        await db.execute(
            text(f"UPDATE users SET {set_clause}, updated_at = :now "
                 f"WHERE user_id = :uid"),
            user_updates
        )

    await log_audit(db, str(current_user["user_id"]), current_user["username"],
                    "doctor", "PROFILE_UPDATE", "", "success", str(list(body.keys())))
    return {"status": "success", "message": "Profile updated."}


# ── DOCTOR: UPDATE AVAILABILITY ───────────────────────────────────────────
@router.put("/me/availability")
async def update_availability(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    if current_user["role"] != "doctor":
        raise HTTPException(403, "Only doctors can update availability.")

    avail = body.get("availability_status")
    if avail not in ("available", "busy", "on_leave", "off_duty"):
        raise HTTPException(400, "Invalid availability_status.")

    await db.execute(text("""
        UPDATE doctors SET availability_status = :avail, updated_at = :now
        WHERE user_id = :uid
    """), {"avail": avail, "now": datetime.now(timezone.utc),
           "uid": str(current_user["user_id"])})

    return {"status": "success", "message": f"Availability set to {avail}."}


# ── DOCTORS LIST (for dropdowns) ──────────────────────────────────────────
@router.get("")
async def list_doctors(
    department:  Optional[str] = Query(None),
    available:   Optional[bool]= Query(None),
    page:        int           = Query(1, ge=1),
    page_size:   int           = Query(20, ge=1, le=100),
    current_user: dict         = Depends(require_doctor),
    db: AsyncSession           = Depends(get_db),
):
    where  = "WHERE 1=1"
    params = {}

    if department:
        where += " AND d.department ILIKE :dept"
        params["dept"] = f"%{department}%"

    if available is not None:
        if available:
            where += " AND d.availability_status = 'available'"
        else:
            where += " AND d.availability_status != 'available'"

    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})

    rows = await db.execute(text(f"""
        SELECT d.doctor_id, d.full_name, d.title, d.specialisation,
               d.department, d.hospital, d.phone, d.work_email,
               d.availability_status, d.total_patients, d.total_diagnostics,
               u.status AS account_status
        FROM doctors d
        JOIN users u ON d.user_id = u.user_id
        {where}
        ORDER BY d.full_name
        LIMIT :lim OFFSET :off
    """), params)

    doctors = [dict(r) for r in rows.mappings().all()]

    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    cr = await db.execute(
        text(f"SELECT COUNT(*) FROM doctors d JOIN users u ON d.user_id=u.user_id {where}"),
        count_params
    )
    total = cr.scalar()

    return {
        "status":    "success",
        "doctors":   doctors,
        "total":     total,
        "page":      page,
        "page_size": page_size,
    }


# ── DOCTOR BY ID ──────────────────────────────────────────────────────────
@router.get("/{doctor_id}")
async def get_doctor(
    doctor_id: str,
    current_user: dict = Depends(require_doctor),
    db: AsyncSession   = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT d.*, u.email, u.status AS account_status,
               u.last_login_at
        FROM doctors d
        JOIN users u ON d.user_id = u.user_id
        WHERE d.doctor_id = :did
    """), {"did": doctor_id})
    doc = r.mappings().first()
    if not doc:
        raise HTTPException(404, "Doctor not found.")
    return {"status": "success", "doctor": dict(doc)}


# ── DOCTOR WORKLOAD (admin view) ───────────────────────────────────────────
@router.get("/{doctor_id}/workload")
async def doctor_workload(
    doctor_id: str,
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT * FROM v_doctor_workload WHERE doctor_id = :did
    """), {"did": doctor_id})
    row = r.mappings().first()
    if not row:
        raise HTTPException(404, "Doctor not found.")
    return {"status": "success", "workload": dict(row)}