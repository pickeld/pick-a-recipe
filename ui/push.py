"""Web Push delivery for recipe job outcomes.

Socket.IO already tells the browser the moment a job changes state, but only
while a tab is open. Extraction runs for minutes, so the signal that actually
matters -- your recipe is ready, needs approval, or failed -- almost always
lands after the user has moved on. This module delivers that signal through
the OS instead.

Everything here is best-effort. A push service being slow or down must never
hold up or fail a recipe: sends run off the worker thread and every failure is
swallowed after pruning whatever is no longer deliverable.

VAPID keys
----------
The VAPID pair identifies this server to the browser's push service. The
private key is a credential, so it is never committed and never hardcoded:
VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY from the environment win, and otherwise a
P-256 pair is generated on first use from the platform CSPRNG and persisted to
the config table. That keeps a self-hosted install working with no setup while
still letting an operator manage the key properly.
"""

import base64
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from config import config, set_config_value
from database import delete_push_subscription, get_push_subscriptions

logger = logging.getLogger(__name__)

# Soft import: an install that has not run `pip install -r requirements.txt`
# since this landed should lose notifications, not the whole app.
try:
    from pywebpush import WebPushException, webpush
    _PYWEBPUSH_IMPORT_ERROR: Optional[str] = None
except ImportError as exc:  # pragma: no cover - exercised only on stale installs
    webpush = None  # type: ignore[assignment]
    WebPushException = Exception  # type: ignore[assignment,misc]
    _PYWEBPUSH_IMPORT_ERROR = str(exc)

# Two workers is plenty: one notification per job, and each send is a single
# short HTTPS POST. Bounded on purpose so an unreachable push service backs up
# here instead of spawning a thread per job.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='webpush')
_key_lock = threading.Lock()

# Push services reject anything larger, and payloads are encrypted before the
# limit is applied, so leave room rather than measuring the ciphertext.
_MAX_BODY_CHARS = 300

# How long a push service should hold a notification for a device that is
# offline. pywebpush defaults this to 0, which means "deliver only if the
# device is connected this instant, otherwise discard" -- the exact case this
# feature exists for, since extraction finishes long after the user left.
_DEFAULT_TTL_SECONDS = 6 * 60 * 60

# Subscriptions the push service has permanently disowned -- the browser was
# uninstalled, or the user revoked permission. Any other status is transient
# and the row is kept.
_DEAD_SUBSCRIPTION_STATUSES = frozenset({404, 410})


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _generate_keypair() -> tuple[str, str]:
    """Mint a P-256 VAPID pair, base64url-encoded as the Web Push spec wants."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, 'big')
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return _b64url(public_raw), _b64url(private_raw)


def _stored_keys() -> tuple[str, str]:
    """Current VAPID pair: environment first, then the config table.

    Environment wins here, the opposite of settings-backed values like the USDA
    key. Nobody types a VAPID key into the settings UI -- the stored pair is
    self-generated -- so an operator who sets the environment explicitly is
    making the more deliberate statement of the two.
    """
    env_public = (os.environ.get('VAPID_PUBLIC_KEY') or '').strip()
    env_private = (os.environ.get('VAPID_PRIVATE_KEY') or '').strip()
    if env_public and env_private:
        return env_public, env_private

    config.reload()
    return config.VAPID_PUBLIC_KEY, config.VAPID_PRIVATE_KEY


def ensure_keys() -> tuple[str, str]:
    """Return the VAPID pair, generating and persisting one if needed."""
    public, private = _stored_keys()
    if public and private:
        return public, private

    with _key_lock:
        # Re-read inside the lock: a concurrent caller may have just made one,
        # and a second pair would silently invalidate every live subscription.
        public, private = _stored_keys()
        if public and private:
            return public, private

        public, private = _generate_keypair()
        stored = set_config_value('vapid_public_key', public) and set_config_value(
            'vapid_private_key', private
        )
        if not stored:
            logger.error(
                'Could not persist the generated VAPID keys. Notifications are '
                'disabled: an unsaved pair would change on restart and silently '
                'break every subscription.'
            )
            return '', ''

        config.reload()
        logger.info('Generated a VAPID key pair for Web Push notifications.')
        return public, private


def is_available() -> bool:
    """Whether a push can actually be sent right now."""
    if webpush is None:
        return False
    public, private = ensure_keys()
    return bool(public and private)


def get_public_key() -> str:
    """The applicationServerKey the browser needs to subscribe."""
    if webpush is None:
        return ''
    return ensure_keys()[0]


def _vapid_claims() -> dict:
    """Contact details the push service can use to reach the operator.

    The spec wants a real mailto:/https: URI. VAPID_SUBJECT overrides; the
    default is a non-routable placeholder, which push services accept.
    """
    subject = (os.environ.get('VAPID_SUBJECT') or config.VAPID_SUBJECT or '').strip()
    if not subject:
        subject = 'mailto:admin@pick-a-recipe.invalid'
    return {'sub': subject}


def _truncate(text: str) -> str:
    text = ' '.join((text or '').split())
    if len(text) <= _MAX_BODY_CHARS:
        return text
    return text[: _MAX_BODY_CHARS - 1].rstrip() + '…'


def _deliver(username: str, payload: dict, ttl: int) -> None:
    """Fan a payload out to every device the user has registered."""
    subscriptions = get_push_subscriptions(username)
    if not subscriptions:
        return

    public, private = ensure_keys()
    if not private:
        return

    claims = _vapid_claims()
    encoded = json.dumps(payload)

    for sub in subscriptions:
        endpoint = sub['endpoint']
        try:
            webpush(
                subscription_info={
                    'endpoint': endpoint,
                    'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']},
                },
                data=encoded,
                vapid_private_key=private,
                vapid_claims=dict(claims),
                ttl=ttl,
                timeout=10,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status in _DEAD_SUBSCRIPTION_STATUSES:
                # The endpoint is gone for good. Drop it so a dead browser does
                # not earn a failed request on every future recipe.
                delete_push_subscription(endpoint, username=username)
                logger.info('Dropped an expired push subscription for %s.', username)
            else:
                logger.warning(
                    'Push to %s failed (status %s): %s', username, status, exc
                )
        except Exception:
            # Never let a notification take a recipe down with it.
            logger.exception('Unexpected error pushing to %s.', username)


def notify_user(
    username: Optional[str],
    *,
    title: str,
    body: str,
    url: str = '/',
    tag: Optional[str] = None,
    ttl: int = _DEFAULT_TTL_SECONDS,
) -> None:
    """Queue a notification. Returns immediately; delivery happens off-thread.

    Called from job worker threads, so it must not raise and must not block:
    the caller is in the middle of finishing someone's recipe.

    `ttl` is how long the push service should keep trying to reach an offline
    device. Shorten it for anything that goes stale -- a notification that
    arrives after the thing it describes has lapsed is worse than none.
    """
    if not username:
        return
    if webpush is None:
        if _PYWEBPUSH_IMPORT_ERROR:
            logger.debug(
                'Skipping notification, pywebpush is unavailable: %s',
                _PYWEBPUSH_IMPORT_ERROR,
            )
        return

    payload = {
        'title': title,
        'body': _truncate(body),
        'url': url,
        # Collapses supersedable notifications for the same job, so a user who
        # was away does not come back to three cards about one recipe.
        'tag': tag or 'pick-a-recipe',
    }

    try:
        _executor.submit(_deliver, username, payload, max(0, ttl))
    except RuntimeError:
        # Interpreter shutting down. Nothing worth reporting.
        pass
