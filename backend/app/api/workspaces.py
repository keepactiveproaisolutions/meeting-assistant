"""Workspace endpoints."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_workspace_member
from app.models import User, Workspace, WorkspaceMember
from app.schemas.schemas import MemberAdd, WorkspaceCreate, WorkspaceOut

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(Workspace).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                            .where(WorkspaceMember.user_id == user.id).order_by(Workspace.created_at))
    return rows.all()


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, user: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    ws = Workspace(name=body.name, owner_id=user.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await db.commit()
    return ws


@router.post("/{workspace_id}/members", status_code=201)
async def add_member(workspace_id: UUID, body: MemberAdd, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    await require_workspace_member(db, workspace_id, user, roles=("owner", "admin"))
    target = await db.scalar(select(User).where(User.email == body.email.lower()))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if await db.scalar(select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == target.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member")
    db.add(WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=body.role))
    await db.commit()
    return {"status": "added"}
