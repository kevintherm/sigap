"""Passwords, opaque tokens and admin-panel sessions.

Secrets (session cookies, invite codes, gateway tokens) are random and only their SHA-256 is
stored, so a leaked database does not leak working credentials. Passwords use argon2id.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlmodel import Session, select

from .models import AuthSession, User

_ph = PasswordHasher()
SESSION_TTL = timedelta(hours=12)
ROLES = ("admin", "staff", "lecturer")


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret(prefix: str = "", nbytes: int = 24) -> str:
    return prefix + secrets.token_urlsafe(nbytes)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; stored datetimes are UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_session(db: Session, user: User) -> str:
    token = new_secret()
    db.add(AuthSession(token_hash=sha256(token), user_id=user.id, expires_at=now_utc() + SESSION_TTL))
    db.commit()
    return token


def user_for_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    s = db.get(AuthSession, sha256(token))
    if not s or as_utc(s.expires_at) < now_utc():
        return None
    user = db.get(User, s.user_id)
    return user if user and user.active else None


def end_session(db: Session, token: str | None) -> None:
    if token and (s := db.get(AuthSession, sha256(token))):
        db.delete(s)
        db.commit()


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.exec(select(User).where(User.email == email.strip().lower())).first()
    if not user:
        verify_password(_DUMMY_HASH, password)  # same work whether or not the email exists
        return None
    return user if verify_password(user.password_hash, password) and user.active else None


_DUMMY_HASH = _ph.hash("not-a-real-password-just-timing")
