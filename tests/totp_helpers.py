"""TOTP helpers for tests.

Codes are single-use: the server records the time step each accepted code came
from and refuses anything at or below it. Tests that need two codes in a row
therefore have to reach into a *different* step, which ``totp_next`` does — it
returns the code for the following 30-second window, still inside the server's
±1-step drift tolerance so it verifies.
"""

from __future__ import annotations

import time

import pyotp


def totp_now(secret: str) -> str:
    """The code for the current time step."""
    return pyotp.TOTP(secret).now()


def totp_next(secret: str) -> str:
    """The code for the next time step (accepted via the drift window)."""
    totp = pyotp.TOTP(secret)
    return totp.at(time.time() + totp.interval)
