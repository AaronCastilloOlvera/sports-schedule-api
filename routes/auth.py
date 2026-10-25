from fastapi import APIRouter, Request
from pydantic import BaseModel

from utils import auth

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _client_ip(request: Request) -> str:
    # Behind Railway's proxy request.client is the proxy (100.64.x.x); the last
    # X-Forwarded-For entry is the one the proxy itself appended, so it can't be spoofed.
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.split(',')[-1].strip()
    return request.client.host if request.client else 'unknown'


@router.post("/login")
def login(body: LoginRequest, request: Request):
    """Devuelve un JWT para el admin. Público por diseño — es la única puerta de entrada."""
    return auth.login(body.username, body.password, _client_ip(request))
