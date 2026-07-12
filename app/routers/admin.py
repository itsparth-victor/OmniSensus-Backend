from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database import get_db
from app.security import require_admin, hash_password
from app.services.db_service import (
    get_platform_analytics,
    get_audit_logs, get_beds, log_audit
)
from app.services.ml_client import ml_eda, ml_risk_factors, ml_health, ml_model_status
from typing import Optional
from datetime import datetime, timezone
import logging

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger("OmniSensus.Admin")

# Cache view availability so we do not execute a failing query on every request.
_HAS_MODEL_FAILURE_RATE_VIEW: Optional[bool] = None
_WARNED_MODEL_FAILURE_RATE_VIEW: bool = False


async def _fetch_model_daily_rates(db: AsyncSession) -> list[dict]:
    global _HAS_MODEL_FAILURE_RATE_VIEW, _WARNED_MODEL_FAILURE_RATE_VIEW

    if _HAS_MODEL_FAILURE_RATE_VIEW is None:
        try:
            probe = await db.execute(text("""
                SELECT to_regclass('public.v_model_failure_rate') AS view_name
            """))
            view_name = (probe.mappings().first() or {}).get("view_name")
            _HAS_MODEL_FAILURE_RATE_VIEW = bool(view_name)
        except Exception:
            # Be defensive: if probing fails, default to fallback path.
            await db.rollback()
            _HAS_MODEL_FAILURE_RATE_VIEW = False

    if _HAS_MODEL_FAILURE_RATE_VIEW:
        try:
            rates = await db.execute(text("""
                SELECT * FROM v_model_failure_rate LIMIT 30
            """))
            return [dict(r) for r in rates.mappings().all()]
        except Exception as exc:
            # The failed query leaves transaction state aborted on Postgres.
            await db.rollback()
            _HAS_MODEL_FAILURE_RATE_VIEW = False
            if not _WARNED_MODEL_FAILURE_RATE_VIEW:
                logger.warning("v_model_failure_rate unavailable, using inline fallback: %s", exc)
                _WARNED_MODEL_FAILURE_RATE_VIEW = True

    fallback = await db.execute(text("""
        SELECT
            DATE(created_at) AS day,
            COUNT(*) FILTER (WHERE status != 'ok')::int AS failures,
            COUNT(*)::int AS total,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE status != 'ok')
                / NULLIF(COUNT(*), 0),
                2
            ) AS failure_rate
        FROM model_events
        WHERE created_at >= (CURRENT_DATE - INTERVAL '30 days')
        GROUP BY DATE(created_at)
        ORDER BY day DESC
        LIMIT 30
    """))
    return [dict(r) for r in fallback.mappings().all()]


# ── ANALYTICS ─────────────────────────────────────────────────────────────
@router.get("/analytics")
async def analytics(
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    platform = await get_platform_analytics(db)

    # Normalize and backfill commonly used dashboard metrics when view fields are missing.
    try:
        patient_counts = await db.execute(text("""
            SELECT
                COUNT(*)::int AS total_patients,
                COUNT(*) FILTER (WHERE current_tier = 'Critical')::int AS critical_count,
                COUNT(*) FILTER (WHERE current_tier = 'Borderline')::int AS borderline_count,
                COUNT(*) FILTER (WHERE current_tier = 'Stable')::int AS stable_count,
                COUNT(*) FILTER (WHERE known_diabetes IS TRUE)::int AS diabetic_count,
                COUNT(*) FILTER (WHERE known_hypertension IS TRUE)::int AS hypertension_count,
                COUNT(*) FILTER (WHERE last_scan_at >= (CURRENT_TIMESTAMP - INTERVAL '7 days'))::int AS scanned_this_week
            FROM patients
        """))
        pc = dict(patient_counts.mappings().first() or {})
    except Exception:
        pc = {}

    try:
        weekly = await db.execute(text("""
            WITH days AS (
              SELECT generate_series(CURRENT_DATE - INTERVAL '6 days', CURRENT_DATE, INTERVAL '1 day')::date AS d
            )
            SELECT
              TO_CHAR(days.d, 'Dy') AS label,
              COALESCE(COUNT(dr.run_id), 0)::int AS value
            FROM days
            LEFT JOIN diagnostic_runs dr ON DATE(dr.created_at) = days.d
            GROUP BY days.d
            ORDER BY days.d
        """))
        weekly_admissions = [dict(r) for r in weekly.mappings().all()]
    except Exception:
        weekly_admissions = []

    if not platform:
        platform = {}

    def _fill_if_missing(key: str, value):
        if platform.get(key) is None:
            platform[key] = value

    _fill_if_missing("total_patients", pc.get("total_patients", 0))
    _fill_if_missing("critical_count", pc.get("critical_count", 0))
    _fill_if_missing("borderline_count", pc.get("borderline_count", 0))
    _fill_if_missing("stable_count", pc.get("stable_count", 0))
    _fill_if_missing("diabetic_count", pc.get("diabetic_count", 0))
    _fill_if_missing("hypertension_count", pc.get("hypertension_count", 0))
    _fill_if_missing("scanned_this_week", pc.get("scanned_this_week", 0))
    if not platform.get("weekly_admissions") and weekly_admissions:
        platform["weekly_admissions"] = weekly_admissions

    critical = await db.execute(text("""
        SELECT patient_id, full_name, current_score, last_scan_at
        FROM patients WHERE current_tier = 'Critical'
        ORDER BY current_score ASC LIMIT 10
    """))
    try:
        model_stats = await ml_health()
    except Exception:
        model_stats = {}
    return {
        "status":            "success",
        "platform":          platform,
        "critical_patients": [dict(r) for r in critical.mappings().all()],
        "model_stats":       model_stats,
    }


# ── USERS LIST ────────────────────────────────────────────────────────────
@router.get("/users")
async def list_users(
    role:      Optional[str] = Query(None),
    status:    Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(10, ge=1, le=50),
    current_user: dict       = Depends(require_admin),
    db: AsyncSession         = Depends(get_db),
):
    where  = "WHERE 1=1"
    params = {}
    if role:
        where += " AND u.role = :role"
        params["role"] = role
    if status:
        where += " AND u.status = :status"
        params["status"] = status
    if search:
        where += """
            AND (
                u.username ILIKE :s
                OR u.email ILIKE :s
                OR EXISTS (SELECT 1 FROM patients p WHERE p.user_id = u.user_id AND p.full_name ILIKE :s)
                OR EXISTS (SELECT 1 FROM doctors d WHERE d.user_id = u.user_id AND d.full_name ILIKE :s)
                OR EXISTS (SELECT 1 FROM admins a WHERE a.user_id = u.user_id AND a.full_name ILIKE :s)
            )
        """
        params["s"] = f"%{search.strip()}%"

    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})

    rows = await db.execute(text(f"""
        SELECT u.user_id, u.username, u.email, u.role, u.status,
               u.mfa_enabled, u.failed_logins, u.last_login_at, u.created_at,
               COALESCE(pt.full_name, d.full_name, a.full_name) AS full_name
        FROM users u
        LEFT JOIN patients pt ON u.user_id = pt.user_id
        LEFT JOIN doctors   d ON u.user_id = d.user_id
        LEFT JOIN admins    a ON u.user_id = a.user_id
        {where}
        ORDER BY u.created_at DESC
        LIMIT :lim OFFSET :off
    """), params)

    items = [dict(r) for r in rows.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    total_q = await db.execute(text(f"SELECT COUNT(*) FROM users u {where}"), count_params)
    total = total_q.scalar() or 0

    return {
        "status": "success",
        "users": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── CREATE USER ───────────────────────────────────────────────────────────
@router.post("/users")
async def create_user(
    body: dict,
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    username = body.get("username", "").strip()
    email    = body.get("email", "").strip()
    password = body.get("password", "").strip()
    role     = body.get("role", "patient")
    full_name= body.get("full_name", username)

    if not username or not email or not password:
        raise HTTPException(400, "username, email, and password are required.")
    if role not in ("admin", "doctor", "patient"):
        raise HTTPException(400, "Invalid role.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    # Check uniqueness
    exists = await db.execute(
        text("SELECT user_id FROM users WHERE username=:u OR email=:e"),
        {"u": username, "e": email}
    )
    if exists.first():
        raise HTTPException(409, "Username or email already exists.")

    pw_hash = hash_password(password)
    r = await db.execute(text("""
        INSERT INTO users (username, email, password_hash, role, status)
        VALUES (:u, :e, :pw, :role, 'active')
        RETURNING user_id
    """), {"u": username, "e": email, "pw": pw_hash, "role": role})
    user_id = str(r.scalar())

    # Create role-specific profile
    now = datetime.now(timezone.utc)
    if role == "admin":
        await db.execute(text("""
            INSERT INTO admins (user_id, full_name, department, access_level)
            VALUES (:uid, :name, 'IT & Systems', 1)
        """), {"uid": user_id, "name": full_name})

    elif role == "doctor":
        reg_no = body.get("registration_no", f"REG-{user_id[:8].upper()}")
        await db.execute(text("""
            INSERT INTO doctors
                (user_id, full_name, specialisation, qualification,
                 registration_no, department, hospital)
            VALUES (:uid, :name, :spec, :qual, :reg, :dept, :hosp)
        """), {
            "uid":  user_id,
            "name": full_name,
            "spec": body.get("specialisation", "General Medicine"),
            "qual": body.get("qualification", "MBBS"),
            "reg":  reg_no,
            "dept": body.get("department", "General"),
            "hosp": body.get("hospital", "OmniSensus Medical Centre"),
        })

    elif role == "patient":
        dob = body.get("date_of_birth", "1990-01-01")
        await db.execute(text("""
            INSERT INTO patients
                (user_id, full_name, date_of_birth, gender, blood_group)
            VALUES (:uid, :name, :dob, :gender, :bg)
        """), {
            "uid":    user_id,
            "name":   full_name,
            "dob":    dob,
            "gender": body.get("gender", "M"),
            "bg":     body.get("blood_group", "Unknown"),
        })

    # Ensure preference row exists even if DB has no trigger/default creator.
    await db.execute(text("""
        INSERT INTO user_preferences (user_id, updated_at)
        SELECT :uid, :now
        WHERE NOT EXISTS (
            SELECT 1 FROM user_preferences WHERE user_id = :uid
        )
    """), {"uid": user_id, "now": now})

    await log_audit(db, str(current_user["user_id"]), current_user["username"],
                    "admin", "USER_CREATE", user_id, "success",
                    f"Created {role} user {username}")
    return {"status": "success", "message": f"User {username} created.", "user_id": user_id}


# ── UPDATE USER STATUS ────────────────────────────────────────────────────
@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    body: dict,
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    new_status = body.get("status")
    if new_status not in ("active", "suspended", "deactivated"):
        raise HTTPException(400, "Invalid status.")

    target_q = await db.execute(text("""
        SELECT user_id, username, status
        FROM users
        WHERE user_id = :uid
    """), {"uid": user_id})
    target_user = dict(target_q.mappings().first() or {})
    if not target_user:
        raise HTTPException(404, "User not found.")

    actor_user_id = str(current_user["user_id"])
    if user_id == actor_user_id and new_status in ("suspended", "deactivated"):
        raise HTTPException(403, "Admins cannot suspend or deactivate their own account.")

    old_status = target_user.get("status")
    await db.execute(text("""
        UPDATE users SET status = :s, updated_at = :now WHERE user_id = :uid
    """), {"s": new_status, "now": datetime.now(timezone.utc), "uid": user_id})
    await log_audit(db, str(current_user["user_id"]), current_user["username"],
                    "admin", "USER_STATUS_CHANGE", user_id, "success",
                    f"Status changed from {old_status} to {new_status}")
    return {"status": "success", "message": f"User status updated to {new_status}."}


# ── AUDIT LOGS ────────────────────────────────────────────────────────────
@router.get("/audit")
async def audit_log(
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(20, ge=1, le=100),
    action:    Optional[str] = Query(None),
    status:    Optional[str] = Query(None),
    current_user: dict       = Depends(require_admin),
    db: AsyncSession         = Depends(get_db),
):
    return {"status": "success",
            **await get_audit_logs(db, page, page_size, action, status)}


# ── RESOURCES: BEDS ───────────────────────────────────────────────────────
@router.get("/resources/beds")
async def beds(
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    return {"status": "success", **await get_beds(db)}


# ── RESOURCES: UPDATE BED ─────────────────────────────────────────────────
@router.put("/resources/beds/{bed_id}")
async def update_bed(
    bed_id: str,
    body: dict,
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    allowed = {"status", "notes", "occupied_by", "expected_discharge"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update.")

    set_parts = ", ".join(f"{k} = :{k}" for k in updates)
    updates["bed_id"] = bed_id
    updates["now"]    = datetime.now(timezone.utc)
    await db.execute(
        text(f"UPDATE beds SET {set_parts}, updated_at = :now WHERE bed_id = :bed_id"),
        updates
    )
    return {"status": "success", "message": "Bed updated."}


# ── RESOURCES: EQUIPMENT ──────────────────────────────────────────────────
@router.get("/resources/equipment")
async def equipment(
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    rows = await db.execute(text("""
        SELECT equipment_id, name, category, location, status,
               utilisation_pct, last_maintenance, next_maintenance,
               serial_number, vendor, notes, updated_at
        FROM equipment
        ORDER BY status, name
    """))
    equip = [dict(r) for r in rows.mappings().all()]

    # Summary
    total       = len(equip)
    operational = sum(1 for e in equip if e["status"] == "operational")
    maintenance = sum(1 for e in equip if e["status"] == "maintenance")
    offline     = sum(1 for e in equip if e["status"] == "offline")
    avg_util    = round(
        sum(e["utilisation_pct"] or 0 for e in equip) / total, 1
    ) if total else 0

    return {
        "status":    "success",
        "equipment": equip,
        "summary": {
            "total":       total,
            "operational": operational,
            "maintenance": maintenance,
            "offline":     offline,
            "avg_utilisation_pct": avg_util,
        },
    }


# ── RESOURCES: UPDATE EQUIPMENT ───────────────────────────────────────────
@router.put("/resources/equipment/{equipment_id}")
async def update_equipment(
    equipment_id: str,
    body: dict,
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    allowed = {"status", "utilisation_pct", "notes",
               "last_maintenance", "next_maintenance", "location"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields to update.")

    set_parts = ", ".join(f"{k} = :{k}" for k in updates)
    updates["equipment_id"] = equipment_id
    updates["now"]          = datetime.now(timezone.utc)
    await db.execute(
        text(f"UPDATE equipment SET {set_parts}, updated_at = :now "
             f"WHERE equipment_id = :equipment_id"),
        updates
    )
    await log_audit(db, str(current_user["user_id"]), current_user["username"],
                    "admin", "EQUIPMENT_UPDATE", equipment_id, "success",
                    str(updates))
    return {"status": "success", "message": "Equipment updated."}


# ── RESOURCES: ONCALL STAFF ───────────────────────────────────────────────
@router.get("/resources/oncall")
async def oncall_staff(
    shift_date: Optional[str] = Query(None),
    current_user: dict        = Depends(require_admin),
    db: AsyncSession          = Depends(get_db),
):
    date_filter = shift_date or "CURRENT_DATE"
    if shift_date:
        rows = await db.execute(text("""
            SELECT so.*, d.full_name, d.specialisation, d.department,
                   d.availability_status
            FROM staff_oncall so
            JOIN doctors d ON so.doctor_id = d.doctor_id
            WHERE so.shift_date = :sd
            ORDER BY so.shift_start
        """), {"sd": shift_date})
    else:
        rows = await db.execute(text("""
            SELECT so.*, d.full_name, d.specialisation, d.department,
                   d.availability_status
            FROM staff_oncall so
            JOIN doctors d ON so.doctor_id = d.doctor_id
            WHERE so.shift_date = CURRENT_DATE
            ORDER BY so.shift_start
        """))

    oncall = [dict(r) for r in rows.mappings().all()]
    return {"status": "success", "oncall": oncall, "count": len(oncall)}


# ── RESOURCES: ALL (combined endpoint for resource page) ─────────────────
@router.get("/resources")
async def all_resources(
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    """Single endpoint returning beds + equipment + oncall + summary."""
    beds_data  = await get_beds(db)

    equip_rows = await db.execute(text("""
        SELECT equipment_id, name, category, location, status,
               utilisation_pct, last_maintenance, next_maintenance,
               serial_number, vendor, notes
        FROM equipment ORDER BY status, name
    """))
    equip = [dict(r) for r in equip_rows.mappings().all()]

    oncall_rows = await db.execute(text("""
        SELECT so.oncall_id, so.shift_label, so.shift_start, so.shift_end,
               so.is_available, so.patient_count,
               d.full_name, d.specialisation, d.department
        FROM staff_oncall so
        JOIN doctors d ON so.doctor_id = d.doctor_id
        WHERE so.shift_date = CURRENT_DATE
        ORDER BY so.shift_start
    """))
    oncall = [dict(r) for r in oncall_rows.mappings().all()]

    return {
        "status":   "success",
        "beds":     beds_data.get("beds", []),
        "bed_summary": beds_data.get("summary", {}),
        "equipment": equip,
        "oncall":   oncall,
    }


# ── MODEL PERFORMANCE ─────────────────────────────────────────────────────
@router.get("/model/performance")
async def model_performance(
    current_user: dict = Depends(require_admin),
    db: AsyncSession   = Depends(get_db),
):
    def _to_ratio(value):
        try:
            n = float(value)
        except (TypeError, ValueError):
            return None
        if n > 1.0:
            n = n / 100.0
        if n < 0:
            return 0.0
        if n > 1:
            return 1.0
        return n

    def _fmt_pct(value):
        if value is None:
            return None
        return f"{round(value * 100, 1)}%"

    def _specificity(eval_stats: dict):
        if not isinstance(eval_stats, dict):
            return None
        rec = _to_ratio(eval_stats.get("recall_sensitivity"))
        pre = _to_ratio(eval_stats.get("precision"))
        try:
            pos = float(eval_stats.get("support_positive") or 0)
            tot = float(eval_stats.get("support_total") or 0)
        except (TypeError, ValueError):
            return None

        neg = tot - pos
        if rec is None or pre is None or pre <= 0 or pos <= 0 or neg <= 0:
            return None

        tp = rec * pos
        fp = tp * ((1.0 / pre) - 1.0)
        tn = neg - fp
        if neg <= 0:
            return None

        spec = tn / neg
        if spec < 0:
            return 0.0
        if spec > 1:
            return 1.0
        return spec

    def _weighted_metric(models: dict, key: str):
        num = 0.0
        den = 0.0
        for model in (models or {}).values():
            eval_stats = model.get("eval_stats", {}) if isinstance(model, dict) else {}
            metric = _to_ratio(eval_stats.get(key))
            try:
                weight = float(eval_stats.get("support_total") or 0)
            except (TypeError, ValueError):
                weight = 0
            if metric is None:
                continue
            if weight <= 0:
                weight = 1.0
            num += metric * weight
            den += weight
        return (num / den) if den > 0 else None

    failures = await db.execute(text("""
        SELECT * FROM model_events WHERE status != 'ok'
        ORDER BY created_at DESC LIMIT 20
    """))
    daily_rates = await _fetch_model_daily_rates(db)

    avg_latency_q = await db.execute(text("""
        SELECT ROUND(AVG(latency_ms)::numeric, 1) AS avg_latency_ms
        FROM model_events
        WHERE status = 'ok'
          AND created_at >= (CURRENT_TIMESTAMP - INTERVAL '30 days')
    """))
    avg_latency_ms = (avg_latency_q.mappings().first() or {}).get("avg_latency_ms")

    try:
        ml_health_data = await ml_health()
    except Exception:
        ml_health_data = {}

    try:
        model_status = await ml_model_status()
    except Exception:
        model_status = {}

    models = (model_status or {}).get("models", {}) if isinstance(model_status, dict) else {}
    heart_eval = (models.get("heart") or {}).get("eval_stats", {}) if isinstance(models.get("heart"), dict) else {}
    diabetes_eval = (models.get("diabetes") or {}).get("eval_stats", {}) if isinstance(models.get("diabetes"), dict) else {}
    kidney_eval = (models.get("kidney") or {}).get("eval_stats", {}) if isinstance(models.get("kidney"), dict) else {}

    weighted_acc = _weighted_metric(models, "accuracy")
    weighted_ppv = _weighted_metric(models, "precision")
    diabetic_sens = _to_ratio(diabetes_eval.get("recall_sensitivity"))
    cvd_spec = _specificity(heart_eval)
    renal_auc = _to_ratio(kidney_eval.get("auc_roc"))

    roc_points = [{"x": 0, "y": 0}]
    for disease, model in (models or {}).items():
        eval_stats = model.get("eval_stats", {}) if isinstance(model, dict) else {}
        tpr = _to_ratio(eval_stats.get("recall_sensitivity"))
        spec = _specificity(eval_stats)
        if tpr is None or spec is None:
            continue
        roc_points.append({
            "label": disease,
            "x": round((1.0 - spec) * 100.0, 2),
            "y": round(tpr * 100.0, 2),
        })
    roc_points.append({"x": 100, "y": 100})
    roc_points = sorted(roc_points, key=lambda p: p["x"])

    ml_stats = {
        **(ml_health_data if isinstance(ml_health_data, dict) else {}),
        "overall_accuracy": _fmt_pct(weighted_acc),
        "metabolic_sensitivity": _fmt_pct(diabetic_sens),
        "cvd_specificity": _fmt_pct(cvd_spec),
        "renal_auc": _fmt_pct(renal_auc),
        "ppv": _fmt_pct(weighted_ppv),
        "avg_latency": (f"{avg_latency_ms} ms" if avg_latency_ms is not None else None),
        "avg_latency_ms": avg_latency_ms,
        "roc_points": roc_points,
        "model_status": model_status,
    }

    return {
        "status":          "success",
        "recent_failures": [dict(r) for r in failures.mappings().all()],
        "daily_rates":     daily_rates,
        "ml_health":       ml_stats,
    }


# ── EDA ───────────────────────────────────────────────────────────────────
@router.get("/eda")
async def eda(current_user: dict = Depends(require_admin)):
    try:
        result = await ml_eda()
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(503, f"ML EDA service unavailable: {str(e)}")


@router.get("/eda/risk-factors")
async def risk_factors(current_user: dict = Depends(require_admin)):
    try:
        result = await ml_risk_factors()
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(503, f"ML service unavailable: {str(e)}")