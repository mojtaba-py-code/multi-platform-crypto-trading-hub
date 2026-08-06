"""Cryptographic primitives for secrets at rest and password hashing.

Two independent concerns live here:

* :class:`SecretCipher` — authenticated symmetric encryption (Fernet / AES-128-CBC
  + HMAC-SHA256) used to protect exchange API secrets and passphrases before they
  touch the database. Built on :class:`~cryptography.fernet.MultiFernet` so keys can
  be rotated: the first key encrypts, any key can decrypt.

* :class:`PasswordHasher` — Argon2id password hashing for user login credentials.

The plaintext of an exchange secret exists only transiently in memory while a
request is being served; it is never logged (see ``core.logging``) and never
stored unencrypted.
"""

from __future__ import annotations

import functools

import anyio.to_thread
from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings
from app.core.exceptions import EncryptionError
from app.core.logging import get_logger

log = get_logger(__name__)


def generate_master_key() -> str:
    """Generate a fresh urlsafe base64 32-byte key suitable for ``SecretCipher``."""
    return Fernet.generate_key().decode("ascii")


class SecretCipher:
    """Encrypt/decrypt small secrets with key-rotation support.

    ``keys`` is an ordered list of Fernet keys. The first key is the *active*
    key used for encryption; every key is tried for decryption, which lets an
    operator introduce a new key and re-encrypt lazily without downtime.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise EncryptionError("SecretCipher requires at least one key.")
        try:
            fernets = [Fernet(key.encode("ascii")) for key in keys]
        except (ValueError, TypeError) as exc:
            raise EncryptionError("Invalid encryption key material.") from exc
        self._multi = MultiFernet(fernets)

    def encrypt(self, plaintext: str) -> str:
        """Return an opaque ciphertext token for ``plaintext``."""
        if plaintext is None:
            raise EncryptionError("Cannot encrypt None.")
        token = self._multi.encrypt(plaintext.encode("utf-8"))
        return token.decode("ascii")

    def decrypt(self, token: str) -> str:
        """Recover the plaintext from a ciphertext token."""
        try:
            plaintext = self._multi.decrypt(token.encode("ascii"))
        except (InvalidToken, ValueError) as exc:
            # Never include the token or key material in the error.
            raise EncryptionError("Failed to decrypt secret (bad key or corrupt data).") from exc
        return plaintext.decode("utf-8")

    def rotate(self, token: str) -> str:
        """Re-encrypt ``token`` under the active (first) key."""
        try:
            return self._multi.rotate(token.encode("ascii")).decode("ascii")
        except (InvalidToken, ValueError) as exc:
            raise EncryptionError("Failed to rotate secret.") from exc


class PasswordHasher:
    """Argon2id password hashing with transparent rehash-on-verify.

    Argon2id is deliberately expensive: at these parameters a single hash or
    verify burns ~50-100 ms of CPU. Calling it directly from an ``async``
    request handler would block the event loop for that whole time — a handful
    of concurrent logins would stall every other request in the process. The
    ``*_async`` variants therefore run the work on a worker thread (argon2-cffi
    releases the GIL during hashing), and application code must prefer them.
    The synchronous methods remain for non-async callers such as CLI tools,
    Celery tasks, and unit tests.
    """

    def __init__(self) -> None:
        # Parameters chosen for interactive login on commodity hardware.
        self._hasher = _Argon2Hasher(
            time_cost=3,
            memory_cost=64 * 1024,  # 64 MiB
            parallelism=2,
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, hashed: str, password: str) -> bool:
        try:
            self._hasher.verify(hashed, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
        return True

    def needs_rehash(self, hashed: str) -> bool:
        return self._hasher.check_needs_rehash(hashed)

    async def hash_async(self, password: str) -> str:
        """Hash off the event loop. See the class docstring."""
        return await anyio.to_thread.run_sync(self.hash, password)

    async def verify_async(self, hashed: str, password: str) -> bool:
        """Verify off the event loop. See the class docstring."""
        return await anyio.to_thread.run_sync(self.verify, hashed, password)


@functools.lru_cache(maxsize=1)
def get_secret_cipher() -> SecretCipher:
    """Return the process-wide :class:`SecretCipher`.

    ``MASTER_ENCRYPTION_KEY`` may hold a comma-separated list of Fernet keys to
    support rotation: the **first** key encrypts, and every key is tried on
    decrypt. To rotate, prepend a new key and keep the old one until all stored
    ciphertexts have been re-encrypted, then drop it.

    In development/test an ephemeral key is generated so the app runs without
    configuration; production is required to supply the key (enforced by
    ``Settings.validate_runtime``).
    """
    settings = get_settings()
    keys = settings.encryption_keys
    if not keys:
        if settings.is_production:
            raise EncryptionError("MASTER_ENCRYPTION_KEY is required in production.")
        keys = [generate_master_key()]
        log.warning("using_ephemeral_encryption_key", env=str(settings.app_env))
    return SecretCipher(keys)


@functools.lru_cache(maxsize=1)
def get_password_hasher() -> PasswordHasher:
    return PasswordHasher()
