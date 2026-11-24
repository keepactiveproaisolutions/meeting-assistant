"""User-scoped document management: upload -> extract -> preview -> index -> ready/failed,
plus per-meeting document selection. A user only ever sees and retrieves their own documents.
"""
import json
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user, meeting_access, require_workspace_member
from app.models import Document, DocumentChunk, User, WorkspaceMember
from app.realtime.events import Event
from app.realtime.manager import manager
from app.services import document_service as ds

router = APIRouter(tags=["documents"])


async def resolve_workspace_id(db: AsyncSession, user: User, workspace_id: UUID | None = None) -> UUID:
    if workspace_id is not None:
        await require_workspace_member(db, workspace_id, user)
        return workspace_id
    ws_id = await db.scalar(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
                            .order_by(WorkspaceMember.created_at).limit(1))
    if ws_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No workspace found for this user")
    return ws_id


async def _own_doc(db: AsyncSession, user: User, document_id: UUID) -> Document:
    doc = await db.get(Document, document_id)
    if not doc or doc.uploaded_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


async def _chunk_counts(db: AsyncSession, ids: list[UUID]) -> dict[UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(select(DocumentChunk.document_id, func.count(DocumentChunk.id))
                            .where(DocumentChunk.document_id.in_(ids)).group_by(DocumentChunk.document_id))
    return dict(rows.all())


async def _read_upload(file: UploadFile) -> bytes:
    if not file.filename or not ds.safe_extension(file.filename):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, ds.SUPPORTED_HELP)
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"File too large (limit {get_settings().max_upload_mb} MB).")
    return data


@router.get("/documents/types")
async def document_types():
    return {"types": [{"value": k, "label": v} for k, v in ds.DOC_TYPES.items()],
            "supported": ds.SUPPORTED_HELP, "ocr_available": ds.OCR_AVAILABLE,
            "extensions": sorted(ds.ALLOWED_EXTENSIONS)}


@router.get("/documents")
async def list_documents(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    docs = (await db.scalars(select(Document).where(Document.uploaded_by == user.id)
                             .order_by(Document.created_at.desc()))).all()
    counts = await _chunk_counts(db, [d.id for d in docs])
    return [ds.document_payload(d, counts.get(d.id, 0)) for d in docs]


@router.get("/documents/{document_id}")
async def get_document(document_id: UUID, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    doc = await _own_doc(db, user, document_id)
    counts = await _chunk_counts(db, [doc.id])
    return ds.document_payload(doc, counts.get(doc.id, 0), include_text=True)


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(background: BackgroundTasks, file: UploadFile = File(...),
                          doc_type: str = Form("supporting"), user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    ws_id = await resolve_workspace_id(db, user)
    data = await _read_upload(file)
    sha = ds.sha256_bytes(data)
    kind = ds.normalize_doc_type(doc_type)
    dup = await db.scalar(select(Document).where(Document.uploaded_by == user.id, Document.file_sha256 == sha,
                                                 Document.status != "failed"))
    if dup:
        if dup.doc_kind != kind:
            dup.doc_kind = kind
            await db.commit()
        return {**ds.document_payload(dup), "duplicate": True}
    path = ds.store_file(ws_id, file.filename, data)
    doc = Document(workspace_id=ws_id, uploaded_by=user.id, filename=file.filename[:500],
                   content_type=file.content_type or "application/octet-stream", storage_path=path,
                   size_bytes=len(data), file_sha256=sha, doc_kind=kind, status="pending",
                   embedding_status="pending", meta_json="{}")
    db.add(doc)
    await db.commit()
    background.add_task(ds.ingest_document, doc.id)
    return {**ds.document_payload(doc, 0), "duplicate": False}


@router.post("/documents/{document_id}/replace", status_code=status.HTTP_202_ACCEPTED)
async def replace_document(document_id: UUID, background: BackgroundTasks, file: UploadFile = File(...),
                           user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Swap the file behind an existing document; keeps its id, type and meeting selections."""
    doc = await _own_doc(db, user, document_id)
    data = await _read_upload(file)
    old_path = doc.storage_path
    doc.storage_path = ds.store_file(doc.workspace_id, file.filename, data)
    doc.filename = file.filename[:500]
    doc.content_type = file.content_type or "application/octet-stream"
    doc.size_bytes, doc.file_sha256 = len(data), ds.sha256_bytes(data)
    doc.status, doc.error, doc.meta_json = "pending", None, "{}"
    await db.commit()
    if old_path != doc.storage_path:
        ds.remove_file(old_path)
    background.add_task(ds.ingest_document, doc.id)
    return ds.document_payload(doc, 0)


@router.post("/documents/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(document_id: UUID, background: BackgroundTasks,
                             user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    doc = await _own_doc(db, user, document_id)
    doc.status, doc.error = "pending", None
    await db.commit()
    background.add_task(ds.ingest_document, doc.id)
    return ds.document_payload(doc)


class DocumentPatch(BaseModel):
    doc_type: str


@router.patch("/documents/{document_id}")
async def update_document(document_id: UUID, body: DocumentPatch, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    doc = await _own_doc(db, user, document_id)
    doc.doc_kind = ds.normalize_doc_type(body.doc_type)
    await db.commit()
    return ds.document_payload(doc)


@router.delete("/documents/{document_id}", status_code=200)
async def delete_document(document_id: UUID, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    doc = await _own_doc(db, user, document_id)
    path = doc.storage_path
    await db.delete(doc)  # chunks cascade; answer_sources keep their label/snippet (chunk_id -> NULL)
    await db.commit()
    ds.remove_file(path)
    return {"deleted": str(document_id)}


# ---------------------------------------------------------------------------
# Per-meeting selection
# ---------------------------------------------------------------------------
def selected_ids(meeting) -> list[str] | None:
    if meeting.document_ids_json is None:
        return None
    try:
        v = json.loads(meeting.document_ids_json)
        return [str(x) for x in v] if isinstance(v, list) else None
    except ValueError:
        return None


class SelectionBody(BaseModel):
    document_ids: list[UUID] | None = None  # null = use all my ready documents


@router.get("/meetings/{meeting_id}/documents")
async def get_meeting_documents(meeting_id: UUID, user: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_db)):
    meeting, _ = await meeting_access(db, meeting_id, user)
    # The meeting's documents are its owner's, shared by every device and person in the session.
    docs = (await db.scalars(select(Document).where(Document.uploaded_by == meeting.created_by)
                             .order_by(Document.created_at.desc()))).all()
    sel = selected_ids(meeting)
    counts = await _chunk_counts(db, [d.id for d in docs])
    out = [{**ds.document_payload(d, counts.get(d.id, 0)),
            "selected": d.status == "ready" and (sel is None or str(d.id) in sel)} for d in docs]
    if user.id != meeting.created_by:  # participants only see which documents the session is using
        out = [{k: d[k] for k in ("id", "filename", "doc_type", "doc_type_label", "status", "selected")}
               for d in out if d["selected"]]
    return {"mode": "all" if sel is None else "selected", "documents": out}


@router.put("/meetings/{meeting_id}/documents")
async def set_meeting_documents(meeting_id: UUID, body: SelectionBody, user: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_db)):
    meeting, _ = await meeting_access(db, meeting_id, user)
    if user.id != meeting.created_by:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the meeting owner can change its documents")
    if body.document_ids is None:
        meeting.document_ids_json = None
    else:
        owned = set((await db.scalars(select(Document.id).where(Document.uploaded_by == user.id,
                                                                Document.id.in_(body.document_ids)))).all())
        meeting.document_ids_json = json.dumps([str(i) for i in body.document_ids if i in owned])
    await db.commit()
    await manager.broadcast(meeting.id, Event.MEETING_DOCUMENTS, {"mode": "all" if body.document_ids is None else "selected"})
    return await get_meeting_documents(meeting_id, user, db)
