"""SQLAlchemy ORM models. See docs/database-design.md for the ER diagram."""
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.database import Base

EMBED_DIM = get_settings().embedding_dimensions


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def fk(target: str, nullable: bool = False, ondelete: str = "CASCADE"):
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


class User(IdMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Workspace(IdMixin, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[uuid.UUID] = fk("users.id")
    members: Mapped[list["WorkspaceMember"]] = relationship(cascade="all, delete-orphan")


class WorkspaceMember(IdMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    role: Mapped[str] = mapped_column(String(20), default="member")  # owner | admin | member


class Meeting(IdMixin, Base):
    __tablename__ = "meetings"
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    created_by: Mapped[uuid.UUID] = fk("users.id")
    title: Mapped[str] = mapped_column(String(300))
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="live")  # live | ended
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULL = use all of the asker's ready documents; JSON list = only these document ids
    document_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MeetingParticipant(IdMixin, Base):
    """Users (any workspace) who joined a live meeting with its code."""
    __tablename__ = "meeting_participants"
    __table_args__ = (UniqueConstraint("meeting_id", "user_id"),)
    meeting_id: Mapped[uuid.UUID] = fk("meetings.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")


class TranscriptSegment(IdMixin, Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("meeting_id", "chunk_index"),)
    meeting_id: Mapped[uuid.UUID] = fk("meetings.id")
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_offset_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    end_offset_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    is_question: Mapped[bool] = mapped_column(Boolean, default=False)


class DetectedQuestion(IdMixin, Base):
    __tablename__ = "detected_questions"
    meeting_id: Mapped[uuid.UUID] = fk("meetings.id")
    segment_id: Mapped[uuid.UUID] = fk("transcript_segments.id")
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


class Document(IdMixin, Base):
    __tablename__ = "documents"
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    uploaded_by: Mapped[uuid.UUID] = fk("users.id")
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    storage_path: Mapped[str] = mapped_column(String(1000))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    doc_kind: Mapped[str] = mapped_column(String(40), default="supporting")  # cv | jd | project | supporting
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_status: Mapped[str] = mapped_column(String(40), default="pending_approval")  # ready | keyword_only | pending_approval
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")  # extraction/indexing report


class DocumentChunk(IdMixin, Base):
    """A text chunk with optional pgvector embedding and section metadata."""
    __tablename__ = "document_chunks"
    document_id: Mapped[uuid.UUID] = fk("documents.id")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")  # denormalised for fast filtered search
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)


class AIAnswer(IdMixin, Base):
    __tablename__ = "ai_answers"
    meeting_id: Mapped[uuid.UUID] = fk("meetings.id")
    asked_by: Mapped[uuid.UUID] = fk("users.id")
    question_id: Mapped[uuid.UUID | None] = fk("detected_questions.id", nullable=True, ondelete="SET NULL")
    question_text: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # selected transcript text
    answer_text: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="streaming")  # streaming|completed|failed
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sources: Mapped[list["AnswerSource"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class AnswerSource(IdMixin, Base):
    __tablename__ = "answer_sources"
    answer_id: Mapped[uuid.UUID] = fk("ai_answers.id")
    source_type: Mapped[str] = mapped_column(String(20))  # document | transcript
    chunk_id: Mapped[uuid.UUID | None] = fk("document_chunks.id", nullable=True, ondelete="SET NULL")
    segment_id: Mapped[uuid.UUID | None] = fk("transcript_segments.id", nullable=True, ondelete="SET NULL")
    label: Mapped[str] = mapped_column(String(500))  # filename (+page) or "Transcript 00:01:23"
    offset_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    snippet: Mapped[str] = mapped_column(Text)


class ProfileEntry(IdMixin, Base):
    """Scoped user CV, self-reported skills, project briefing, and demo prep per workspace."""
    __tablename__ = "profile_entries"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    cv_document_id: Mapped[uuid.UUID | None] = fk("documents.id", nullable=True, ondelete="SET NULL")
    cv_text: Mapped[str] = mapped_column(Text, default="")
    self_reported_skills: Mapped[str] = mapped_column(Text, default="")
    project_notes: Mapped[str] = mapped_column(Text, default="")
    demo_prep_json: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
