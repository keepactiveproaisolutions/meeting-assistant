"""Demo Prep endpoints (generated from the user's selected documents)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import resolve_workspace_id
from app.core.database import get_db
from app.core.deps import get_current_user, meeting_access
from app.models import User
from app.rag.retriever import meeting_document_ids
from app.realtime.events import Event
from app.realtime.manager import manager
from app.services import demo_prep_service

router = APIRouter(prefix="/demo-prep", tags=["demo-prep"])


async def _scope(db: AsyncSession, user: User, meeting_id: UUID | None):
    if meeting_id is None:
        return await resolve_workspace_id(db, user), None, None
    meeting, _ = await meeting_access(db, meeting_id, user)
    return meeting.workspace_id, meeting_document_ids(meeting), meeting


@router.get("")
async def get_demo_prep(meeting_id: UUID | None = Query(default=None), user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    ws_id, doc_ids, _ = await _scope(db, user, meeting_id)
    return await demo_prep_service.get_prep(db, ws_id, user.id, doc_ids)


@router.post("/generate")
async def generate_demo_prep(meeting_id: UUID | None = Query(default=None), user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    ws_id, doc_ids, meeting = await _scope(db, user, meeting_id)
    try:
        out = await demo_prep_service.generate_prep(db, ws_id, user.id, doc_ids)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if meeting is not None:
        await manager.broadcast(meeting.id, Event.DEMO_PREP_UPDATED, {"generated_at": out["generated_at"]})
    return out
