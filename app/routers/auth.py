from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas import LoginRequest, LoginResponse, RefreshRequest, ForgotPasswordRequest, MessageResponse
from app.security import verify_password, create_access_token, create_refresh_token, decode_token
from app.services.db_service import (get_user_by_username, get_display_name,
                                      increment_failed_logins, reset_login, log_audit)
import logging

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger("OmniSensus.Auth")

ROLE_REDIRECT = {"admin": "admin.html", "doctor": "doctor.html", "patient": "patient.html"}

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_user_by_username(db, body.username)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if user["status"] == "suspended":
        raise HTTPException(status_code=403, detail="Account suspended.")
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account not active.")
    if body.role and user["role"] != body.role:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not verify_password(body.password, user["password_hash"]):
        await increment_failed_logins(db, str(user["user_id"]))
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    await reset_login(db, str(user["user_id"]),
                      request.client.host,
                      request.headers.get("User-Agent", ""))
    name = await get_display_name(db, str(user["user_id"]), user["role"])
    claims = {"sub": str(user["user_id"]), "username": user["username"],
              "role": user["role"], "name": name}
    await log_audit(db, str(user["user_id"]), user["username"],
                    user["role"], "LOGIN", "Auth", "success",
                    ip=request.client.host,
                    device=request.headers.get("User-Agent", ""))
    logger.info(f"[LOGIN] {body.username} ({user['role']})")
    return LoginResponse(
        access_token  = create_access_token(claims),
        refresh_token = create_refresh_token(claims),
        role          = user["role"],
        name          = name,
        user_id       = str(user["user_id"]),
        redirect      = ROLE_REDIRECT.get(user["role"], "index.html"),
    )

@router.post("/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token.")
    user = await get_user_by_username(db, payload.get("username", ""))
    if not user or user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account inactive.")
    new_token = create_access_token({
        "sub": payload["sub"], "username": payload.get("username"),
        "role": payload.get("role"), "name": payload.get("name"),
    })
    return {"status": "success", "access_token": new_token}

@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        payload = decode_token(auth.split(" ")[1])
        if payload:
            await log_audit(db, payload.get("sub"), payload.get("username"),
                            payload.get("role"), "LOGOUT", ip=request.client.host)
    return MessageResponse(message="Logged out.")

@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(body: ForgotPasswordRequest):
    logger.info(f"[RESET] Password reset requested for {body.email}")
    return MessageResponse(message="If that email exists, a reset link has been sent.")
