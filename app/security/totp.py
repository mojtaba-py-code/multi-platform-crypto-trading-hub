"""Time-based one-time password (TOTP) two-factor authentication.

The TOTP shared secret is itself a secret and is stored encrypted via
:class:`~app.security.crypto.SecretCipher`, exactly like an exchange API key.

Verification returns the **time step** (counter) a code matched, not just a
boolean. The caller persists the last accepted step per user and refuses any
code at or below it, which makes each code single-use: without that, a code
observed by an attacker (shoulder-surfing, a phishing proxy, a leaked log on
the client side) stays replayable for the whole validity window.
"""

from __future__ import annotations

import hmac
import time

import pyotp

_DEFAULT_ISSUER = "CryptoTradingHub"


class TotpService:
    """Wraps :mod:`pyotp` with a small, testable surface."""

    def __init__(self, *, issuer: str = _DEFAULT_ISSUER, valid_window: int = 1) -> None:
        self._issuer = issuer
        # ``valid_window`` tolerates minor clock drift (± one 30s step).
        self._valid_window = valid_window

    @staticmethod
    def generate_secret() -> str:
        """Return a new base32 TOTP secret."""
        return pyotp.random_base32()

    def provisioning_uri(self, *, secret: str, account_name: str) -> str:
        """Return an ``otpauth://`` URI for QR-code enrolment in an authenticator app."""
        return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=self._issuer)

    def verify_counter(self, *, secret: str, code: str, now: float | None = None) -> int | None:
        """Return the time step ``code`` matches, or ``None`` if it is invalid.

        Every candidate step in the drift window is compared so the result does
        not depend on which step matched; comparisons use
        :func:`hmac.compare_digest` to avoid leaking timing information.
        """
        code = (code or "").strip()
        if not code.isdigit():
            return None
        totp = pyotp.TOTP(secret)
        current = int(time.time() if now is None else now)
        matched: int | None = None
        for offset in range(-self._valid_window, self._valid_window + 1):
            at = current + offset * totp.interval
            if hmac.compare_digest(totp.at(at), code):
                matched = at // totp.interval
        return matched

    def verify(self, *, secret: str, code: str, now: float | None = None) -> bool:
        """Validate a code against the secret (no replay protection)."""
        return self.verify_counter(secret=secret, code=code, now=now) is not None

    def now(self, secret: str) -> str:
        """Return the current code for ``secret`` (used only in tests/tools)."""
        return pyotp.TOTP(secret).now()
