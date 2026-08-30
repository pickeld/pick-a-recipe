"""Mobile JWT token issuance and verification.

The Android app authenticates through Authentik via a browser round-trip that
hands back an access/refresh JWT pair over a deep-link redirect (see
/api/mobile/auth/login-url in ui/app.py). Tokens are HS256-signed with
JWT_SECRET_KEY; without that env var the whole mobile surface stays disabled.
"""

import os
import time

import jwt

ACCESS_TTL_SECONDS = 900
REFRESH_TTL_SECONDS = 30 * 24 * 3600

_ALGORITHM = 'HS256'
_TYPE_CLAIM = 'type'


def mobile_auth_enabled() -> bool:
    return bool(os.environ.get('JWT_SECRET_KEY', '').strip())


def issue_token_pair(username: str, *, is_admin: bool = False,
                     refresh_ttl: int = REFRESH_TTL_SECONDS) -> dict:
    secret = os.environ['JWT_SECRET_KEY']
    now = int(time.time())
    base_claims = {'sub': username, 'adm': is_admin, 'iat': now}
    return {
        'access_token': jwt.encode(
            {**base_claims, _TYPE_CLAIM: 'access', 'exp': now + ACCESS_TTL_SECONDS},
            secret, algorithm=_ALGORITHM),
        'refresh_token': jwt.encode(
            {**base_claims, _TYPE_CLAIM: 'refresh', 'exp': now + refresh_ttl},
            secret, algorithm=_ALGORITHM),
        'token_type': 'Bearer',
        'expires_in': ACCESS_TTL_SECONDS,
    }


def decode_token(token: str, *, expected_type: str) -> dict | None:
    """Return claims for a valid `token` of `expected_type`, else None."""
    if not token or not mobile_auth_enabled():
        return None
    try:
        payload = jwt.decode(
            token, os.environ['JWT_SECRET_KEY'],
            algorithms=[_ALGORITHM],
            options={'require': ['exp', 'sub', _TYPE_CLAIM]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get(_TYPE_CLAIM) != expected_type:
        return None
    return payload
