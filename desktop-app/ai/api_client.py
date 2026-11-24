"""REST client for the backend (auth, workspaces, meetings, ask)."""
import httpx

from config.settings import settings


class ApiError(Exception):
    pass


class ApiClient:
    def __init__(self) -> None:
        self.token: str | None = None
        self._http = httpx.Client(base_url=f"{settings.api_url}/api/v1", timeout=30)

    def _req(self, method: str, path: str, **kw):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise ApiError(f"Cannot reach backend: {exc}") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ApiError(str(detail))
        return r.json() if r.content else None

    def get_config(self) -> dict:
        try:
            return self._req("GET", "/auth/config") or {}
        except Exception:
            return {}

    def login(self, email: str, password: str) -> dict:
        data = self._req("POST", "/auth/login", json={"email": email, "password": password})
        self.token = data["access_token"]
        return data

    def workspaces(self) -> list[dict]:
        return self._req("GET", "/workspaces")

    def create_meeting(self, workspace_id: str, title: str) -> dict:
        return self._req("POST", f"/workspaces/{workspace_id}/meetings", json={"title": title})

    def join_meeting(self, code: str) -> dict:
        return self._req("POST", "/meetings/join", json={"code": code})

    def end_meeting(self, meeting_id: str) -> dict:
        return self._req("POST", f"/meetings/{meeting_id}/end")

    def ask(self, meeting_id: str, question: str, selected_text: str | None = None,
            question_id: str | None = None) -> dict:
        return self._req("POST", f"/meetings/{meeting_id}/ask", json={
            "question": question, "selected_text": selected_text, "question_id": question_id})

    def screen_ask(self, meeting_id: str, jpeg: bytes, note: str = "") -> dict:
        """AI Screen Intelligence: upload one screenshot; the answer streams over the meeting WebSocket."""
        return self._req("POST", f"/meetings/{meeting_id}/screen-ask",
                         files={"image": ("screen.jpg", jpeg, "image/jpeg")}, data={"note": note}, timeout=60)

    # ---- Documents -------------------------------------------------------
    def list_documents(self) -> list[dict]:
        return self._req("GET", "/documents")

    def get_document(self, document_id: str) -> dict:
        return self._req("GET", f"/documents/{document_id}")

    def upload_document(self, path: str, doc_type: str) -> dict:
        from pathlib import Path
        p = Path(path)
        with p.open("rb") as fh:
            return self._req("POST", "/documents", files={"file": (p.name, fh.read())},
                             data={"doc_type": doc_type}, timeout=120)

    def replace_document(self, document_id: str, path: str) -> dict:
        from pathlib import Path
        p = Path(path)
        with p.open("rb") as fh:
            return self._req("POST", f"/documents/{document_id}/replace",
                             files={"file": (p.name, fh.read())}, timeout=120)

    def set_document_type(self, document_id: str, doc_type: str) -> dict:
        return self._req("PATCH", f"/documents/{document_id}", json={"doc_type": doc_type})

    def delete_document(self, document_id: str) -> dict:
        return self._req("DELETE", f"/documents/{document_id}")

    def meeting_documents(self, meeting_id: str) -> dict:
        return self._req("GET", f"/meetings/{meeting_id}/documents")

    def set_meeting_documents(self, meeting_id: str, document_ids: list[str] | None) -> dict:
        return self._req("PUT", f"/meetings/{meeting_id}/documents", json={"document_ids": document_ids})

    # ---- Demo Prep -------------------------------------------------------
    def get_demo_prep(self, meeting_id: str | None) -> dict:
        return self._req("GET", "/demo-prep", params={"meeting_id": meeting_id} if meeting_id else None)

    def generate_demo_prep(self, meeting_id: str | None) -> dict:
        return self._req("POST", "/demo-prep/generate",
                         params={"meeting_id": meeting_id} if meeting_id else None, timeout=240)
