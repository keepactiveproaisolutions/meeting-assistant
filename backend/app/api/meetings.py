"""Meeting endpoints: create, join by code, snapshot, audio ingestion, ask."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user, meeting_access, require_workspace_member
from app.core.dev_auth import ensure_dev_identity
from app.models import DetectedQuestion, Meeting, MeetingParticipant, User, WorkspaceMember
from app.realtime.events import Event
from app.realtime.manager import manager
from app.schemas.schemas import (AskRequest, AskResponse, JoinRequest, MeetingCreate, MeetingOut,
                                 MeetingSnapshot, SegmentOut)
from app.services import answer_service, meeting_service

router = APIRouter(tags=["meetings"])
MAX_AUDIO_BYTES = 2 * 1024 * 1024  # a 2 s 16 kHz mono WAV is ~64 KB; generous cap


@router.get("/meetings", response_model=list[MeetingOut])
async def list_default_meetings(user: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_db)):
    if get_settings().dev_auth_bypass:
        _, dev_ws = await ensure_dev_identity(db)
        ws_id = dev_ws.id
    else:
        ws_id = await db.scalar(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id))
        if not ws_id:
            return []
    rows = await db.scalars(select(Meeting).where(Meeting.workspace_id == ws_id)
                            .order_by(Meeting.started_at.desc()).limit(50))
    return [meeting_service.meeting_out(m) for m in rows]


@router.post("/meetings", response_model=MeetingOut, status_code=201)
async def create_default_meeting(body: MeetingCreate, user: User = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)):
    if get_settings().dev_auth_bypass:
        _, dev_ws = await ensure_dev_identity(db)
        ws_id = dev_ws.id
    else:
        ws_id = await db.scalar(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id))
        if not ws_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No workspace found for user")
    meeting = await meeting_service.create_meeting(db, ws_id, user.id, body.title)
    return meeting_service.meeting_out(meeting)


@router.post("/workspaces/{workspace_id}/meetings", response_model=MeetingOut, status_code=201)
async def create_meeting(workspace_id: UUID, body: MeetingCreate, user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    await require_workspace_member(db, workspace_id, user)
    meeting = await meeting_service.create_meeting(db, workspace_id, user.id, body.title)
    return meeting_service.meeting_out(meeting)


@router.get("/workspaces/{workspace_id}/meetings", response_model=list[MeetingOut])
async def list_meetings(workspace_id: UUID, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    await require_workspace_member(db, workspace_id, user)
    rows = await db.scalars(select(Meeting).where(Meeting.workspace_id == workspace_id)
                            .order_by(Meeting.started_at.desc()).limit(100))
    return [meeting_service.meeting_out(m) for m in rows]


@router.post("/meetings/join", response_model=MeetingOut)
async def join_meeting(body: JoinRequest, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    meeting = await db.scalar(select(Meeting).where(Meeting.code == body.code.strip().upper()))
    if not meeting or meeting.status != "live":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No live meeting with that code")
    exists = await db.scalar(select(MeetingParticipant.id).where(
        MeetingParticipant.meeting_id == meeting.id, MeetingParticipant.user_id == user.id))
    if not exists:
        db.add(MeetingParticipant(meeting_id=meeting.id, user_id=user.id))
        await db.commit()
    return meeting_service.meeting_out(meeting)


@router.get("/meetings/{meeting_id}", response_model=MeetingSnapshot)
async def get_meeting(meeting_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    meeting, _ = await meeting_access(db, meeting_id, user)
    return await meeting_service.snapshot(db, meeting)


@router.post("/meetings/{meeting_id}/reopen", response_model=MeetingOut)
async def reopen_meeting(meeting_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    meeting, is_member = await meeting_access(db, meeting_id, user)
    if not is_member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only workspace members can reopen a meeting")
    if meeting.status != "live":
        meeting.status, meeting.ended_at = "live", None
        await db.commit()
    return meeting_service.meeting_out(meeting)


@router.post("/meetings/{meeting_id}/end", response_model=MeetingOut)
async def end_meeting(meeting_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    meeting, is_member = await meeting_access(db, meeting_id, user)
    if not is_member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only workspace members can end a meeting")
    if meeting.status != "ended":
        meeting.status, meeting.ended_at = "ended", datetime.now(timezone.utc)
        await db.commit()
        await manager.broadcast(meeting.id, Event.MEETING_ENDED, {})
    return meeting_service.meeting_out(meeting)


@router.post("/meetings/{meeting_id}/audio", response_model=SegmentOut | None)
async def upload_audio(meeting_id: UUID, file: UploadFile = File(...), chunk_index: int = Form(ge=0),
                       offset_ms: int = Form(ge=0), duration_ms: int = Form(gt=0, le=30_000),
                       user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Desktop app uploads one ~2 s system-audio WAV chunk; the transcript is pushed over WebSocket."""
    meeting, is_member = await meeting_access(db, meeting_id, user)
    if not is_member:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only workspace members can stream audio")
    if meeting.status != "live":
        raise HTTPException(status.HTTP_409_CONFLICT, "Meeting has ended")
    from app.services import live_transcription
    if live_transcription.active(meeting_id):  # never run Standard and Live paid transcription together
        raise HTTPException(status.HTTP_409_CONFLICT, "Live transcription is active for this meeting")
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Invalid audio chunk size")
    return await meeting_service.ingest_audio_chunk(db, meeting, audio, chunk_index, offset_ms, duration_ms)


@router.post("/meetings/{meeting_id}/ask", response_model=AskResponse, status_code=202)
async def ask(meeting_id: UUID, body: AskRequest, user: User = Depends(get_current_user),
              db: AsyncSession = Depends(get_db)):
    """Start a streamed answer. Tokens are broadcast to every client in the meeting via WebSocket."""
    meeting, _ = await meeting_access(db, meeting_id, user)
    if body.question_id:
        q = await db.get(DetectedQuestion, body.question_id)
        if not q or q.meeting_id != meeting.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown question_id")
    answer = await answer_service.create_answer(db, meeting, user.id, body.question.strip(),
                                                body.selected_text, body.question_id)
    answer_service.schedule_answer(answer.id)
    return AskResponse(answer_id=answer.id)
