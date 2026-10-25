"""
Single-admin JWT auth. Credentials live in env vars (no users table):
ADMIN_USERNAME, ADMIN_PASSWORD_HASH (bcrypt), JWT_SECRET, JWT_EXPIRE_HOURS.

Fails closed: if any of them is missing, login is refused and every protected
route returns 401 — a config mistake never leaves the API open.
"""
import hmac
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ALGORITHM = 'HS256'
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW = 15 * 60  # seconds

_bearer = HTTPBearer(auto_error=False)
_failed_logins = defaultdict(deque)  # client ip -> timestamps; in-memory, resets on restart


def _config():
    return (os.getenv('ADMIN_USERNAME'), os.getenv('ADMIN_PASSWORD_HASH'), os.getenv('JWT_SECRET'))


def _unauthorized(detail='No autenticado'):
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={'WWW-Authenticate': 'Bearer'})


def _too_many_attempts(ip):
    attempts = _failed_logins[ip]
    while attempts and attempts[0] < time.time() - FAILED_LOGIN_WINDOW:
        attempts.popleft()
    return len(attempts) >= MAX_FAILED_LOGINS


def login(username: str, password: str, client_ip: str) -> dict:
    admin_user, password_hash, secret = _config()
    if not (admin_user and password_hash and secret):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Autenticación no configurada')
    if _too_many_attempts(client_ip):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail='Demasiados intentos. Espera unos minutos.')

    # Always run bcrypt, even for a wrong username, so response time doesn't reveal which part failed.
    password_ok = bcrypt.checkpw(password.encode(), password_hash.encode())
    user_ok = hmac.compare_digest(username, admin_user)
    if not (password_ok and user_ok):
        _failed_logins[client_ip].append(time.time())
        raise _unauthorized('Usuario o contraseña incorrectos')

    _failed_logins.pop(client_ip, None)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=float(os.getenv('JWT_EXPIRE_HOURS', '24')))
    token = jwt.encode({'sub': admin_user, 'exp': expires_at}, secret, algorithm=ALGORITHM)
    return {'access_token': token, 'token_type': 'bearer', 'expires_at': expires_at.isoformat()}


def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """FastAPI dependency: valid, unexpired token for the configured admin, or 401."""
    admin_user, _, secret = _config()
    if not (admin_user and secret) or credentials is None:
        raise _unauthorized()
    try:
        payload = jwt.decode(credentials.credentials, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise _unauthorized('Sesión inválida o vencida')
    if payload.get('sub') != admin_user:
        raise _unauthorized('Sesión inválida o vencida')
    return admin_user
