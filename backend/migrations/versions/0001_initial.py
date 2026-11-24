"""initial schema with pgvector

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBED_DIM = 1536  # must match EMBEDDING_DIMENSIONS


def _id():
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _fk(name, target, nullable=False, ondelete="CASCADE"):
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table("users", _id(), _created(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()))
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table("workspaces", _id(), _created(),
        sa.Column("name", sa.String(200), nullable=False), _fk("owner_id", "users.id"))

    op.create_table("workspace_members", _id(), _created(),
        _fk("workspace_id", "workspaces.id"), _fk("user_id", "users.id"),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.UniqueConstraint("workspace_id", "user_id"))

    op.create_table("meetings", _id(), _created(),
        _fk("workspace_id", "workspaces.id"), _fk("created_by", "users.id"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("code", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="live"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)))
    op.create_index("ix_meetings_code", "meetings", ["code"], unique=True)

    op.create_table("meeting_participants", _id(), _created(),
        _fk("meeting_id", "meetings.id"), _fk("user_id", "users.id"),
        sa.UniqueConstraint("meeting_id", "user_id"))

    op.create_table("transcript_segments", _id(), _created(),
        _fk("meeting_id", "meetings.id"),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("start_offset_ms", sa.BigInteger, nullable=False, index=True),
        sa.Column("end_offset_ms", sa.BigInteger, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("is_question", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("meeting_id", "chunk_index"))

    op.create_table("detected_questions", _id(), _created(),
        _fk("meeting_id", "meetings.id"), _fk("segment_id", "transcript_segments.id"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"))

    op.create_table("documents", _id(), _created(),
        _fk("workspace_id", "workspaces.id"), _fk("uploaded_by", "users.id"),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text))

    op.create_table("document_chunks", _id(), _created(),
        _fk("document_id", "documents.id"), _fk("workspace_id", "workspaces.id"),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("page_number", sa.Integer),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=False))
    op.execute("CREATE INDEX ix_document_chunks_embedding ON document_chunks "
               "USING hnsw (embedding vector_cosine_ops)")

    op.create_table("ai_answers", _id(), _created(),
        _fk("meeting_id", "meetings.id"), _fk("asked_by", "users.id"),
        _fk("question_id", "detected_questions.id", nullable=True, ondelete="SET NULL"),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("context_text", sa.Text),
        sa.Column("answer_text", sa.Text, nullable=False, server_default=""),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="streaming"),
        sa.Column("completed_at", sa.DateTime(timezone=True)))

    op.create_table("answer_sources", _id(), _created(),
        _fk("answer_id", "ai_answers.id"),
        sa.Column("source_type", sa.String(20), nullable=False),
        _fk("chunk_id", "document_chunks.id", nullable=True, ondelete="SET NULL"),
        _fk("segment_id", "transcript_segments.id", nullable=True, ondelete="SET NULL"),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("offset_ms", sa.BigInteger),
        sa.Column("score", sa.Float),
        sa.Column("snippet", sa.Text, nullable=False))


def downgrade() -> None:
    for t in ("answer_sources", "ai_answers", "document_chunks", "documents", "detected_questions",
              "transcript_segments", "meeting_participants", "meetings", "workspace_members",
              "workspaces", "users"):
        op.drop_table(t)
