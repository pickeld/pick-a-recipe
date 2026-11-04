"""Bearer tokens for the calendar feed and read-only share links.

The token is shown to the user once and only its SHA-256 is stored, so a copy
of the database does not hand out working links. Regenerating replaces the
hash, which revokes every copy of the old link.
"""

from __future__ import annotations

import hashlib
import secrets

from kitchen.db import connect


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_shape(token: str) -> bool:
    return bool(token) and 20 <= len(token) <= 100 and all(c.isalnum() or c in "-_" for c in token)


def rotate_ics_token(username: str) -> str:
    token = new_token()
    with connect() as conn:
        conn.execute(
            "INSERT INTO ics_tokens(username, token_hash) VALUES (?, ?)"
            " ON CONFLICT(username) DO UPDATE SET token_hash = excluded.token_hash,"
            " created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
            (username, token_hash(token)),
        )
        conn.commit()
    return token


def revoke_ics_token(username: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM ics_tokens WHERE username = ?", (username,))
        conn.commit()


def has_ics_token(username: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM ics_tokens WHERE username = ?", (username,)).fetchone() is not None


def ics_user(token: str) -> str | None:
    if not _valid_shape(token):
        return None
    with connect() as conn:
        row = conn.execute("SELECT username FROM ics_tokens WHERE token_hash = ?", (token_hash(token),)).fetchone()
    return row["username"] if row else None


def create_share_link(recipe_id: str, created_by: str | None) -> str:
    token = new_token()
    with connect() as conn:
        conn.execute(
            "INSERT INTO share_links(token_hash, recipe_id, created_by) VALUES (?, ?, ?)",
            (token_hash(token), recipe_id, created_by),
        )
        conn.commit()
    return token


def revoke_share_links(recipe_id: str) -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM share_links WHERE recipe_id = ?", (recipe_id,))
        conn.commit()
        return cur.rowcount


def share_count(recipe_id: str) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM share_links WHERE recipe_id = ?", (recipe_id,)).fetchone()[0]


def shared_recipe(token: str) -> str | None:
    if not _valid_shape(token):
        return None
    with connect() as conn:
        row = conn.execute("SELECT recipe_id FROM share_links WHERE token_hash = ?", (token_hash(token),)).fetchone()
    return row["recipe_id"] if row else None
