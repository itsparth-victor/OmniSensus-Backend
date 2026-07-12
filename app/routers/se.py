from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database import get_db
from app.security import create_access_token, verify_password
from datetime import datetime, timezone
from typing import Optional
import logging

router = APIRouter(prefix="/se", tags=["Support Engineers"])
logger = logging.getLogger("OmniSensus.SE")

def now():
    return datetime.now(timezone.utc)

# ── SE AUTH ───────────────────────────────────────────────────────────────

async def get_current_se(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Authentication required.")
    from app.security import decode_token
    payload = decode_token(auth.split(" ")[1])
    if not payload or payload.get("type") != "access" or payload.get("portal") != "se":
        raise HTTPException(401, "Invalid or expired SE token.")
    se_id = payload.get("sub")
    r = await db.execute(
        text("SELECT * FROM support_engineers WHERE se_id = :sid"),
        {"sid": se_id}
    )
    se = r.mappings().first()
    if not se:
        raise HTTPException(401, "SE account not found.")
    if not se["is_active"]:
        raise HTTPException(403, "SE account is inactive.")
    return dict(se)

def require_admin_se(current_se: dict = Depends(get_current_se)) -> dict:
    if current_se["role"] != "admin_se":
        raise HTTPException(403, "Admin SE role required.")
    return current_se

# ── SE LOGIN ──────────────────────────────────────────────────────────────

@router.post("/login")
async def se_login(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        raise HTTPException(400, "Username and password required.")

    r = await db.execute(
        text("SELECT * FROM support_engineers WHERE username = :u"),
        {"u": username}
    )
    se = r.mappings().first()

    if not se:
        raise HTTPException(401, "Invalid credentials.")
    if not se["is_active"]:
        raise HTTPException(403, "Account inactive.")
    if not verify_password(password, se["password_hash"]):
        raise HTTPException(401, "Invalid credentials.")

    await db.execute(text("""
        UPDATE support_engineers
        SET last_login_at = :now, last_login_ip = :ip, updated_at = :now
        WHERE se_id = :sid
    """), {"now": now(), "ip": request.client.host, "sid": str(se["se_id"])})

    claims = {
        "sub":      str(se["se_id"]),
        "username": se["username"],
        "role":     se["role"],
        "name":     se["full_name"],
        "portal":   "se",
    }

    return {
        "status":        "success",
        "access_token":  create_access_token(claims),
        "role":          se["role"],
        "name":          se["full_name"],
        "se_id":         str(se["se_id"]),
        "redirect":      "se-dashboard.html",
    }

# ── SE DASHBOARD ──────────────────────────────────────────────────────────

@router.get("/dashboard")
async def se_dashboard(
    current_se: dict = Depends(get_current_se),
    db: AsyncSession = Depends(get_db),
):
    # Platform stats
    platform = await db.execute(text("SELECT * FROM v_platform_analytics"))
    platform_data = dict(platform.mappings().first() or {})

    # Model events summary
    model_summary = await db.execute(text("""
        SELECT
            COUNT(*)                                      AS total_events,
            COUNT(*) FILTER (WHERE status = 'ok')        AS successes,
            COUNT(*) FILTER (WHERE status = 'error')     AS errors,
            COUNT(*) FILTER (WHERE status = 'timeout')   AS timeouts,
            ROUND(AVG(latency_ms) FILTER
                  (WHERE status = 'ok'), 1)               AS avg_latency_ms
        FROM model_events
        WHERE created_at >= NOW() - INTERVAL '24 hours'
    """))
    model_data = dict(model_summary.mappings().first() or {})

    # Recent errors
    recent_errors = await db.execute(text("""
        SELECT * FROM model_events
        WHERE status != 'ok'
        ORDER BY created_at DESC LIMIT 5
    """))

    # Open dev reports
    open_reports = await db.execute(text("""
        SELECT COUNT(*) AS open_count FROM dev_reports
        WHERE status = 'open'
    """))

    # Total users
    user_counts = await db.execute(text("""
        SELECT role, COUNT(*) AS count
        FROM users GROUP BY role
    """))

    return {
        "status":         "success",
        "platform":       platform_data,
        "model_24h":      model_data,
        "recent_errors":  [dict(r) for r in recent_errors.mappings().all()],
        "open_reports":   dict(open_reports.mappings().first() or {}),
        "user_counts":    [dict(r) for r in user_counts.mappings().all()],
    }

# ── SE USERS ──────────────────────────────────────────────────────────────

@router.get("/users")
async def se_users(
    role:      Optional[str] = Query(None),
    status:    Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(10, ge=1, le=50),
    current_se: dict         = Depends(get_current_se),
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
        where += " AND (u.username ILIKE :s OR u.email ILIKE :s)"
        params["s"] = f"%{search}%"

    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})

    r = await db.execute(text(f"""
        SELECT u.user_id, u.username, u.email, u.role, u.status,
               u.failed_logins, u.last_login_at, u.last_login_ip,
               u.created_at,
               COALESCE(pt.full_name, d.full_name, a.full_name) AS full_name
        FROM users u
        LEFT JOIN patients pt ON u.user_id = pt.user_id
        LEFT JOIN doctors   d ON u.user_id = d.user_id
        LEFT JOIN admins    a ON u.user_id = a.user_id
        {where}
        ORDER BY u.created_at DESC
        LIMIT :lim OFFSET :off
    """), params)

    rows = [dict(row) for row in r.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    cr = await db.execute(
        text(f"SELECT COUNT(*) FROM users u {where}"), count_params
    )
    total = cr.scalar()

    return {
        "status":    "success",
        "users":     rows,
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     (total + page_size - 1) // page_size,
    }

# ── SE ACTIVITY LOG ───────────────────────────────────────────────────────

@router.get("/activity")
async def se_activity(
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(20, ge=1, le=100),
    action:    Optional[str] = Query(None),
    current_se: dict         = Depends(get_current_se),
    db: AsyncSession         = Depends(get_db),
):
    where  = "WHERE 1=1"
    params = {}

    if action:
        where += " AND action = :action"
        params["action"] = action

    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})

    r = await db.execute(text(f"""
        SELECT * FROM audit_logs
        {where}
        ORDER BY created_at DESC
        LIMIT :lim OFFSET :off
    """), params)

    rows = [dict(row) for row in r.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    cr = await db.execute(
        text(f"SELECT COUNT(*) FROM audit_logs {where}"), count_params
    )

    return {
        "status":    "success",
        "logs":      rows,
        "total":     cr.scalar(),
        "page":      page,
        "page_size": page_size,
    }

# ── SE MODEL EVENTS ───────────────────────────────────────────────────────

@router.get("/model/events")
async def se_model_events(
    status:    Optional[str] = Query(None),
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(20, ge=1, le=100),
    current_se: dict         = Depends(get_current_se),
    db: AsyncSession         = Depends(get_db),
):
    where  = "WHERE 1=1"
    params = {}

    if status:
        where += " AND status = :status"
        params["status"] = status

    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})

    r = await db.execute(text(f"""
        SELECT * FROM model_events
        {where}
        ORDER BY created_at DESC
        LIMIT :lim OFFSET :off
    """), params)

    rows = [dict(row) for row in r.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    cr = await db.execute(
        text(f"SELECT COUNT(*) FROM model_events {where}"), count_params
    )

    # Daily failure rate
    try:
        rates = await db.execute(text("""
            SELECT * FROM v_model_failure_rate LIMIT 14
        """))
        daily_rates = [dict(r) for r in rates.mappings().all()]
    except Exception as exc:
        logger.warning("v_model_failure_rate unavailable, using inline fallback: %s", exc)
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
            WHERE created_at >= (CURRENT_DATE - INTERVAL '14 days')
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            LIMIT 14
        """))
        daily_rates = [dict(r) for r in fallback.mappings().all()]

    return {
        "status":      "success",
        "events":      rows,
        "total":       cr.scalar(),
        "page":        page,
        "page_size":   page_size,
        "daily_rates": daily_rates,
    }

# ── SE DEV REPORTS ────────────────────────────────────────────────────────

@router.get("/reports")
async def se_get_reports(
    status:    Optional[str] = Query(None),
    priority:  Optional[str] = Query(None),
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(10, ge=1, le=50),
    current_se: dict         = Depends(get_current_se),
    db: AsyncSession         = Depends(get_db),
):
    where  = "WHERE 1=1"
    params = {}

    if status:
        where += " AND dr.status = :status"
        params["status"] = status
    if priority:
        where += " AND dr.priority = :priority"
        params["priority"] = priority

    offset = (page - 1) * page_size
    params.update({"lim": page_size, "off": offset})

    r = await db.execute(text(f"""
        SELECT dr.*, se.full_name AS reporter_name, se.username AS reporter_username
        FROM dev_reports dr
        LEFT JOIN support_engineers se ON dr.reporter_id = se.se_id
        {where}
        ORDER BY dr.filed_at DESC
        LIMIT :lim OFFSET :off
    """), params)

    rows = [dict(row) for row in r.mappings().all()]
    count_params = {k: v for k, v in params.items() if k not in ("lim", "off")}
    cr = await db.execute(
        text(f"SELECT COUNT(*) FROM dev_reports dr {where}"), count_params
    )

    return {
        "status":    "success",
        "reports":   rows,
        "total":     cr.scalar(),
        "page":      page,
        "page_size": page_size,
    }

@router.post("/reports")
async def se_file_report(
    body: dict,
    current_se: dict = Depends(get_current_se),
    db: AsyncSession = Depends(get_db),
):
    title       = body.get("title", "").strip()
    description = body.get("description", "").strip()

    if not title or not description:
        raise HTTPException(400, "Title and description are required.")

    r = await db.execute(text("""
        INSERT INTO dev_reports
            (title, description, evidence, priority, reporter_id,
             assignee_team, tags, linked_model_event)
        VALUES
            (:title, :desc, :evidence, :priority, :reporter,
             :team, :tags, :event)
        RETURNING report_id
    """), {
        "title":    title,
        "desc":     description,
        "evidence": body.get("evidence"),
        "priority": body.get("priority", "medium"),
        "reporter": str(current_se["se_id"]),
        "team":     body.get("assignee_team", "Backend Team"),
        "tags":     body.get("tags"),
        "event":    body.get("linked_model_event"),
    })

    report_id = str(r.scalar())
    return {
        "status":    "success",
        "report_id": report_id,
        "message":   "Report filed successfully.",
    }

@router.put("/reports/{report_id}/status")
async def se_update_report_status(
    report_id: str,
    body: dict,
    current_se: dict = Depends(require_admin_se),
    db: AsyncSession = Depends(get_db),
):
    new_status = body.get("status")
    if new_status not in ("open", "in_review", "resolved", "closed"):
        raise HTTPException(400, "Invalid status.")

    extra = ""
    if new_status == "resolved":
        extra = ", resolved_at = :now"

    await db.execute(text(f"""
        UPDATE dev_reports
        SET status = :status, updated_at = :now {extra}
        WHERE report_id = :rid
    """), {"status": new_status, "now": now(), "rid": report_id})

    return {"status": "success", "message": f"Report status updated to {new_status}."}

# ── SE PROFILE ────────────────────────────────────────────────────────────

@router.get("/me")
async def se_me(current_se: dict = Depends(get_current_se)):
    safe = {k: v for k, v in current_se.items() if k != "password_hash"}
    return {"status": "success", "se": safe}