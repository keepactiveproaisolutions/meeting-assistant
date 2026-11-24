"""Auth endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User, Workspace, WorkspaceMember
from app.schemas.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _detect_lan_ip() -> str | None:
    from app.core.config import detect_private_lan_ip
    return detect_private_lan_ip()


@router.get("/config")
async def auth_config():
    s = get_settings()
    lan_ip = _detect_lan_ip()
    return {
        "dev_auth_bypass": s.dev_auth_bypass,
        "allow_lan_dev": s.allow_lan_dev,
        "llm_mock": s.llm_mock,
        "environment": s.environment,
        "app_name": s.app_name,
        "lan_ip": lan_ip,
        "lan_web_url": f"http://{lan_ip}:5173" if lan_ip else None,
        "security_notice": (
            "Local development mode — DEV_AUTH_BYPASS is enabled. Anyone on the local network can join if ALLOW_LAN_DEV is enabled; not authenticated security."
            if s.dev_auth_bypass
            else "Authenticated JWT mode."
        ),
    }


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=email, full_name=body.full_name, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    ws = Workspace(name=body.workspace_name or f"{body.full_name}'s workspace", owner_id=user.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
