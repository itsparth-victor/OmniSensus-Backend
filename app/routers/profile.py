"""
OmniSensus Backend · app/routers/profile.py
Shared profile endpoints for all 3 roles:
- GET  /profile/me          → own profile (role-aware)
- PUT  /profile/me          → update profile fields
- PUT  /profile/me/password → change password
- PUT  /profile/me/preferences → update notification/theme prefs
- PUT  /profile/me/avatar   → update avatar URL
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database import get_db
from app.security import get_current_user, verify_password, hash_password
from app.services.db_service import log_audit
from datetime import datetime, timezone

router = APIRouter(prefix="/profile", tags=["Profile"])


def _now():
    return datetime.now(timezone.utc)


# ── GET OWN PROFILE (role-aware) ─────────────────────────────────────────
@router.get("/me")
async def get_my_profile(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    role    = current_user["role"]
    user_id = str(current_user["user_id"])

    # Base user record
    ur = await db.execute(text("""
        SELECT user_id, username, email, role, status, mfa_enabled,
               last_login_at, created_at, avatar_url, theme,
               email_notifications, push_notifications
        FROM users WHERE user_id = :uid
    """), {"uid": user_id})
    user = dict(ur.mappings().first() or {})

    # Preferences
    pr = await db.execute(text("""
        SELECT language, timezone, date_format, compact_mode,
               show_risk_tooltips, default_patient_view,
               notify_critical_alerts, notify_appointment_reminders,
               notify_report_ready, notify_system_updates
        FROM user_preferences WHERE user_id = :uid
    """), {"uid": user_id})
    prefs = dict(pr.mappings().first() or {})

    profile = {}

    if role == "patient":
        rr = await db.execute(text("""
            SELECT p.*, d.full_name AS doctor_name, d.specialisation,
                   DATE_PART('year', AGE(p.date_of_birth))::INT AS age
            FROM patients p
            LEFT JOIN doctors d ON p.primary_doctor_id = d.doctor_id
            WHERE p.user_id = :uid
        """), {"uid": user_id})
        profile = dict(rr.mappings().first() or {})

    elif role == "doctor":
        rr = await db.execute(text("""
            SELECT d.*, u2.status AS account_status
            FROM doctors d
            JOIN users u2 ON d.user_id = u2.user_id
            WHERE d.user_id = :uid
        """), {"uid": user_id})
        profile = dict(rr.mappings().first() or {})

    elif role == "admin":
        rr = await db.execute(text("""
            SELECT * FROM admins WHERE user_id = :uid
        """), {"uid": user_id})
        profile = dict(rr.mappings().first() or {})

    return {
        "status":      "success",
        "user":        user,
        "profile":     profile,
        "preferences": prefs,
    }


# ── UPDATE PROFILE FIELDS ─────────────────────────────────────────────────
@router.put("/me")
async def update_my_profile(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    role    = current_user["role"]
    user_id = str(current_user["user_id"])
    now     = _now()

    if role == "patient":
        allowed = {
            "phone", "address", "city", "state", "pincode",
            "notes", "smoker", "alcohol_use", "physical_activity",
            "known_diabetes", "known_hypertension", "known_ckd",
            "known_heart_disease", "bmi", "height_cm", "weight_kg",
            "emergency_contact", "emergency_phone",
        }
        updates = {k: v for k, v in body.items() if k in allowed}
        if updates:
            set_clause = ", ".join(f"{k} = :{k}" for k in updates)
            updates.update({"uid": user_id, "now": now})
            await db.execute(
                text(f"UPDATE patients SET {set_clause}, updated_at = :now "
                     f"WHERE user_id = :uid"),
                updates
            )

    elif role == "doctor":
        allowed = {
            "phone", "work_email", "department", "hospital",
            "bio", "languages", "availability_status"
        }
        updates = {k: v for k, v in body.items() if k in allowed}
        if updates:
            set_clause = ", ".join(f"{k} = :{k}" for k in updates)
            updates.update({"uid": user_id, "now": now})
            await db.execute(
                text(f"UPDATE doctors SET {set_clause}, updated_at = :now "
                     f"WHERE user_id = :uid"),
                updates
            )

    elif role == "admin":
        allowed = {"full_name", "department"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if updates:
            set_clause = ", ".join(f"{k} = :{k}" for k in updates)
            updates.update({"uid": user_id, "now": now})
            await db.execute(
                text(f"UPDATE admins SET {set_clause}, updated_at = :now "
                     f"WHERE user_id = :uid"),
                updates
            )

    await log_audit(db, user_id, current_user["username"], role,
                    "PROFILE_UPDATE", "", "success", str(list(body.keys())))
    return {"status": "success", "message": "Profile updated successfully."}


# ── CHANGE PASSWORD ───────────────────────────────────────────────────────
@router.put("/me/password")
async def change_password(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    current_pwd = body.get("current_password", "")
    new_pwd     = body.get("new_password", "")
    confirm_pwd = body.get("confirm_password", "")

    if not current_pwd or not new_pwd or not confirm_pwd:
        raise HTTPException(400, "All password fields are required.")
    if new_pwd != confirm_pwd:
        raise HTTPException(400, "New password and confirmation do not match.")
    if len(new_pwd) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    user_id = str(current_user["user_id"])

    # Verify current password
    r = await db.execute(
        text("SELECT password_hash FROM users WHERE user_id = :uid"),
        {"uid": user_id}
    )
    row = r.mappings().first()
    if not row:
        raise HTTPException(404, "User not found.")

    if not verify_password(current_pwd, row["password_hash"]):
        await log_audit(db, user_id, current_user["username"],
                        current_user["role"], "PASSWORD_CHANGE", "",
                        "failure", "Wrong current password")
        raise HTTPException(400, "Current password is incorrect.")

    new_hash = hash_password(new_pwd)
    await db.execute(text("""
        UPDATE users SET password_hash = :pw, updated_at = :now
        WHERE user_id = :uid
    """), {"pw": new_hash, "now": _now(), "uid": user_id})

    await log_audit(db, user_id, current_user["username"],
                    current_user["role"], "PASSWORD_CHANGE", "",
                    "success", "Password changed successfully")
    return {"status": "success", "message": "Password changed successfully."}


# ── UPDATE PREFERENCES ────────────────────────────────────────────────────
@router.put("/me/preferences")
async def update_preferences(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    user_id = str(current_user["user_id"])
    now     = _now()

    allowed_prefs = {
        "language", "timezone", "date_format", "compact_mode",
        "show_risk_tooltips", "default_patient_view",
        "notify_critical_alerts", "notify_appointment_reminders",
        "notify_report_ready", "notify_system_updates",
    }
    allowed_user = {"theme", "email_notifications", "push_notifications"}

    pref_updates = {k: v for k, v in body.items() if k in allowed_prefs}
    user_updates = {k: v for k, v in body.items() if k in allowed_user}

    if pref_updates:
        # Create a preference row if one was not created at signup.
        await db.execute(text("""
            INSERT INTO user_preferences (user_id, updated_at)
            SELECT :uid, :now
            WHERE NOT EXISTS (
                SELECT 1 FROM user_preferences WHERE user_id = :uid
            )
        """), {"uid": user_id, "now": now})

        set_clause = ", ".join(f"{k} = :{k}" for k in pref_updates)
        pref_updates.update({"uid": user_id, "now": now})
        await db.execute(
            text(f"UPDATE user_preferences SET {set_clause}, updated_at = :now "
                 f"WHERE user_id = :uid"),
            pref_updates
        )

    if user_updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in user_updates)
        user_updates.update({"uid": user_id, "now": now})
        await db.execute(
            text(f"UPDATE users SET {set_clause}, updated_at = :now "
                 f"WHERE user_id = :uid"),
            user_updates
        )

    return {"status": "success", "message": "Preferences saved."}


# ── UPDATE AVATAR URL ─────────────────────────────────────────────────────
@router.put("/me/avatar")
async def update_avatar(
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    avatar_url = body.get("avatar_url", "").strip()
    if not avatar_url:
        raise HTTPException(400, "avatar_url is required.")

    user_id = str(current_user["user_id"])
    await db.execute(text("""
        UPDATE users SET avatar_url = :url, updated_at = :now
        WHERE user_id = :uid
    """), {"url": avatar_url, "now": _now(), "uid": user_id})

    return {"status": "success", "message": "Avatar updated.", "avatar_url": avatar_url}