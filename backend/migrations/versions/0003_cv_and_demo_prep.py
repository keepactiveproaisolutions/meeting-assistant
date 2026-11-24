"""Add CV deduplication, nullable pgvector embeddings, section titles, and demo prep columns."""
from alembic import op
import sqlalchemy as sa

revision = "0003_cv_and_demo_prep"
down_revision = "0002_profile_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # documents table additions
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_sha256 VARCHAR(64)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_kind VARCHAR(40) NOT NULL DEFAULT 'document'")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_text TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100)")
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS embedding_status VARCHAR(40) NOT NULL DEFAULT 'pending_approval'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_file_sha256 ON documents (file_sha256)")

    # document_chunks table additions + nullable embedding for non-paid passage storage
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section_title VARCHAR(200)")
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100)")
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding DROP NOT NULL")

    # profile_entries table additions
    op.execute(
        "ALTER TABLE profile_entries ADD COLUMN IF NOT EXISTS cv_document_id UUID REFERENCES documents(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE profile_entries ADD COLUMN IF NOT EXISTS self_reported_skills TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE profile_entries ADD COLUMN IF NOT EXISTS demo_prep_json TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE profile_entries DROP COLUMN IF EXISTS demo_prep_json")
    op.execute("ALTER TABLE profile_entries DROP COLUMN IF EXISTS self_reported_skills")
    op.execute("ALTER TABLE profile_entries DROP COLUMN IF EXISTS cv_document_id")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS section_title")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS embedding_status")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS extracted_text")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS doc_kind")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS file_sha256")
