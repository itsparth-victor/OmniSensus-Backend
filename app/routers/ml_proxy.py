from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.security import get_current_user
from app.config import settings
from app.services.ml_client import (
    call_ml, ml_chat, ml_ask, ml_report
)
from datetime import datetime, timezone
import httpx
import logging
import os
import re
from typing import Optional, Tuple

router = APIRouter(prefix="/ml", tags=["ML Proxy"])
logger = logging.getLogger("OmniSensus.MLProxy")
LOCAL_REPORT_CACHE = os.getenv("OMNI_LOCAL_REPORT_CACHE", "false").strip().lower() in ("1", "true", "yes", "on")


def _contains_any(text_value: str, terms: Tuple[str, ...]) -> bool:
    return any(term in text_value for term in terms)


def _detect_role_scoped_intent(prompt: str) -> Optional[str]:
    q = (prompt or "").strip().lower()
    if not q:
        return None

    count_terms = (
        "how many", "number of", "count", "total", "panel size", "do i have"
    )

    if "patient" in q and _contains_any(q, count_terms):
        return "patient_count"

    if (
        ("appointment" in q or "appointments" in q)
        and _contains_any(q, count_terms)
    ):
        return "appointment_count"

    if (
        ("notification" in q or "notifications" in q or "unread" in q)
        and _contains_any(q, count_terms + ("unread", "pending"))
    ):
        return "notification_count"

    if _contains_any(q, (
        "what can i access", "what can i see", "my access", "access level",
        "my role", "what is my role", "who am i"
    )):
        return "access_scope"

    return None


async def _doctor_id_for_user(db: AsyncSession, user_id: str) -> Optional[str]:
    r = await db.execute(
        text("SELECT doctor_id FROM doctors WHERE user_id = :uid LIMIT 1"),
        {"uid": user_id},
    )
    row = r.mappings().first()
    if not row or not row.get("doctor_id"):
        return None
    return str(row["doctor_id"])


async def _patient_id_for_user(db: AsyncSession, user_id: str) -> Optional[str]:
    r = await db.execute(
        text("SELECT patient_id FROM patients WHERE user_id = :uid LIMIT 1"),
        {"uid": user_id},
    )
    row = r.mappings().first()
    if not row or not row.get("patient_id"):
        return None
    return str(row["patient_id"])


async def _count_scoped_patients(db: AsyncSession, role: str, user_id: str) -> int:
    if role == "admin":
        c = await db.execute(text("SELECT COUNT(*) FROM patients"))
        return int(c.scalar() or 0)

    if role == "doctor":
        doctor_id = await _doctor_id_for_user(db, user_id)
        if not doctor_id:
            return 0
        c = await db.execute(
            text("SELECT COUNT(*) FROM patients WHERE primary_doctor_id = :did"),
            {"did": doctor_id},
        )
        return int(c.scalar() or 0)

    patient_id = await _patient_id_for_user(db, user_id)
    return 1 if patient_id else 0


async def _count_scoped_appointments(db: AsyncSession, role: str, user_id: str) -> int:
    if role == "admin":
        c = await db.execute(text("""
            SELECT COUNT(*)
            FROM appointments
            WHERE scheduled_at >= NOW()
              AND COALESCE(status, 'booked') != 'cancelled'
        """))
        return int(c.scalar() or 0)

    if role == "doctor":
        doctor_id = await _doctor_id_for_user(db, user_id)
        if not doctor_id:
            return 0
        c = await db.execute(text("""
            SELECT COUNT(*)
            FROM appointments
            WHERE doctor_id = :did
              AND scheduled_at >= NOW()
              AND COALESCE(status, 'booked') != 'cancelled'
        """), {"did": doctor_id})
        return int(c.scalar() or 0)

    patient_id = await _patient_id_for_user(db, user_id)
    if not patient_id:
        return 0
    c = await db.execute(text("""
        SELECT COUNT(*)
        FROM appointments
        WHERE patient_id = :pid
          AND scheduled_at >= NOW()
          AND COALESCE(status, 'booked') != 'cancelled'
    """), {"pid": patient_id})
    return int(c.scalar() or 0)


async def _count_unread_notifications(db: AsyncSession, user_id: str) -> int:
    c = await db.execute(text("""
        SELECT COUNT(*)
        FROM notifications
        WHERE user_id = :uid
          AND is_dismissed = FALSE
          AND COALESCE(is_read, FALSE) = FALSE
    """), {"uid": user_id})
    return int(c.scalar() or 0)


def _access_scope_message(role: str) -> str:
    if role == "doctor":
        return (
            "You are signed in as doctor. You can view your assigned patient panel, "
            "run diagnostics, review patient history, and manage appointments."
        )
    if role == "admin":
        return (
            "You are signed in as admin. You can view system-wide patient, appointment, "
            "and operations data across the platform."
        )
    return (
        "You are signed in as patient. You can access your own profile, vitals, reports, "
        "appointments, and notifications."
    )


async def _role_scoped_chat_answer(prompt: str, current_user: dict, db: AsyncSession) -> Optional[dict]:
    intent = _detect_role_scoped_intent(prompt)
    if not intent:
        return None

    role = str(current_user.get("role") or "patient").strip().lower()
    user_id = str(current_user.get("user_id") or "")

    if intent == "access_scope":
        return {
            "response": _access_scope_message(role),
            "mode": "role_scoped_db",
            "intent": intent,
            "role": role,
        }

    if intent == "patient_count":
        total = await _count_scoped_patients(db, role, user_id)
        if role == "doctor":
            response = f"You currently have {total} patient{'s' if total != 1 else ''} in your panel."
        elif role == "admin":
            response = f"System-wide patient count is {total}."
        else:
            response = (
                "You can access your own profile only. "
                f"Linked patient profile count: {total}."
            )
        return {
            "response": response,
            "mode": "role_scoped_db",
            "intent": intent,
            "role": role,
            "count": total,
        }

    if intent == "appointment_count":
        total = await _count_scoped_appointments(db, role, user_id)
        if role == "doctor":
            response = f"You have {total} upcoming appointment{'s' if total != 1 else ''}."
        elif role == "admin":
            response = f"There are {total} upcoming appointments across the system."
        else:
            response = f"You have {total} upcoming appointment{'s' if total != 1 else ''}."
        return {
            "response": response,
            "mode": "role_scoped_db",
            "intent": intent,
            "role": role,
            "count": total,
        }

    if intent == "notification_count":
        total = await _count_unread_notifications(db, user_id)
        response = f"You currently have {total} unread notification{'s' if total != 1 else ''}."
        return {
            "response": response,
            "mode": "role_scoped_db",
            "intent": intent,
            "role": role,
            "count": total,
        }

    return None


@router.post("/chat")
async def chat(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    try:
        prompt = body.get("prompt") or body.get("question", "")
        scoped = await _role_scoped_chat_answer(prompt, current_user, db)
        if scoped:
            return {"status": "success", **scoped}

        result = await ml_chat(
            prompt       = prompt,
            session_id   = body.get("session_id", str(current_user["user_id"])),
            patient_id   = body.get("patient_id"),
            requester_role = current_user.get("role", "patient"),
            vitals       = body.get("vitals", {}),
            patient_info = body.get("patient_info", {}),
            history      = body.get("history", []),
            ml_result    = body.get("ml_result", {}),
        )
        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"ML chat error: {e}")
        raise HTTPException(503, f"ML chat service unavailable: {str(e)}")


@router.post("/chat/clear")
async def clear_chat(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await call_ml("POST", "/chat/clear", {
            "session_id": body.get("session_id", str(current_user["user_id"]))
        })
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(503, str(e))


@router.post("/ask")
async def ask(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    try:
        question = body.get("question", "")
        scoped = await _role_scoped_chat_answer(question, current_user, db)
        if scoped:
            return {"status": "success", "answer": scoped["response"], **scoped}

        result = await ml_ask(
            question     = question,
            patient_id   = body.get("patient_id"),
            requester_role = current_user.get("role", "patient"),
            vitals       = body.get("vitals", {}),
            patient_info = body.get("patient_info", {}),
            history      = body.get("history", []),
            ml_result    = body.get("ml_result", {}),
        )
        # Fallback: if 'answer' is missing or empty, add a default message
        if not result or not result.get("answer"):
            result["answer"] = "No answer available from ML model. Please try again later."
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(503, str(e))


@router.post("/report/generate")
async def generate_report(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    patient_id  = body.get("patient_id")
    run_id      = body.get("run_id")
    doctor_name = body.get("doctor_name", "Dr. R. Sharma")

    if not patient_id:
        raise HTTPException(400, "patient_id required.")

    # Fetch patient info from DB if not provided
    patient_info = body.get("patient_info", {})
    if not patient_info:
        r = await db.execute(text("""
            SELECT p.full_name, p.gender, p.blood_group, p.bmi,
                   DATE_PART('year', AGE(p.date_of_birth))::INT AS age,
                   d.full_name AS doctor_name
            FROM patients p
            LEFT JOIN doctors d ON p.primary_doctor_id = d.doctor_id
            WHERE p.patient_id = :pid
        """), {"pid": patient_id})
        row = r.mappings().first()
        if row:
            patient_info = dict(row)
            if not doctor_name or doctor_name == "Dr. R. Sharma":
                doctor_name = row.get("doctor_name") or doctor_name

    # Fetch latest run if risk_data not passed
    risk_data = body.get("risk_data", {})
    if not risk_data and run_id:
        r = await db.execute(text("""
            SELECT dr.*, ds.cardiovascular, ds.metabolic, ds.renal,
                   pv.glucose, pv.hba1c, pv.egfr, pv.creatinine,
                   pv.blood_pressure_sys, pv.blood_pressure_dia,
                   pv.cholesterol_total, pv.bmi AS vitals_bmi, pv.spo2
            FROM diagnostic_runs dr
            LEFT JOIN domain_scores  ds ON dr.run_id = ds.run_id
            LEFT JOIN patient_vitals pv ON dr.run_id = pv.run_id
            WHERE dr.run_id = :rid
        """), {"rid": run_id})
        row = r.mappings().first()
        if row:
            rd = dict(row)
            risk_data = {
                "health_score":  rd.get("health_score"),
                "risk_tier":     rd.get("risk_tier"),
                "domain_scores": {
                    "cardiovascular": rd.get("cardiovascular"),
                    "metabolic":      rd.get("metabolic"),
                    "renal":          rd.get("renal"),
                },
                "raw_risks": {
                    "heart_pct":    rd.get("heart_risk_pct"),
                    "diabetes_pct": rd.get("diabetes_risk_pct"),
                    "kidney_pct":   rd.get("kidney_risk_pct"),
                },
                "triage": {
                    "department":   rd.get("recommended_dept"),
                    "urgency":      rd.get("urgency"),
                    "urgency_note": rd.get("urgency_note"),
                },
                "vitals": {
                    "glucose":             rd.get("glucose"),
                    "hba1c":               rd.get("hba1c"),
                    "egfr":                rd.get("egfr"),
                    "creatinine":          rd.get("creatinine"),
                    "blood_pressure_sys":  rd.get("blood_pressure_sys"),
                    "blood_pressure_dia":  rd.get("blood_pressure_dia"),
                    "cholesterol_total":   rd.get("cholesterol_total"),
                    "bmi":                 rd.get("vitals_bmi"),
                    "spo2":                rd.get("spo2"),
                },
            }

            # Fetch clinical flags
            fr = await db.execute(text(
                "SELECT domain, severity, message FROM clinical_flags WHERE run_id = :rid"
            ), {"rid": run_id})
            risk_data["clinical_flags"] = [dict(f) for f in fr.mappings().all()]

            # Fetch AI insights
            ir = await db.execute(text(
                "SELECT feature_name, shap_value FROM ai_insights WHERE run_id = :rid ORDER BY rank_position"
            ), {"rid": run_id})
            risk_data["ai_insights"] = {
                row["feature_name"]: float(row["shap_value"])
                for row in ir.mappings().all()
            }

    try:
        # Get doctor_id for saving report
        doctor_id = None
        if current_user.get("role") == "doctor":
            dr = await db.execute(
                text("SELECT doctor_id FROM doctors WHERE user_id = :uid"),
                {"uid": str(current_user["user_id"])}
            )
            doc = dr.mappings().first()
            if doc:
                doctor_id = str(doc["doctor_id"])

        result = await ml_report(
            patient_id   = patient_id,
            patient_info = patient_info,
            risk_data    = risk_data,
            insights     = risk_data.get("ai_insights", {}),
            doctor_name  = doctor_name,
            run_id       = run_id,
            doctor_id    = doctor_id,
        )

        filename = result.get("filename")

        # ── Save report to diagnostic_reports table ────────────────
        if filename:
            resolved_run_id = run_id
            if not resolved_run_id:
                latest_run = await db.execute(text("""
                    SELECT run_id
                    FROM diagnostic_runs
                    WHERE patient_id = :pid
                    ORDER BY created_at DESC
                    LIMIT 1
                """), {"pid": patient_id})
                lrr = latest_run.mappings().first()
                resolved_run_id = str(lrr["run_id"]) if lrr and lrr.get("run_id") else None

            if resolved_run_id:
                try:
                    await db.execute(text("""
                        INSERT INTO diagnostic_reports
                            (run_id, patient_id, doctor_id, filename, file_path,
                             report_type, model_version, generated_at)
                        VALUES (:rid, :pid, :did, :fname, :fpath, 'full_diagnostic', :mv, :now)
                        ON CONFLICT (filename) DO NOTHING
                    """), {
                        "rid":   resolved_run_id,
                        "pid":   patient_id,
                        "did":   doctor_id,
                        "fname": filename,
                        "fpath": f"/exports/reports/{filename}",
                        "mv":    "3.0.1",
                        "now":   datetime.now(timezone.utc),
                    })
                except IntegrityError as e:
                    logger.warning(
                        "Skipping diagnostic_reports insert for %s due to integrity error: %s",
                        patient_id,
                        e,
                    )
            else:
                logger.warning(
                    "Skipping diagnostic_reports insert for %s because no run_id could be resolved",
                    patient_id,
                )

            # Best effort: fetch generated PDF once and cache locally.
            if LOCAL_REPORT_CACHE:
                try:
                    cache_dir = os.path.join(os.getcwd(), "exports", "reports")
                    os.makedirs(cache_dir, exist_ok=True)
                    local_path = os.path.join(cache_dir, os.path.basename(filename))
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        rf = await client.get(
                            f"{settings.ML_API_URL.rstrip('/')}/api/v1/report/download/{os.path.basename(filename)}",
                            headers={"X-API-Key": settings.ML_API_KEY},
                        )
                        if rf.status_code < 400:
                            with open(local_path, "wb") as f:
                                f.write(rf.content)
                except Exception as e:
                    logger.warning(f"Generated report could not be cached locally: {e}")

        return {"status": "success", **result}

    except Exception as e:
        logger.error(f"Report generation error: {e}")
        raise HTTPException(503, f"Report service unavailable: {str(e)}")


@router.get("/report/download/{filename}")
async def download_report_proxy(
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    safe_name = os.path.basename(filename)
    base = settings.ML_API_URL.rstrip('/')
    cache_dir = os.path.join(os.getcwd(), "exports", "reports")
    local_path = os.path.join(cache_dir, safe_name)

    # Serve locally cached PDFs first to avoid repeated upstream fetches.
    if LOCAL_REPORT_CACHE and os.path.exists(local_path):
        return FileResponse(local_path, media_type="application/pdf", filename=safe_name)

    def ml_url(name: str) -> str:
        return f"{base}/api/v1/report/download/{name}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {"X-API-Key": settings.ML_API_KEY}
            r = await client.get(ml_url(safe_name), headers=headers)

            # If requested filename is stale/missing in ML storage, try the latest
            # filename with the same report prefix from diagnostic_reports.
            if r.status_code == 404:
                m = re.match(r"^(OmniReport_[^_]+_)\\d{8}\\.pdf$", safe_name, re.IGNORECASE)
                if m:
                    prefix = f"{m.group(1)}%"
                    qr = await db.execute(text("""
                        SELECT filename
                        FROM diagnostic_reports
                        WHERE filename ILIKE :prefix
                        ORDER BY generated_at DESC NULLS LAST
                        LIMIT 8
                    """), {"prefix": prefix})
                    candidates = [
                        row["filename"] for row in qr.mappings().all()
                        if row.get("filename") and row["filename"] != safe_name
                    ]

                    for cand in candidates:
                        rc = await client.get(ml_url(os.path.basename(cand)), headers=headers)
                        if rc.status_code < 400:
                            safe_name = os.path.basename(cand)
                            r = rc
                            break

            # If prefix-based fallback did not work, resolve patient_id from the
            # stale filename and try the latest reports for that patient.
            if r.status_code == 404:
                fr = await db.execute(text("""
                    SELECT patient_id
                    FROM diagnostic_reports
                    WHERE filename = :fname
                    LIMIT 1
                """), {"fname": safe_name})
                row = fr.mappings().first()
                if row and row.get("patient_id"):
                    lr = await db.execute(text("""
                        SELECT filename
                        FROM diagnostic_reports
                        WHERE patient_id = :pid
                        ORDER BY generated_at DESC NULLS LAST
                        LIMIT 12
                    """), {"pid": str(row["patient_id"])})
                    latest_names = [
                        rr["filename"] for rr in lr.mappings().all()
                        if rr.get("filename") and rr["filename"] != safe_name
                    ]
                    for cand in latest_names:
                        rc = await client.get(ml_url(os.path.basename(cand)), headers=headers)
                        if rc.status_code < 400:
                            safe_name = os.path.basename(cand)
                            r = rc
                            break

            # If file is still missing, regenerate report once from DB context.
            if r.status_code == 404:
                regen = await db.execute(text("""
                    SELECT patient_id, run_id
                    FROM diagnostic_reports
                    WHERE filename = :fname
                    LIMIT 1
                """), {"fname": os.path.basename(filename)})
                rr = regen.mappings().first()
                if rr and rr.get("patient_id"):
                    run_id = str(rr["run_id"]) if rr.get("run_id") else None

                    # If stale row has no run_id, bind regeneration to latest diagnostic run.
                    if not run_id:
                        latest_run = await db.execute(text("""
                            SELECT run_id
                            FROM diagnostic_runs
                            WHERE patient_id = :pid
                            ORDER BY created_at DESC
                            LIMIT 1
                        """), {"pid": str(rr["patient_id"])})
                        lrr = latest_run.mappings().first()
                        if lrr and lrr.get("run_id"):
                            run_id = str(lrr["run_id"])

                    regen_payload = {
                        "patient_id": str(rr["patient_id"]),
                        "run_id": run_id,
                        "doctor_name": "OmniSensus Auto-Regen",
                    }
                    try:
                        regen_result = await call_ml("POST", "/report/generate", regen_payload)
                    except Exception:
                        regen_result = None

                    new_name = (regen_result or {}).get("filename") if isinstance(regen_result, dict) else None
                    if new_name:
                        safe_name = os.path.basename(new_name)
                        r = await client.get(ml_url(safe_name), headers=headers)
                        if r.status_code < 400:
                            if run_id:
                                try:
                                    await db.execute(text("""
                                        INSERT INTO diagnostic_reports
                                            (run_id, patient_id, filename, file_path,
                                             report_type, model_version, generated_at)
                                        VALUES (:rid, :pid, :fname, :fpath,
                                                'full_diagnostic', :mv, :now)
                                        ON CONFLICT (filename) DO NOTHING
                                    """), {
                                        "rid": run_id,
                                        "pid": str(rr["patient_id"]),
                                        "fname": safe_name,
                                        "fpath": f"/exports/reports/{safe_name}",
                                        "mv": "3.0.1",
                                        "now": datetime.now(timezone.utc),
                                    })
                                except IntegrityError as e:
                                    logger.warning(
                                        "Skipping regenerated diagnostic_reports insert for %s due to integrity error: %s",
                                        str(rr["patient_id"]),
                                        e,
                                    )
                            else:
                                logger.warning(
                                    "Skipping regenerated diagnostic_reports insert for %s because run_id is missing",
                                    str(rr["patient_id"]),
                                )
    except Exception as e:
        logger.error(f"Report proxy network error: {e}")
        raise HTTPException(503, "Unable to reach report service.")

    if r.status_code == 404:
        raise HTTPException(404, "Report file not found.")
    if r.status_code >= 400:
        logger.error(f"Report proxy upstream error {r.status_code}: {r.text[:200]}")
        raise HTTPException(502, "Unable to fetch report file.")

    headers = {
        "Content-Disposition": r.headers.get("content-disposition", f'inline; filename="{safe_name}"')
    }

    # Cache successfully downloaded reports locally for repeat preview/download.
    if LOCAL_REPORT_CACHE:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, safe_name), "wb") as f:
                f.write(r.content)
        except Exception as e:
            logger.warning(f"Could not cache report locally: {e}")

    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "application/pdf"),
        headers=headers,
    )


@router.get("/medications")
async def medications(current_user: dict = Depends(get_current_user)):
    try:
        result = await call_ml("GET", "/medications")
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(503, str(e))


@router.get("/medications/{disease}")
async def medications_by_disease(
    disease: str,
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await call_ml("GET", f"/medications/{disease}")
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(503, str(e))


@router.post("/triage")
async def triage(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    try:
        result = await call_ml("POST", "/triage", body)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(503, str(e))