from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.security import get_current_user, require_doctor, require_any
from app.services.db_service import (
    get_patients, get_patient_by_id, get_patient_by_user_id,
    get_visit_history, get_latest_vitals, get_patient_reports,
    get_patient_medications, log_audit
)
from typing import Optional

router = APIRouter(prefix="/patients", tags=["Patients"])

@router.get("")
async def list_patients(
    tier: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(require_doctor),
    db: AsyncSession = Depends(get_db),
):
    return await get_patients(
        db,
        doctor_user_id=str(current_user["user_id"]),
        role=current_user["role"],
        tier=tier, search=search,
        page=page, page_size=page_size,
    )

@router.get("/me")
async def my_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user["role"] != "patient":
        raise HTTPException(403, "Only patients can access this endpoint.")
    patient = await get_patient_by_user_id(db, str(current_user["user_id"]))
    if not patient:
        raise HTTPException(404, "Patient profile not found.")
    return {"status": "success", "patient": patient}

@router.get("/{patient_id}")
async def get_patient(
    patient_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user["role"] == "patient":
        own = await get_patient_by_user_id(db, str(current_user["user_id"]))
        if not own or str(own["patient_id"]) != patient_id:
            raise HTTPException(403, "Access denied.")
    patient = await get_patient_by_id(db, patient_id)
    if not patient:
        raise HTTPException(404, "Patient not found.")
    return {"status": "success", "patient": patient}

@router.get("/{patient_id}/history")
async def patient_history(
    patient_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    visits = await get_visit_history(db, patient_id)
    return {"status": "success", "visits": visits, "count": len(visits)}

@router.get("/{patient_id}/vitals")
async def patient_vitals(
    patient_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vitals = await get_latest_vitals(db, patient_id)
    if not vitals:
        raise HTTPException(404, "No vitals on record.")
    return {"status": "success", "vitals": vitals}

@router.get("/{patient_id}/reports")
async def patient_reports(
    patient_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reports = await get_patient_reports(db, patient_id)
    return {"status": "success", "reports": reports, "count": len(reports)}

@router.get("/{patient_id}/medications")
async def patient_medications(
    patient_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meds = await get_patient_medications(db, patient_id)
    return {"status": "success", "medications": meds, "count": len(meds)}
