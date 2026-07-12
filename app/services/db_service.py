from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import datetime, timezone
import ipaddress
import logging

logger = logging.getLogger("OmniSensus.DB")

def now():
    return datetime.now(timezone.utc)


def _normalize_flag_severity(value: str | None) -> str:
    """Map incoming severities to DB enum-safe values."""
    raw = str(value or "").strip().lower()
    mapping = {
        "critical": "critical",
        "severe": "critical",
        "high": "critical",
        "urgent": "critical",
        "borderline": "borderline",
        "moderate": "borderline",
        "medium": "borderline",
        "warning": "borderline",
        "warn": "borderline",
        "info": "info",
        "informational": "info",
        "low": "info",
        "normal": "info",
    }
    return mapping.get(raw, "info")


def _normalize_flag_domain(value: str | None) -> str:
    """Normalize domain labels to stable, DB-safe values."""
    raw = str(value or "").strip().lower()
    mapping = {
        "cardio": "cardiovascular",
        "cvd": "cardiovascular",
        "cardiovascular": "cardiovascular",
        "metabolic": "metabolic",
        "diabetes": "metabolic",
        "glycemic": "metabolic",
        "renal": "renal",
        "kidney": "renal",
        "nephro": "renal",
    }
    return mapping.get(raw, "metabolic")

# ── AUTH ──────────────────────────────────────────────────────────────────

async def get_user_by_username(db: AsyncSession, username: str) -> Optional[dict]:
    r = await db.execute(
        text("SELECT * FROM users WHERE username = :u"),
        {"u": username}
    )
    row = r.mappings().first()
    return dict(row) if row else None

async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[dict]:
    r = await db.execute(
        text("SELECT * FROM users WHERE user_id = :uid"),
        {"uid": user_id}
    )
    row = r.mappings().first()
    return dict(row) if row else None

async def increment_failed_logins(db: AsyncSession, user_id: str):
    await db.execute(
        text("UPDATE users SET failed_logins = failed_logins + 1, updated_at = :now WHERE user_id = :uid"),
        {"now": now(), "uid": user_id}
    )

async def reset_login(db: AsyncSession, user_id: str, ip: str, device: str):
    await db.execute(
        text("""UPDATE users SET failed_logins = 0, last_login_at = :now,
             last_login_ip = :ip, last_device = :dev, updated_at = :now
             WHERE user_id = :uid"""),
        {"now": now(), "ip": ip, "dev": device[:255], "uid": user_id}
    )

async def get_display_name(db: AsyncSession, user_id: str, role: str) -> str:
    tables = {"admin": "admins", "doctor": "doctors", "patient": "patients"}
    table  = tables.get(role)
    if not table:
        return "User"
    r = await db.execute(
        text(f"SELECT full_name FROM {table} WHERE user_id = :uid"),
        {"uid": user_id}
    )
    row = r.mappings().first()
    return row["full_name"] if row else "User"

# ── PATIENTS ──────────────────────────────────────────────────────────────

async def get_patients(db: AsyncSession, doctor_user_id: str = None,
                       role: str = "admin", tier: str = None,
                       search: str = None, page: int = 1,
                       page_size: int = 10) -> dict:
    where  = "WHERE 1=1"
    params = {}

    if role == "doctor":
        r = await db.execute(
            text("SELECT doctor_id FROM doctors WHERE user_id = :uid"),
            {"uid": doctor_user_id}
        )
        doc = r.mappings().first()
        if doc:
            where += " AND p.primary_doctor_id = :did"
            params["did"] = str(doc["doctor_id"])

    if tier:
        where += " AND p.current_tier = :tier"
        params["tier"] = tier

    if search:
        where += " AND (p.full_name ILIKE :s OR CAST(p.patient_id AS TEXT) ILIKE :s)"
        params["s"] = f"%{search}%"

    offset = (page - 1) * page_size
    params.update({"limit": page_size, "offset": offset})

    sql = f"""
        SELECT p.patient_id, p.full_name,
               DATE_PART('year', AGE(p.date_of_birth))::INT AS age,
               p.gender, p.blood_group, p.bmi,
               p.current_score, p.current_tier, p.last_scan_at,
               p.known_diabetes, p.known_hypertension, p.known_ckd, p.smoker,
               d.full_name AS doctor_name,
               u.status    AS account_status,
               pv.glucose, pv.hba1c, pv.egfr,
               pv.blood_pressure_sys, pv.blood_pressure_dia
        FROM patients p
        LEFT JOIN doctors d ON p.primary_doctor_id = d.doctor_id
        LEFT JOIN users   u ON p.user_id = u.user_id
        LEFT JOIN LATERAL (
            SELECT glucose, hba1c, egfr, blood_pressure_sys, blood_pressure_dia
            FROM patient_vitals WHERE patient_id = p.patient_id
            ORDER BY recorded_at DESC LIMIT 1
        ) pv ON TRUE
        {where}
        ORDER BY CASE p.current_tier
            WHEN 'Critical' THEN 1 WHEN 'Borderline' THEN 2 ELSE 3 END,
            p.current_score ASC
        LIMIT :limit OFFSET :offset
    """
    r     = await db.execute(text(sql), params)
    rows  = [dict(row) for row in r.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    cr    = await db.execute(text(f"SELECT COUNT(*) FROM patients p {where}"), count_params)
    total = cr.scalar()
    return {"patients": rows, "total": total, "page": page,
            "page_size": page_size, "pages": (total + page_size - 1) // page_size}

async def get_patient_by_id(db: AsyncSession, patient_id: str) -> Optional[dict]:
    r = await db.execute(text("""
        SELECT p.*, DATE_PART('year', AGE(p.date_of_birth))::INT AS age,
               d.full_name AS doctor_name, d.specialisation,
               u.email, u.status AS account_status, u.last_login_at
        FROM patients p
        LEFT JOIN doctors d ON p.primary_doctor_id = d.doctor_id
        LEFT JOIN users   u ON p.user_id = u.user_id
        WHERE p.patient_id = :pid
    """), {"pid": patient_id})
    row = r.mappings().first()
    return dict(row) if row else None

async def get_patient_by_user_id(db: AsyncSession, user_id: str) -> Optional[dict]:
    r = await db.execute(text("""
        SELECT p.*, DATE_PART('year', AGE(p.date_of_birth))::INT AS age,
               d.full_name AS doctor_name, d.specialisation, u.email
        FROM patients p
        LEFT JOIN doctors d ON p.primary_doctor_id = d.doctor_id
        LEFT JOIN users   u ON p.user_id = u.user_id
        WHERE p.user_id = :uid
    """), {"uid": user_id})
    row = r.mappings().first()
    return dict(row) if row else None

async def get_visit_history(db: AsyncSession, patient_id: str) -> list:
    r = await db.execute(text("""
        SELECT vh.*, d.full_name AS doctor_name,
               dr.heart_risk_pct, dr.diabetes_risk_pct, dr.kidney_risk_pct
        FROM visit_history vh
        LEFT JOIN doctors         d  ON vh.doctor_id = d.doctor_id
        LEFT JOIN diagnostic_runs dr ON vh.run_id    = dr.run_id
        WHERE vh.patient_id = :pid
        ORDER BY vh.visit_date DESC LIMIT 24
    """), {"pid": patient_id})
    return [dict(row) for row in r.mappings().all()]

async def get_latest_vitals(db: AsyncSession, patient_id: str) -> Optional[dict]:
    r = await db.execute(text("""
        SELECT * FROM patient_vitals WHERE patient_id = :pid
        ORDER BY recorded_at DESC LIMIT 1
    """), {"pid": patient_id})
    row = r.mappings().first()
    return dict(row) if row else None

async def get_patient_reports(db: AsyncSession, patient_id: str) -> list:
    r = await db.execute(text("""
        SELECT rpt.*, d.full_name AS doctor_name,
               dr.health_score, dr.risk_tier, dr.run_id
        FROM diagnostic_reports rpt
        LEFT JOIN doctors         d  ON rpt.doctor_id = d.doctor_id
        LEFT JOIN diagnostic_runs dr ON rpt.run_id    = dr.run_id
        WHERE rpt.patient_id = :pid
        ORDER BY rpt.generated_at DESC NULLS LAST
    """), {"pid": patient_id})
    reports = [dict(row) for row in r.mappings().all()]

    # For each report, fetch vitals and domain scores
    for report in reports:
        run_id = report.get('run_id')
        if run_id:
            # Vitals
            vitals_r = await db.execute(
                text("SELECT * FROM patient_vitals WHERE run_id = :rid"),
                {"rid": run_id}
            )
            vitals = vitals_r.mappings().first()
            if vitals:
                report.update({
                    "hr": vitals.get("heart_rate"),
                    "bp_sys": vitals.get("blood_pressure_sys"),
                    "bp_dia": vitals.get("blood_pressure_dia"),
                    "spo2": vitals.get("spo2"),
                    "temp": vitals.get("temperature"),
                    "glucose": vitals.get("glucose"),
                    "hba1c": vitals.get("hba1c"),
                    "chol": vitals.get("cholesterol_total"),
                    "ldl": vitals.get("ldl"),
                    "egfr": vitals.get("egfr"),
                    "crp": vitals.get("crp"),
                })
            # Domain scores
            ds_r = await db.execute(
                text("SELECT * FROM domain_scores WHERE run_id = :rid"),
                {"rid": run_id}
            )
            ds = ds_r.mappings().first()
            if ds:
                report.update({
                    "cardiovascular": ds.get("cardiovascular"),
                    "metabolic": ds.get("metabolic"),
                    "renal": ds.get("renal"),
                })
    return reports

async def get_patient_medications(db: AsyncSession, patient_id: str) -> list:
    r = await db.execute(text("""
        SELECT pm.*, m.name, m.drug_class, m.disease_target,
               d.full_name AS prescribed_by_name
        FROM patient_medications pm
        JOIN medications m  ON pm.medication_id = m.medication_id
        LEFT JOIN doctors d ON pm.prescribed_by  = d.doctor_id
        WHERE pm.patient_id = :pid AND pm.status = 'active'
        ORDER BY pm.prescribed_at DESC
    """), {"pid": patient_id})
    return [dict(row) for row in r.mappings().all()]

# ── SAVE DIAGNOSTIC RUN ───────────────────────────────────────────────────

async def save_diagnostic_run(db: AsyncSession, patient_id: str,
                               doctor_id: str, ml_result: dict,
                               vitals: dict) -> str:
    # Normalize urgency value to match enum casing
    urgency_raw = ml_result.get("triage", {}).get("urgency", "Routine")
    urgency_map = {
        "routine": "Routine",
        "Routine": "Routine",
        "urgent": "Urgent",
        "Urgent": "Urgent",
        "critical": "Critical",
        "Critical": "Critical",
        "semi-urgent": "Semi-Urgent",
        "Semi-Urgent": "Semi-Urgent",
        "soon": "Semi-Urgent",
        "Soon": "Semi-Urgent"
    }
    urgency = urgency_map.get(str(urgency_raw).strip(), "Routine")
    r = await db.execute(text("""
        INSERT INTO diagnostic_runs (
            patient_id, doctor_id, health_score, risk_tier,
            heart_risk_pct, diabetes_risk_pct, kidney_risk_pct,
            weight_heart, weight_diabetes, weight_kidney,
            recommended_dept, urgency, urgency_note,
            latency_ms, model_version, trend_status, trend_delta,
            trend_forecast, patient_context_desc
        ) VALUES (
            :pid, :did, :hs, :tier,
            :hrisk, :drisk, :krisk,
            :wh, :wd, :wk,
            :dept, :urgency, :urgency_note,
            :latency, :model, :tstatus, :tdelta,
            :tforecast, :ctx
        ) RETURNING run_id
    """), {
        "pid":          patient_id,
        "did":          doctor_id,
        "hs":           ml_result.get("health_score", 50),
        "tier":         ml_result.get("risk_tier", "Stable"),
        "hrisk":        ml_result.get("raw_risks", {}).get("heart_pct", 0),
        "drisk":        ml_result.get("raw_risks", {}).get("diabetes_pct", 0),
        "krisk":        ml_result.get("raw_risks", {}).get("kidney_pct", 0),
        "wh":           ml_result.get("adaptive_weights", {}).get("heart", 0.40),
        "wd":           ml_result.get("adaptive_weights", {}).get("diabetes", 0.35),
        "wk":           ml_result.get("adaptive_weights", {}).get("kidney", 0.25),
        "dept":         ml_result.get("triage", {}).get("department"),
        "urgency":      urgency,
        "urgency_note": ml_result.get("triage", {}).get("urgency_note"),
        "latency":      ml_result.get("latency_ms"),
        "model":        ml_result.get("model_version", "3.0.1"),
        "tstatus":      ml_result.get("trend_analysis", {}).get("status"),
        "tdelta":       ml_result.get("trend_analysis", {}).get("delta"),
        "tforecast":    ml_result.get("trend_analysis", {}).get("forecast"),
        "ctx":          ml_result.get("patient_context"),
    })
    run_id = str(r.scalar())

    ds = ml_result.get("domain_scores", {})
    await db.execute(text("""
        INSERT INTO domain_scores (run_id, cardiovascular, metabolic, renal)
        VALUES (:rid, :cvd, :meta, :renal)
    """), {"rid": run_id, "cvd": ds.get("cardiovascular", 50),
           "meta": ds.get("metabolic", 50), "renal": ds.get("renal", 50)})

    for flag in ml_result.get("clinical_flags", []):
        severity = _normalize_flag_severity(flag.get("severity"))
        domain = _normalize_flag_domain(flag.get("domain"))
        message = str(flag.get("message") or "Clinical flag detected.").strip()
        await db.execute(text("""
            INSERT INTO clinical_flags (run_id, domain, severity, message)
            VALUES (:rid, :dom, :sev, :msg)
        """), {"rid": run_id, "dom": domain,
               "sev": severity, "msg": message})

    for rank, (feat, shap) in enumerate(ml_result.get("ai_insights", {}).items(), 1):
        await db.execute(text("""
            INSERT INTO ai_insights (run_id, feature_name, shap_value, rank_position)
            VALUES (:rid, :feat, :shap, :rank)
            ON CONFLICT (run_id, feature_name) DO NOTHING
        """), {"rid": run_id, "feat": feat, "shap": float(shap), "rank": rank})

    v = vitals
    await db.execute(text("""
        INSERT INTO patient_vitals (
            run_id, patient_id, glucose, hba1c, insulin,
            blood_pressure_sys, blood_pressure_dia,
            cholesterol_total, egfr, creatinine,
            bmi, heart_rate, spo2
        ) VALUES (
            :rid, :pid, :gluc, :hba1c, :ins,
            :bps, :bpd, :chol, :egfr, :creat,
            :bmi, :hr, :spo2
        )
    """), {
        "rid":   run_id, "pid": patient_id,
        "gluc":  v.get("glucose"),
        "hba1c": v.get("hba1c") or v.get("hba_1c"),
        "ins":   v.get("insulin"),
        "bps":   v.get("blood_pressure_sys") or v.get("blood_pressure"),
        "bpd":   v.get("blood_pressure_dia"),
        "chol":  v.get("cholesterol"),
        "egfr":  v.get("egfr"),
        "creat": v.get("creatinine"),
        "bmi":   v.get("bmi"),
        "hr":    v.get("heart_rate"),
        "spo2":  v.get("spo2"),
    })

    await db.execute(text("""
        INSERT INTO visit_history (
            patient_id, run_id, doctor_id, visit_type,
            health_score, risk_tier, glucose, hba1c,
            egfr, bp_sys, bp_dia, summary_notes
        ) VALUES (
            :pid, :rid, :did, 'Full Diagnostic',
            :hs, :tier, :gluc, :hba1c,
            :egfr, :bps, :bpd, :notes
        )
    """), {
        "pid":   patient_id, "rid": run_id, "did": doctor_id,
        "hs":    ml_result.get("health_score"),
        "tier":  ml_result.get("risk_tier"),
        "gluc":  v.get("glucose"),
        "hba1c": v.get("hba1c"),
        "egfr":  v.get("egfr"),
        "bps":   v.get("blood_pressure_sys") or v.get("blood_pressure"),
        "bpd":   v.get("blood_pressure_dia"),
        "notes": ml_result.get("patient_context"),
    })

    return run_id

# ── APPOINTMENTS ─────────────────────────────────────────────────────────

async def get_appointments(db: AsyncSession, role: str,
                            user_id: str) -> list:
    if role == "doctor":
        r = await db.execute(text("""
            SELECT a.*, p.full_name AS patient_name,
                   DATE_PART('year', AGE(p.date_of_birth))::INT AS patient_age
            FROM appointments a
            JOIN patients p ON a.patient_id = p.patient_id
            WHERE a.doctor_id = (SELECT doctor_id FROM doctors WHERE user_id = :uid)
              AND a.scheduled_at >= NOW() - INTERVAL '30 days'
            ORDER BY a.scheduled_at ASC
        """), {"uid": user_id})
    elif role == "patient":
        r = await db.execute(text("""
            SELECT a.*, d.full_name AS doctor_name, d.specialisation
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.doctor_id
            WHERE a.patient_id = (SELECT patient_id FROM patients WHERE user_id = :uid)
            ORDER BY a.scheduled_at ASC
        """), {"uid": user_id})
    else:
        r = await db.execute(text("""
            SELECT a.*, p.full_name AS patient_name, d.full_name AS doctor_name
            FROM appointments a
            JOIN patients p ON a.patient_id = p.patient_id
            JOIN doctors  d ON a.doctor_id  = d.doctor_id
            ORDER BY a.scheduled_at DESC LIMIT 100
        """))
    return [dict(row) for row in r.mappings().all()]

async def create_appointment(db: AsyncSession, data: dict) -> str:
    r = await db.execute(text("""
        INSERT INTO appointments
            (patient_id, doctor_id, type, scheduled_at, duration_min, notes, follow_up_due)
        VALUES (:pid, :did, :type, :sat, :dur, :notes, :fup)
        RETURNING appointment_id
    """), {
        "pid":   str(data["patient_id"]),
        "did":   str(data["doctor_id"]),
        "type":  data.get("type", "consultation"),
        "sat":   data["scheduled_at"],
        "dur":   data.get("duration_min", 30),
        "notes": data.get("notes"),
        "fup":   data.get("follow_up_due"),
    })
    return str(r.scalar())

# ── NOTIFICATIONS ─────────────────────────────────────────────────────────

async def get_notifications(db: AsyncSession, user_id: str,
                             limit: int = 20) -> list:
    r = await db.execute(text("""
        SELECT * FROM notifications
        WHERE user_id = :uid AND is_dismissed = FALSE
        ORDER BY created_at DESC LIMIT :lim
    """), {"uid": user_id, "lim": limit})
    return [dict(row) for row in r.mappings().all()]

async def mark_notification_read(db: AsyncSession,
                                  notification_id: str, user_id: str):
    await db.execute(text("""
        UPDATE notifications SET is_read = TRUE, read_at = :now
        WHERE notification_id = :nid AND user_id = :uid
    """), {"now": now(), "nid": notification_id, "uid": user_id})

async def mark_all_read(db: AsyncSession, user_id: str):
    await db.execute(text("""
        UPDATE notifications SET is_read = TRUE, read_at = :now
        WHERE user_id = :uid AND is_read = FALSE
    """), {"now": now(), "uid": user_id})

async def create_notification(db: AsyncSession, user_id: str, title: str,
                               message: str, notif_type: str = "clinical"):
    await db.execute(text("""
        INSERT INTO notifications (user_id, type, title, message, source)
        VALUES (:uid, :type, :title, :msg, 'Clinical System')
    """), {"uid": user_id, "type": notif_type,
           "title": title, "msg": message})

# ── AUDIT LOG ─────────────────────────────────────────────────────────────

async def log_audit(db: AsyncSession, user_id: str, username: str,
                    role: str, action: str, resource: str = "",
                    status: str = "success", detail: str = "",
                    ip: str = "", device: str = ""):
    clean_ip = None
    if ip:
        ip_text = str(ip).strip()
        if ip_text:
            try:
                clean_ip = str(ipaddress.ip_address(ip_text))
            except ValueError:
                logger.warning("Ignoring invalid audit IP address: %s", ip_text)

    await db.execute(text("""
        INSERT INTO audit_logs
            (user_id, username, user_role, action, resource,
             status, detail, ip_address, device_info)
        VALUES (:uid, :uname, :role, :action, :res,
                :status, :detail, :ip, :dev)
    """), {
        "uid": user_id, "uname": username, "role": role,
        "action": action, "res": resource, "status": status,
        "detail": detail,
        "ip": clean_ip,
        "dev": device[:255] if device else "",
    })

# ── ADMIN ─────────────────────────────────────────────────────────────────

async def get_platform_analytics(db: AsyncSession) -> dict:
    r = await db.execute(text("SELECT * FROM v_platform_analytics"))
    return dict(r.mappings().first() or {})

async def get_all_users(db: AsyncSession, role: str = None,
                        status: str = None, page: int = 1,
                        page_size: int = 10) -> dict:
    where  = "WHERE 1=1"
    params = {}
    if role:
        where += " AND u.role = :role"
        params["role"] = role
    if status:
        where += " AND u.status = :status"
        params["status"] = status
    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})
    r = await db.execute(text(f"""
        SELECT u.user_id, u.username, u.email, u.role, u.status,
               u.mfa_enabled, u.failed_logins, u.last_login_at, u.created_at,
               COALESCE(pt.full_name, d.full_name, a.full_name) AS full_name
        FROM users u
        LEFT JOIN patients pt ON u.user_id = pt.user_id
        LEFT JOIN doctors   d ON u.user_id = d.user_id
        LEFT JOIN admins    a ON u.user_id = a.user_id
        {where} ORDER BY u.created_at DESC LIMIT :lim OFFSET :off
    """), params)
    rows = [dict(row) for row in r.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    cr = await db.execute(text(f"SELECT COUNT(*) FROM users u {where}"), count_params)
    total = cr.scalar()
    return {"users": rows, "total": total, "page": page, "page_size": page_size}

async def get_audit_logs(db: AsyncSession, page: int = 1,
                         page_size: int = 20, action: str = None,
                         status: str = None) -> dict:
    where  = "WHERE 1=1"
    params = {}
    if action and action.strip():
        where += " AND action ILIKE :action"
        params["action"] = f"%{action.strip()}%"
    if status and status.strip():
        where += " AND status = :status"
        params["status"] = status.strip()
    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})
    r = await db.execute(text(f"""
        SELECT * FROM audit_logs {where}
        ORDER BY created_at DESC LIMIT :lim OFFSET :off
    """), params)
    rows = [dict(row) for row in r.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    cr = await db.execute(text(f"SELECT COUNT(*) FROM audit_logs {where}"), count_params)
    return {"logs": rows, "total": cr.scalar(), "page": page, "page_size": page_size}

async def get_beds(db: AsyncSession) -> dict:
    r = await db.execute(text("""
        SELECT b.*, p.full_name AS patient_name
        FROM beds b LEFT JOIN patients p ON b.occupied_by = p.patient_id
        ORDER BY b.ward, b.bed_number
    """))
    rows = [dict(row) for row in r.mappings().all()]
    sr = await db.execute(text("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'occupied')  AS occupied,
               COUNT(*) FILTER (WHERE status = 'available') AS available,
               COUNT(*) FILTER (WHERE status = 'icu')       AS icu
        FROM beds
    """))
    return {"beds": rows, "summary": dict(sr.mappings().first() or {})}
