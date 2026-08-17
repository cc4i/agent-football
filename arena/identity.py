"""Player identity: a name that is theirs alone, and an address they may keep.

A name is what the board shows and what the wall calls somebody, so it is
unique across the venue and tidied here before anything compares two of them.

The email is optional and exists only so one player keeps one place on the
leaderboard across repeat plays, on a phone that is not the one they started
on. Nothing here can recover the address it was handed.
"""

import base64
import hashlib
import hmac
import secrets

import codes

# A recovery code proves an address is yours. Six characters from the same
# alphabet room codes use, shown on /home and typed on a phone when claiming a
# row from another device.
RECOVERY_LENGTH = 6


def normalise_name(name):
    """Trim, and collapse every run of whitespace to one space.

    `Alex  Rivera ` and `Alex Rivera` are one person by any reading a human
    gives them, so they are one name here too -- and the difference is removed
    before the name is stored rather than only when two are compared, so the
    board never shows a gap nobody typed on purpose. A name of nothing but
    whitespace comes back empty, which is what refuses it upstream.
    """
    return " ".join(name.split())


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
    # A cookie holds whatever the browser was told to hold, and `compare_digest`
    # refuses a non-ASCII str rather than saying no - which would be a 500 on
    # every route a phone uses. `isdigit` is no guard against reaching it: it is
    # true of Arabic-Indic digits too. Bytes answer every cookie that can be
    # sent, and a real mac is base64, so it is the same bytes either way.
    if not body.isdigit() or not hmac.compare_digest(mac.encode("utf-8", "surrogatepass"),
                                                     _mac(body, secret).encode()):
        return None
    return int(body)


def _mac(body, secret):
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def new_recovery_code():
    """Six characters that prove an address is yours. Read across a room, typed on a phone."""
    return "".join(secrets.choice(codes.ALPHABET) for _ in range(RECOVERY_LENGTH))
