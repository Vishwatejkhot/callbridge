"""Staff login. Each staff user belongs to exactly one clinic; the token carries that clinic_id and
every dashboard query is scoped by it (and by Postgres RLS underneath)."""

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, UTC

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from callbridge.config import get_settings

_ITER = 310_000
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(digest))
    except ValueError:
        return False


class Staff(BaseModel):
    user_id: int
    clinic_id: int
    email: str
    role: str


def issue_token(user_id: int, clinic_id: int, email: str, role: str) -> str:
    st = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "clinic_id": clinic_id, "email": email, "role": role,
               "iat": now, "exp": now + timedelta(minutes=st.jwt_ttl_minutes)}
    return jwt.encode(payload, st.jwt_secret, algorithm="HS256")


def decode(token: str) -> Staff:
    try:
        p = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from e
    return Staff(user_id=int(p["sub"]), clinic_id=int(p["clinic_id"]), email=p["email"], role=p["role"])


def current_staff(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Staff:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")
    return decode(creds.credentials)


def owner_only(staff: Staff = Depends(current_staff)) -> Staff:
    if staff.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner only")
    return staff
