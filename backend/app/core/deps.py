"""Shared FastAPI dependencies: authentication and workspace/meeting access control."""
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dev_auth import DEV_USER_EMAIL, ensure_dev_identity
from app.core.security import decode_access_token
from app.models import Meeting, MeetingParticipant, User, WorkspaceMember

bearer = HTTPBearer(auto_error=False)


async def user_from_token(token: str, db: AsyncSession) -> User | None:
    if get_settings().dev_auth_bypass and (not token or token == "dev"):
        dev_user, _ = await ensure_dev_identity(db)
        return dev_user
    user_id = decode_access_token(token)
    if not user_id:
        if get_settings().dev_auth_bypass:
            dev_user, _ = await ensure_dev_identity(db)
            return dev_user
        return None
    user = await db.get(User, user_id)
    return user if user and user.is_active else None


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if get_settings().dev_auth_bypass:
        if creds and creds.credentials:
            user = await user_from_token(creds.credentials, db)
            if user:
                return user
        dev_user, _ = await ensure_dev_identity(db)
        return dev_user

    user = await user_from_token(creds.credentials, db) if creds else None
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing credentials",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


async def get_membership(db: AsyncSession, workspace_id: UUID, user_id: UUID) -> WorkspaceMember | None:
    return await db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id))


async def require_workspace_member(
    db: AsyncSession, workspace_id: UUID, user: User, roles: tuple[str, ...] | None = None
) -> WorkspaceMember:
    if get_settings().dev_auth_bypass and user.email == DEV_USER_EMAIL:
        m = await get_membership(db, workspace_id, user.id)
        if not m:
            m = WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role="owner")
            db.add(m)
            await db.commit()
        return m
    m = await get_membership(db, workspace_id, user.id)
    if not m or (roles and m.role not in roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this workspace")
    return m


async def meeting_access(db: AsyncSession, meeting_id: UUID, user: User) -> tuple[Meeting, bool]:
    """Return (meeting, is_workspace_member). Raises 404/403 if the user has no access.

    Workspace members have full access; users who joined with the meeting code
    (participants) get read + ask access only.
    """
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found")
    if get_settings().dev_auth_bypass:
        return meeting, True
    if await get_membership(db, meeting.workspace_id, user.id):
        return meeting, True
    joined = await db.scalar(select(MeetingParticipant.id).where(
        MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.user_id == user.id))
    if not joined:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the meeting with its code first")
    return meeting, False
