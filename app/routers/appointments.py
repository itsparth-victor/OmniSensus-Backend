from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database import get_db
from app.security import get_current_user, require_doctor
from app.schemas import AppointmentCreate, AppointmentStatusUpdate
from app.services.db_service import (
    get_appointments, create_appointment,
    create_notification, log_audit
)
from datetime import datetime, timezone

router = APIRouter(prefix="/appointments", tags=["Appointments"])

@router.get("")
async def list_appointments(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    appts = await get_appointments(
        db, current_user["role"], str(current_user["user_id"])
    )
    return {"status": "success", "appointments": appts, "count": len(appts)}

@router.post("")
async def book_appointment(
    body: AppointmentCreate,
    current_user: dict = Depends(require_doctor),
    db: AsyncSession = Depends(get_db),
):
    appt_id = await create_appointment(db, body.model_dump())
    user_r  = await db.execute(
        text("SELECT user_id FROM patients WHERE patient_id = :pid"),
        {"pid": str(body.patient_id)}
    )
    pat_user = user_r.mappings().first()
    if pat_user:
        await create_notification(
            db, str(pat_user["user_id"]),
            "Appointment Booked",
            f"Your {body.type} appointment has been scheduled.",
        )
    return {"status": "success", "appointment_id": appt_id, "message": "Appointment booked."}

@router.put("/{appt_id}/status")
async def update_status(
    appt_id: str,
    body: AppointmentStatusUpdate,
    current_user: dict = Depends(require_doctor),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    if body.status == "completed":
        await db.execute(text("""
            UPDATE appointments SET status = :s, completed_at = :now, updated_at = :now
            WHERE appointment_id = :aid
        """), {"s": body.status, "now": now, "aid": appt_id})
    elif body.status == "cancelled":
        await db.execute(text("""
            UPDATE appointments SET status = :s, cancelled_at = :now,
            cancellation_reason = :reason, updated_at = :now
            WHERE appointment_id = :aid
        """), {"s": body.status, "now": now, "reason": body.reason, "aid": appt_id})
    else:
        await db.execute(text("""
            UPDATE appointments SET status = :s, updated_at = :now
            WHERE appointment_id = :aid
        """), {"s": body.status, "now": now, "aid": appt_id})
    return {"status": "success", "message": "Status updated."}
