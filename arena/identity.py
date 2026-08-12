"""Player identity: an email becomes a hash and a mask, never a stored address.

The email exists only so one player keeps one place on the leaderboard across
repeat plays. Nothing here can recover the address it was handed.
"""

import base64
import hashlib
import hmac


def normalise_email(email):
    """Trim and lower-case, so `Alex@Example.com ` and `alex@example.com` match."""
    return email.strip().lower()


def hash_email(email, salt):
    """Salted SHA-256 of the normalised address, hex encoded."""
    return hashlib.sha256(f"{salt}:{normalise_email(email)}".encode()).hexdigest()


def mask_email(email):
    """`alex@example.com` -> `a***x@example.com`. The board renders only this."""
    local, _, domain = normalise_email(email).partition("@")
    if len(local) >= 2:
        local = f"{local[0]}***{local[-1]}"
    elif local:
        # One letter, so showing it twice would give the whole thing away.
        local = f"{local[0]}***"
    return f"{local}@{domain}"


def sign_token(player_id, secret):
    """A session token: the player id plus an HMAC over it."""
    body = str(player_id)
    return f"{body}.{_mac(body, secret)}"


def verify_token(token, secret):
    """Return the player id, or None if this was not signed with `secret`."""
    body, _, mac = (token or "").partition(".")
    if not body.isdigit() or not hmac.compare_digest(mac, _mac(body, secret)):
        return None
    return int(body)


def _mac(body, secret):
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
