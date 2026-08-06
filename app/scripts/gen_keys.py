"""Generate the secrets required by ``.env``.

Usage:
    python -m app.scripts.gen_keys

Prints a ``MASTER_ENCRYPTION_KEY`` (Fernet key) and a ``JWT_SECRET_KEY`` to
stdout. These are secrets — store them in your secret manager / environment,
never commit them.
"""

from __future__ import annotations

import secrets

from app.security.crypto import generate_master_key


def main() -> None:
    print("# Add these to your .env (keep them secret!)")
    print(f"MASTER_ENCRYPTION_KEY={generate_master_key()}")
    print(f"JWT_SECRET_KEY={secrets.token_urlsafe(48)}")


if __name__ == "__main__":
    main()
