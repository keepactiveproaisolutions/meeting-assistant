"""Idempotent development identity management when DEV_AUTH_BYPASS is active."""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Workspace, WorkspaceMember

log = logging.getLogger(__name__)

DEV_USER_EMAIL = "dev@local.test"
DEV_USER_NAME = "Development User"
DEV_WORKSPACE_NAME = "Development Workspace"


async def ensure_dev_identity(db: AsyncSession) -> tuple[User, Workspace]:
    """Ensure the single fixed development user and workspace exist without duplicating records."""
    user = await db.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if not user:
        user = User(
            email=DEV_USER_EMAIL,
            full_name=DEV_USER_NAME,
            password_hash="dev-bypass-disabled",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        log.info("Created local dev user: %s (id: %s)", DEV_USER_EMAIL, user.id)

    ws = await db.scalar(
        select(Workspace)
        .where(Workspace.name == DEV_WORKSPACE_NAME, Workspace.owner_id == user.id)
    )
    if not ws:
        ws = Workspace(name=DEV_WORKSPACE_NAME, owner_id=user.id)
        db.add(ws)
        await db.flush()
        log.info("Created local dev workspace: %s (id: %s)", DEV_WORKSPACE_NAME, ws.id)

    member = await db.scalar(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
    )
    if not member:
        member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
        db.add(member)
        await db.flush()

    await db.commit()
    return user, ws
