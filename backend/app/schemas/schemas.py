"""Pydantic request/response schemas."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth ----
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    workspace_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORM):
    id: UUID
    email: EmailStr
    full_name: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- workspaces ----
class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceOut(ORM):
    id: UUID
    name: str


class MemberAdd(BaseModel):
    email: EmailStr
    role: Literal["admin", "member"] = "member"


# ---- meetings ----
class MeetingCreate(BaseModel):
    title: str = Field(default="Untitled meeting", max_length=300)


class MeetingOut(ORM):
    id: UUID
    workspace_id: UUID
    title: str
    code: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    join_url: str | None = None


class JoinRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class SegmentOut(ORM):
    id: UUID
    chunk_index: int
    start_offset_ms: int
    end_offset_ms: int
    text: str
    is_question: bool
    created_at: datetime


class QuestionOut(ORM):
    id: UUID
    segment_id: UUID
    text: str
    confidence: float
    created_at: datetime


class SourceOut(ORM):
    id: UUID | None = None
    source_type: str
    label: str
    snippet: str
    offset_ms: int | None = None
    score: float | None = None


class DiagramOut(BaseModel):
    index: int
    kind: str
    source: str
    svg: str
    ok: bool = True
    error: str | None = None


class AnswerOut(ORM):
    id: UUID
    question_id: UUID | None
    question_text: str
    answer_text: str
    status: str
    created_at: datetime
    sources: list[SourceOut] = []
    spoken_markdown: str = ""
    spoken_clean: str = ""
    technical_markdown: str = ""
    how_to_draw_markdown: str = ""
    diagrams: list[DiagramOut] = []
    code_blocks: list[dict[str, str]] = []
    design_state: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _populate_structured_fields(self) -> "AnswerOut":
        from app.services.diagram_renderer import parse_structured_answer

        parsed = parse_structured_answer(self.answer_text or "", is_complete=(self.status == "completed"))
        self.spoken_markdown = parsed.spoken_markdown
        self.spoken_clean = parsed.spoken_clean
        self.technical_markdown = parsed.technical_markdown
        self.how_to_draw_markdown = parsed.how_to_draw_markdown
        self.diagrams = [DiagramOut(**d.to_dict()) for d in parsed.diagrams]
        self.code_blocks = parsed.code_blocks
        self.design_state = parsed.design_state
        return self


class ProfileUpdate(BaseModel):
    cv_text: str = Field(default="", max_length=80000)
    self_reported_skills: str = Field(default="", max_length=20000)
    project_notes: str = Field(default="", max_length=80000)


class ProfileOut(ORM):
    id: UUID | None = None
    workspace_id: UUID
    user_id: UUID
    cv_document_id: UUID | None = None
    cv_text: str = ""
    self_reported_skills: str = ""
    project_notes: str = ""
    demo_prep: dict[str, Any] | None = None
    cv_document: dict[str, Any] | None = None
    retrieval_mode: str = "none"
    updated_at: datetime | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    selected_text: str | None = Field(default=None, max_length=8000)
    question_id: UUID | None = None


class AskResponse(BaseModel):
    answer_id: UUID


class UtteranceOut(BaseModel):
    id: str
    start_offset_ms: int
    end_offset_ms: int
    text: str
    is_final: bool = False
    is_question: bool = False
    questions: list[str] = []
    segment_ids: list[str] = []


class MeetingSnapshot(BaseModel):
    meeting: MeetingOut
    segments: list[SegmentOut]
    utterances: list[UtteranceOut] = []
    questions: list[QuestionOut]
    answers: list[AnswerOut]
    partials: list[dict] = []  # Live mode provisional text still being spoken
    live: bool = False


# ---- documents ----
class DocumentOut(ORM):
    id: UUID
    workspace_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    file_sha256: str | None = None
    doc_kind: str = "document"
    embedding_model: str | None = None
    embedding_status: str = "pending_approval"
    status: str
    error: str | None = None
    created_at: datetime
