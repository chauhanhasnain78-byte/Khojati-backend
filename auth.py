"""
Khojati Inventory Management System - Authentication Module.

Provides PIN-based admin authentication with JWT token issuance using native bcrypt.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import settings

# ── Bearer Token Scheme ────────────────────────────────────────────────
bearer_scheme = HTTPBearer(
    scheme_name="Admin JWT",
    description="Admin access token obtained via PIN verification",
)


# ── PIN Verification ───────────────────────────────────────────────────

def verify_pin(pin: str) -> bool:
    """
    Verify a plaintext PIN against the stored bcrypt hash.

    Args:
        pin: The plaintext PIN entered by the admin.

    Returns:
        True if the PIN matches, False otherwise.
    """
    try:
        return bcrypt.checkpw(pin.encode("utf-8"), settings.ADMIN_PIN_HASH.encode("utf-8"))
    except Exception:
        return False


def hash_pin(pin: str) -> str:
    """
    Generate a bcrypt hash for a given plaintext PIN.

    Args:
        pin: The plaintext PIN to hash.

    Returns:
        The bcrypt hash string.
    """
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pin.encode("utf-8"), salt).decode("utf-8")


# ── JWT Token Management ──────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    """
    Create a signed JWT access token.

    Args:
        data: Claims to encode in the token payload.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT access token.

    Args:
        token: The encoded JWT string.

    Returns:
        The decoded payload dictionary.

    Raises:
        HTTPException: If the token is invalid or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI Dependency ─────────────────────────────────────────────────

def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    FastAPI dependency that validates the JWT bearer token.

    Use this as a dependency on any endpoint that requires admin access.

    Args:
        credentials: The HTTP Bearer credentials extracted from the
            Authorization header.

    Returns:
        The decoded JWT payload (contains ``sub`` and ``role``).

    Raises:
        HTTPException 401: If the token is missing, invalid, or expired.
    """
    payload = decode_access_token(credentials.credentials)
    role = payload.get("role")
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Insufficient privileges. Admin access required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload
