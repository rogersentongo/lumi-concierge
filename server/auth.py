"""Lumi — quick device-token auth (no passwords).

A guest token is `guest_uuid.HMAC(secret, guest_uuid)`, stored in the browser's
localStorage. Possession-based identity; the HMAC stops trivial forgery.
"""
import hashlib
import hmac
import os
import uuid

SECRET = os.environ.get("LUMI_AUTH_SECRET", "dev-insecure-change-me").encode()


def _sig(guest_id: str) -> str:
    return hmac.new(SECRET, guest_id.encode(), hashlib.sha256).hexdigest()[:16]


def issue_token():
    """-> (token, guest_id) for a brand-new device."""
    gid = str(uuid.uuid4())
    return f"{gid}.{_sig(gid)}", gid


def verify_token(token: str):
    """-> guest_id if the token is valid, else None."""
    if not token or "." not in token:
        return None
    gid, sig = token.rsplit(".", 1)
    return gid if hmac.compare_digest(sig, _sig(gid)) else None
