"""POST /api/v1/meetings/{id}/screen-ask — ask about the current screenshot."""
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, meeting_access
from app.models import User
from app.schemas.schemas import AskResponse
from app.screen_intelligence import service
from app.services import answer_service

router = APIRouter(tags=["screen-intelligence"])
DEFAULT_QUESTION = "Analyze my screen"


@router.post("/meetings/{meeting_id}/screen-ask", response_model=AskResponse, status_code=202)
async def screen_ask(meeting_id: UUID, image: UploadFile = File(...), note: str = Form(default=""),
                     user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Upload one screenshot (JPEG/PNG/WebP). The answer streams to every client of the meeting like any answer."""
    meeting, _ = await meeting_access(db, meeting_id, user)
    data = await image.read(service.MAX_IMAGE_BYTES + 1)
    if not data or len(data) > service.MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Screenshot is empty or larger than 8 MB")
    mime = service.detect_mime(data)
    if mime is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Screenshot must be a JPEG, PNG or WebP image")
    note = (note or "").strip()[:1000]
    question = f"[Screen] {note}" if note else f"[Screen] {DEFAULT_QUESTION}"
    answer = await answer_service.create_answer(db, meeting, user.id, question, None, None)
    service.put_image(answer.id, data, mime)
    answer_service.schedule_answer(answer.id)
    return AskResponse(answer_id=answer.id)
