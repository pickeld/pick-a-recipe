"""Password hashing for local accounts.

Argon2id, via argon2-cffi. Parameters are the library's defaults, which track
the RFC 9106 guidance; they are recorded inside each hash, so raising them
later does not invalidate existing passwords. Verification detects an outdated
hash and callers re-store it (see needs_rehash).
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

# Argon2id is the default type. Memory-hard, so a stolen database resists
# GPU-driven guessing far better than a fast hash would.
_hasher = PasswordHasher()

# NIST SP 800-63B: length is what matters. No composition rules, and a ceiling
# only so an enormous input cannot be used to burn CPU.
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024


class WeakPassword(ValueError):
    """Raised when a password fails the length policy."""


def validate(password: str) -> None:
    """Raise WeakPassword unless the password is an acceptable length.

    Deliberately not a strength meter: character-class rules push people
    towards predictable substitutions without adding real entropy.
    """
    if not isinstance(password, str) or not password:
        raise WeakPassword('Enter a password.')
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f'Password must be at least {MIN_PASSWORD_LENGTH} characters.'
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise WeakPassword(
            f'Password must be at most {MAX_PASSWORD_LENGTH} characters.'
        )


def hash_password(password: str) -> str:
    """Hash a password for storage. Salt is generated per call and embedded."""
    validate(password)
    return _hasher.hash(password)


def verify(stored_hash: str | None, password: str) -> bool:
    """Check a password against a stored hash, in constant time.

    False for every failure mode — wrong password, malformed hash, or a user
    with no password set — so callers cannot accidentally distinguish them and
    leak which accounts exist.
    """
    if not stored_hash or not isinstance(password, str) or not password:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when a hash predates the current cost parameters."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, VerificationError):
        return False


def dummy_verify() -> None:
    """Burn roughly the time a real verification takes.

    Called on the no-such-user path so that a missing account and a wrong
    password take comparable time. Without it, response latency alone
    enumerates valid usernames.
    """
    try:
        _hasher.verify(_DUMMY_HASH, 'not-the-password')
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        pass


# Computed once at import: hashing here would add startup cost on every boot,
# and the value is not a secret — it is a hash of a fixed throwaway string.
_DUMMY_HASH = _hasher.hash('timing-equalisation-placeholder')
