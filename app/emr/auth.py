"""Login, roles, CSRF protection and API tokens.

Passwords are hashed with scrypt (standard library). There are no default
passwords: initial users are created from environment variables at start-up.
API clients exchange a username and password for a signed bearer token.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Depends, Form, Header, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
TOKEN_SECONDS = 8 * 3600
ROLES = {"NURSE": "Nurse", "ADMIN": "Administrator"}
_failures: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def locked_out(username: str) -> bool:
    recent = [t for t in _failures.get(username, []) if time.time() - t < LOCKOUT_SECONDS]
    _failures[username] = recent
    return len(recent) >= MAX_FAILURES


def authenticate(db: Session, username: str, password: str) -> User | None:
    username = username.strip().lower()
    if locked_out(username):
        return None
    user = db.scalar(select(User).where(User.username == username, User.active.is_(True)))
    if user and verify_password(password, user.password_hash):
        _failures.pop(username, None)
        return user
    _failures.setdefault(username, []).append(time.time())
    return None


def ensure_users_from_env(db: Session) -> list[str]:
    """Create the initial accounts named in EMR_USERS if they do not exist.

    EMR_USERS="username:ROLE:Full Name,..." and each password in
    EMR_PASSWORD_<USERNAME>. An account without a password is skipped.
    """
    created = []
    for entry in filter(None, (e.strip() for e in os.environ.get("EMR_USERS", "").split(","))):
        username, role, full_name = (part.strip() for part in entry.split(":", 2))
        password = os.environ.get(f"EMR_PASSWORD_{username.upper().replace('-', '_').replace('.', '_')}")
        if not password or db.scalar(select(User.id).where(User.username == username)):
            continue
        if len(password) < 12:
            raise RuntimeError(f"Password for {username} must be at least 12 characters")
        if role not in ROLES:
            raise RuntimeError(f"Unknown role {role} for {username}")
        db.add(User(username=username, role=role, full_name=full_name, password_hash=hash_password(password)))
        created.append(username)
    db.commit()
    return created


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.active:
        raise LoginRequired()
    return user


def admin(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


def check_csrf(request: Request, csrf: str = Form("")) -> None:
    expected = request.session.get("csrf")
    if not expected or not hmac.compare_digest(expected, csrf):
        raise HTTPException(status_code=400, detail="Form expired; reload the page and try again")


# ---------------------------------------------------------------- API tokens

_serializer: URLSafeTimedSerializer | None = None


def init_tokens(secret_key: str) -> None:
    global _serializer
    _serializer = URLSafeTimedSerializer(secret_key, salt="emr-api-token")


def issue_token(user: User) -> str:
    return _serializer.dumps({"uid": user.id})


def api_user(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        data = _serializer.loads(authorization[7:].strip(), max_age=TOKEN_SECONDS)
    except SignatureExpired:
        raise HTTPException(401, "Token expired", headers={"WWW-Authenticate": "Bearer"})
    except BadSignature:
        raise HTTPException(401, "Invalid token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, data.get("uid"))
    if user is None or not user.active:
        raise HTTPException(401, "Unknown user", headers={"WWW-Authenticate": "Bearer"})
    return user
