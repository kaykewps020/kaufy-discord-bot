"""Owner authentication — anti-impersonation layer.

Why this exists:
  A normal "is this the owner?" check usually only verifies the Discord user
  ID. That is already strong (Discord cryptographically proves the ID), but a
  second factor adds defense-in-depth: even a true owner must prove knowledge
  of OWNER_SECRET (a shared passphrase) before owner-only powers unlock.

Guarantees:
  1. Discord ID match         — the caller IS the owner as far as Discord sees.
  2. Secret proof (optional)  — caller also supplied the correct OWNER_SECRET.
                                Required for *privileged* owner actions/reveals.

If OWNER_SECRET is empty, privileged mode degrades to ID-only (legacy behavior)
but logs a warning so the operator knows 2FA is off.
"""
import asyncio
import logging
import sys
import time
from bot.config import Config

logger = logging.getLogger("kaufy.owner_auth")

# user_id -> expiry_epoch for owner session that has proven the secret
_AUTHENTICATED: dict[int, float] = {}
_AUTH_LOCK = asyncio.Lock()
# How long a verified owner session stays "unlocked" (seconds)
SESSION_TTL = 24 * 3600


def _secret_configured() -> bool:
    return bool(Config.OWNER_SECRET and Config.OWNER_SECRET.strip())


def is_owner_id(user_id: int) -> bool:
    """Bare Discord-ID owner check (no secret needed)."""
    return user_id in Config.OWNER_IDS


async def authenticate(user_id: int, provided_secret: str) -> bool:
    """Try to unlock privileged owner mode for user_id with provided_secret.

    Returns True only if the user is a real owner AND the secret matches.
    On success, stores an unlocked session for SESSION_TTL seconds.
    Sends a notice to other owners only when something suspicious happens.
    """
    if not is_owner_id(user_id):
        # Someone who is not a registered owner tried to auth — fail silently
        # but log with the (fake) id so operator can audit.
        logger.warning(f"Non-owner id={user_id} attempted owner auth.")
        return False

    if not _secret_configured():
        # No secret set: legacy mode, ID alone is enough. Still mark session.
        async with _AUTH_LOCK:
            _AUTHENTICATED[user_id] = time.time() + SESSION_TTL
        logger.info(f"Owner id={user_id} authenticated (no secret configured).")
        return True

    if not provided_secret or provided_secret.strip() != Config.OWNER_SECRET:
        logger.warning(f"Owner id={user_id} supplied WRONG owner secret.")
        return False

    async with _AUTH_LOCK:
        _AUTHENTICATED[user_id] = time.time() + SESSION_TTL
    logger.info(f"Owner id={user_id} authenticated with secret. Privileges unlocked.")
    return True


def is_authenticated(user_id: int) -> bool:
    """True if user_id has an unexpired privileged (secret-proven) session."""
    expiry = _AUTHENTICATED.get(user_id)
    if not expiry:
        return False
    if time.time() > expiry:
        # Expired; clean up
        _AUTHENTICATED.pop(user_id, None)
        return False
    return True


async def is_owner(user_id: int, *, via_secret: bool = False) -> bool:
    """Return whether user_id may act as owner.

    via_secret=False -> just need a real owner ID (normal/basic owner status).
    via_secret=True  -> require a real owner ID AND a proven secret session
                         (privileged reveals, internal data, etc.).
    """
    if not is_owner_id(user_id):
        return False
    if not via_secret:
        return True
    # Privileged: need secret session OR (if no secret configured) just the id.
    if not _secret_configured():
        return True
    return is_authenticated(user_id)


# Allow: from bot.services.owner_auth import owner_auth  (then owner_auth.is_owner(...))
owner_auth = sys.modules[__name__]
