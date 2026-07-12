from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database import get_db
from app.security import get_current_user
from app.schemas import DiagnoseRequest
from app.services.ml_client import ml_diagnose, ml_readmission
from app.services.db_service import (
    save_diagnostic_run, get_visit_history,
    get_latest_vitals, log_audit
)
import logging
import httpx


def _to_num(v, default=0.0):
    try:
        if v is None or v == "":
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _local_diagnostic_fallback(vitals: dict, patient_info: dict, history: list) -> dict:
    age = _to_num(vitals.get("age", patient_info.get("age", 45)), 45)
    sbp = _to_num(vitals.get("blood_pressure_sys", vitals.get("blood_pressure", 120)), 120)
    dbp = _to_num(vitals.get("blood_pressure_dia", 80), 80)
    hr = _to_num(vitals.get("heart_rate", 72), 72)
    spo2 = _to_num(vitals.get("spo2", 98), 98)
    glucose = _to_num(vitals.get("glucose", 95), 95)
    hba1c = _to_num(vitals.get("hba1c", 5.4), 5.4)
    bmi = _to_num(vitals.get("bmi", 24), 24)
    egfr = _to_num(vitals.get("egfr", 90), 90)
    creatinine = _to_num(vitals.get("creatinine", 1.0), 1.0)
    chol = _to_num(vitals.get("cholesterol_total", 180), 180)
    ldl = _to_num(vitals.get("ldl", 110), 110)

    heart = 8.0
    if sbp >= 140:
        heart += 18
    elif sbp >= 130:
        heart += 9
    if hr >= 110:
        heart += 10
    elif hr >= 100:
        heart += 5
    if chol >= 240:
        heart += 12
    elif chol >= 200:
        heart += 6
    if ldl >= 160:
        heart += 10
    elif ldl >= 130:
        heart += 5
    if age >= 65:
        heart += 8
    elif age >= 50:
        heart += 4
    if spo2 < 92:
        heart += 10
    elif spo2 < 95:
        heart += 4

    metabolic = 6.0
    if glucose >= 126:
        metabolic += 22
    elif glucose >= 100:
        metabolic += 10
    if hba1c >= 6.5:
        metabolic += 22
    elif hba1c >= 5.7:
        metabolic += 11
    if bmi >= 35:
        metabolic += 14
    elif bmi >= 30:
        metabolic += 7
    if age >= 55:
        metabolic += 5
    elif age >= 45:
        metabolic += 3

    renal = 4.0
    if egfr < 15:
        renal += 55
    elif egfr < 30:
        renal += 40
    elif egfr < 45:
        renal += 25
    elif egfr < 60:
        renal += 15
    elif egfr < 75:
        renal += 7
    if creatinine > 1.5:
        renal += 10
    elif creatinine > 1.2:
        renal += 5
    if sbp >= 140:
        renal += 5

    heart = max(0, min(97, round(heart, 1)))
    metabolic = max(0, min(97, round(metabolic, 1)))
    renal = max(0, min(97, round(renal, 1)))

    health_score = round(max(5, min(100, 100 - (heart * 0.4 + metabolic * 0.35 + renal * 0.25) * 0.65)), 1)
    if health_score <= 40:
        tier = "Critical"
    elif health_score <= 65:
        tier = "Borderline"
    else:
        tier = "Stable"

    flags = []
    if sbp >= 140 or dbp >= 90:
        flags.append({"domain": "cardiovascular", "severity": "critical", "message": "Elevated blood pressure."})
    if hba1c >= 6.5 or glucose >= 126:
        flags.append({"domain": "metabolic", "severity": "critical", "message": "Hyperglycemia risk indicators detected."})
    if egfr < 60:
        flags.append({"domain": "renal", "severity": "critical", "message": "Reduced renal filtration trend."})

    urgency = "Routine"
    if tier == "Critical":
        urgency = "Urgent"
    elif tier == "Borderline":
        urgency = "Semi-Urgent"

    return {
        "health_score": health_score,
        "risk_tier": tier,
        "domain_scores": {
            "cardiovascular": heart,
            "metabolic": metabolic,
            "renal": renal,
        },
        "raw_risks": {
            "heart_pct": heart,
            "diabetes_pct": metabolic,
            "kidney_pct": renal,
        },
        "adaptive_weights": {"heart": 0.40, "diabetes": 0.35, "kidney": 0.25},
        "clinical_flags": flags,
        "triage": {
            "department": "Internal Medicine",
            "urgency": urgency,
            "urgency_note": "Generated via local fallback due to temporary ML outage.",
        },
        "trend_analysis": {"trend": "insufficient_data", "history_points": len(history or [])},
        "latency_ms": 0.0,
        "model_version": "local-fallback",
        "ml_fallback_mode": "local_heuristic",
    }

router = APIRouter(prefix="/diagnostics", tags=["Diagnostics"])
logger = logging.getLogger("OmniSensus.Diagnostics")

@router.post("")
async def run_diagnostic(
    body: DiagnoseRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doctor_id = None
    if current_user["role"] == "doctor":
        r = await db.execute(
            text("SELECT doctor_id FROM doctors WHERE user_id = :uid"),
            {"uid": str(current_user["user_id"])}
        )
        doc = r.mappings().first()
        if doc:
            doctor_id = str(doc["doctor_id"])

    vitals_dict      = body.vitals.to_dict()
    patient_info_dict= body.patient_info.model_dump(exclude_none=True) if body.patient_info else {}
    history          = body.history or []

    try:
        ml = await ml_diagnose(
            body.patient_id, vitals_dict, patient_info_dict, history
        )
    except Exception as e:
        logger.error(f"ML API error: {e}")
        ml = _local_diagnostic_fallback(vitals_dict, patient_info_dict, history)
        logger.warning("Using local heuristic diagnostic fallback for patient %s", body.patient_id)

    run_id = await save_diagnostic_run(
        db, body.patient_id, doctor_id, ml, vitals_dict
    )

    await log_audit(
        db, str(current_user["user_id"]), current_user["username"],
        current_user["role"], "DIAGNOSTIC_RUN", body.patient_id,
        "success", f"Score:{ml.get('health_score')} Tier:{ml.get('risk_tier')}",
        ip=request.client.host
    )

    logger.info(f"[DIAGNOSTIC] {body.patient_id} Score:{ml.get('health_score')} run_id:{run_id}")
    return {"status": "success", **ml, "run_id": run_id}

@router.get("/{run_id}")
async def get_run(
    run_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT dr.*, ds.cardiovascular, ds.metabolic, ds.renal,
               d.full_name AS doctor_name
        FROM diagnostic_runs dr
        LEFT JOIN domain_scores ds ON dr.run_id = ds.run_id
        LEFT JOIN doctors       d  ON dr.doctor_id = d.doctor_id
        WHERE dr.run_id = :rid
    """), {"rid": run_id})
    run = r.mappings().first()
    if not run:
        raise HTTPException(404, "Diagnostic run not found.")

    flags_r    = await db.execute(text("SELECT * FROM clinical_flags WHERE run_id = :rid ORDER BY severity"), {"rid": run_id})
    insights_r = await db.execute(text("SELECT * FROM ai_insights   WHERE run_id = :rid ORDER BY rank_position"), {"rid": run_id})
    vitals_r   = await db.execute(text("SELECT * FROM patient_vitals WHERE run_id = :rid"), {"rid": run_id})

    return {
        "status":   "success",
        "run":      dict(run),
        "flags":    [dict(f) for f in flags_r.mappings().all()],
        "insights": [dict(i) for i in insights_r.mappings().all()],
        "vitals":   dict(vitals_r.mappings().first() or {}),
    }

@router.get("/patient/{patient_id}/latest")
async def latest_run(
    patient_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT dr.*, ds.cardiovascular, ds.metabolic, ds.renal
        FROM diagnostic_runs dr
        LEFT JOIN domain_scores ds ON dr.run_id = ds.run_id
        WHERE dr.patient_id = :pid
        ORDER BY dr.created_at DESC LIMIT 1
    """), {"pid": patient_id})
    run = r.mappings().first()
    if not run:
        raise HTTPException(404, "No diagnostic runs found.")
    run_id     = str(run["run_id"])
    flags_r    = await db.execute(text("SELECT * FROM clinical_flags WHERE run_id = :rid"), {"rid": run_id})
    insights_r = await db.execute(text("SELECT * FROM ai_insights   WHERE run_id = :rid ORDER BY rank_position"), {"rid": run_id})
    return {
        "status":   "success",
        "run":      dict(run),
        "flags":    [dict(f) for f in flags_r.mappings().all()],
        "insights": [dict(i) for i in insights_r.mappings().all()],
    }

@router.post("/patient/{patient_id}/readmission-risk")
async def readmission(
    patient_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vitals = await get_latest_vitals(db, patient_id)
    try:
        result = await ml_readmission(patient_id, vitals or {}, {})
    except Exception as e:
        raise HTTPException(503, f"ML service unavailable: {str(e)}")
    return {"status": "success", **result}
