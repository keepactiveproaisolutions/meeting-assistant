"""Password hashing and JWT helpers."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt

from app.core.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: UUID) -> str:
    s = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=s.access_token_expire_minutes)
    return jwt.encode({"sub": str(user_id), "exp": expires}, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> UUID | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        return UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
