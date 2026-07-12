from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.security import get_current_user
from app.services.db_service import (
    get_notifications, mark_notification_read, mark_all_read
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("")
async def list_notifications(
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notifs  = await get_notifications(db, str(current_user["user_id"]), limit)
    unread  = sum(1 for n in notifs if not n["is_read"])
    return {"status": "success", "notifications": notifs,
            "unread_count": unread, "total": len(notifs)}

@router.put("/{notif_id}/read")
async def mark_read(
    notif_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await mark_notification_read(db, notif_id, str(current_user["user_id"]))
    return {"status": "success", "message": "Marked as read."}

@router.put("/read-all")
async def read_all(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await mark_all_read(db, str(current_user["user_id"]))
    return {"status": "success", "message": "All notifications marked as read."}
