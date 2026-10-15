import logging
import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWKSetError

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")      # fail-fast przy imporcie
JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
ISSUER = f"{SUPABASE_URL}/auth/v1"
AUDIENCE = "authenticated"
ALGORITHMS = ["ES256", "RS256"]                            # jawna lista — nigdy z nagłówka tokenu

_jwks = PyJWKClient(JWKS_URL, cache_keys=True, timeout=5)
_bearer = HTTPBearer(auto_error=True)

_UNAUTHORIZED = HTTPException(401, "Invalid token", headers={"WWW-Authenticate": "Bearer"})
_UNAVAILABLE = HTTPException(503, "Auth provider unavailable")


def verify_token(token: str) -> str:
    """Zwraca `sub` (user_id). 401 = zły token, 503 = nie da się sprawdzić."""
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
    except PyJWKClientConnectionError:          # PRZED PyJWKClientError — to jego podklasa
        log.warning("JWKS unreachable")
        raise _UNAVAILABLE
    except PyJWKSetError:                        # brak użytecznych kluczy (np. projekt tylko HS256)
        log.error("JWKS has no usable keys")
        raise _UNAVAILABLE
    except PyJWKClientError as e:
        log.warning("token rejected (key): %s: %s", type(e).__name__, e)
        raise _UNAUTHORIZED
    except jwt.PyJWTError as e:
        log.warning("token rejected (header): %s: %s", type(e).__name__, e)
        raise _UNAUTHORIZED
    

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=30,                       # rozjazd zegarów klient/serwer (s)
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as e:
        log.info("token rejected: %s", type(e).__name__)     # typ, nigdy token
        raise _UNAUTHORIZED
    return claims["sub"]


def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    """Zależność FastAPI. `def`, NIE `async def` — pobranie JWKS to blokujące HTTP."""
    return verify_token(cred.credentials)