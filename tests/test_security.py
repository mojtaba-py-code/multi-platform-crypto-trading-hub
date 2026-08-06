"""Tests for cryptography, tokens, TOTP and RBAC."""

from __future__ import annotations

from datetime import timedelta

import pyotp
import pytest
from app.config.settings import Settings
from app.core.exceptions import EncryptionError, InvalidTokenError
from app.security.crypto import PasswordHasher, SecretCipher, generate_master_key
from app.security.rbac import Permission, Role, has_permission, permissions_for
from app.security.tokens import TokenService
from app.security.totp import TotpService


# --- SecretCipher ----------------------------------------------------------
def test_cipher_roundtrip():
    cipher = SecretCipher([generate_master_key()])
    token = cipher.encrypt("my-api-secret")
    assert token != "my-api-secret"
    assert cipher.decrypt(token) == "my-api-secret"


def test_cipher_ciphertext_is_non_deterministic():
    cipher = SecretCipher([generate_master_key()])
    assert cipher.encrypt("x") != cipher.encrypt("x")  # Fernet includes random IV


def test_cipher_wrong_key_fails():
    a = SecretCipher([generate_master_key()])
    b = SecretCipher([generate_master_key()])
    token = a.encrypt("secret")
    with pytest.raises(EncryptionError):
        b.decrypt(token)


def test_cipher_key_rotation():
    old_key = generate_master_key()
    new_key = generate_master_key()
    old_cipher = SecretCipher([old_key])
    token = old_cipher.encrypt("secret")
    # New cipher lists the new key first, old key second — can still decrypt.
    rotated_cipher = SecretCipher([new_key, old_key])
    assert rotated_cipher.decrypt(token) == "secret"
    fresh = rotated_cipher.rotate(token)
    assert SecretCipher([new_key]).decrypt(fresh) == "secret"


def test_cipher_requires_key():
    with pytest.raises(EncryptionError):
        SecretCipher([])


def test_cipher_rejects_malformed_key_material():
    with pytest.raises(EncryptionError):
        SecretCipher(["not-a-fernet-key"])


# --- Key rotation via configuration ----------------------------------------
def test_settings_expose_comma_separated_keys_in_priority_order():
    old_key, new_key = generate_master_key(), generate_master_key()
    settings = Settings(master_encryption_key=f" {new_key} , {old_key} ")
    assert settings.encryption_keys == [new_key, old_key]


def test_configured_key_list_decrypts_secrets_written_under_the_old_key():
    """Rotation must not strand data: prepend the new key, keep the old one."""
    old_key, new_key = generate_master_key(), generate_master_key()
    stored = SecretCipher([old_key]).encrypt("exchange-api-secret")

    rotated = SecretCipher(Settings(master_encryption_key=f"{new_key},{old_key}").encryption_keys)
    assert rotated.decrypt(stored) == "exchange-api-secret"
    # New writes use the active (first) key, so dropping the old one later is safe.
    assert SecretCipher([new_key]).decrypt(rotated.encrypt("fresh")) == "fresh"


def test_blank_key_configuration_yields_no_keys():
    assert Settings(master_encryption_key="  ,  ").encryption_keys == []


# --- PasswordHasher --------------------------------------------------------
def test_password_hash_and_verify():
    hasher = PasswordHasher()
    hashed = hasher.hash("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert hasher.verify(hashed, "correct horse battery staple")
    assert not hasher.verify(hashed, "wrong password")


def test_password_verify_bad_hash():
    hasher = PasswordHasher()
    assert not hasher.verify("not-a-hash", "whatever")


@pytest.mark.asyncio
async def test_password_async_helpers_match_the_sync_ones():
    """The async variants exist so Argon2 never runs on the event loop."""
    hasher = PasswordHasher()
    hashed = await hasher.hash_async("correct horse battery staple")
    assert await hasher.verify_async(hashed, "correct horse battery staple")
    assert not await hasher.verify_async(hashed, "wrong password")


# --- TokenService ----------------------------------------------------------
def _tokens() -> TokenService:
    return TokenService(
        secret="unit-test-secret-key-abcdefghijklmnop",
        access_ttl=timedelta(minutes=5),
        refresh_ttl=timedelta(days=1),
    )


def test_token_pair_roundtrip():
    svc = _tokens()
    pair = svc.issue_pair(subject="user-1", role="trader")
    claims = svc.decode(pair.access_token, expected_type="access")
    assert claims.subject == "user-1"
    assert claims.role == "trader"
    assert claims.token_type == "access"


def test_token_type_mismatch_rejected():
    svc = _tokens()
    pair = svc.issue_pair(subject="u", role="viewer")
    with pytest.raises(InvalidTokenError):
        svc.decode(pair.access_token, expected_type="refresh")


def test_token_tampered_rejected():
    svc = _tokens()
    pair = svc.issue_pair(subject="u", role="viewer")
    with pytest.raises(InvalidTokenError):
        svc.decode(pair.access_token + "tamper", expected_type="access")


def test_expired_token_rejected():
    svc = TokenService(
        secret="k" * 32, access_ttl=timedelta(seconds=-1), refresh_ttl=timedelta(days=1)
    )
    pair = svc.issue_pair(subject="u", role="viewer")
    with pytest.raises(InvalidTokenError):
        svc.decode(pair.access_token)


# --- TOTP ------------------------------------------------------------------
def test_totp_verify_roundtrip():
    svc = TotpService()
    secret = svc.generate_secret()
    assert svc.verify(secret=secret, code=svc.now(secret))
    assert not svc.verify(secret=secret, code="000000")
    assert not svc.verify(secret=secret, code="")


def test_totp_provisioning_uri():
    svc = TotpService()
    secret = svc.generate_secret()
    uri = svc.provisioning_uri(secret=secret, account_name="a@b.com")
    assert uri.startswith("otpauth://totp/")


def test_totp_returns_the_matching_time_step():
    """The step is what makes a code single-use; callers store the last one."""
    svc = TotpService()
    secret = svc.generate_secret()
    now = 1_800_000_000.0

    step = svc.verify_counter(secret=secret, code=pyotp.TOTP(secret).at(now), now=now)
    assert step == int(now // 30)
    assert svc.verify_counter(secret=secret, code="000000", now=now) in (None, step)


def test_totp_accepts_neighbouring_steps_with_distinct_counters():
    svc = TotpService()
    secret = svc.generate_secret()
    now = 1_800_000_000.0
    totp = pyotp.TOTP(secret)

    previous = svc.verify_counter(secret=secret, code=totp.at(now - 30), now=now)
    current = svc.verify_counter(secret=secret, code=totp.at(now), now=now)
    following = svc.verify_counter(secret=secret, code=totp.at(now + 30), now=now)

    assert [previous, current, following] == [current - 1, current, current + 1]


def test_totp_rejects_a_step_outside_the_drift_window():
    svc = TotpService()
    secret = svc.generate_secret()
    now = 1_800_000_000.0
    assert svc.verify_counter(secret=secret, code=pyotp.TOTP(secret).at(now + 300), now=now) is None


def test_totp_rejects_non_numeric_input():
    svc = TotpService()
    secret = svc.generate_secret()
    for bad in ("", "   ", "abcdef", "12 34"):
        assert not svc.verify(secret=secret, code=bad)


# --- RBAC ------------------------------------------------------------------
def test_role_permissions_hierarchy():
    assert has_permission(Role.admin, Permission.admin_manage)
    assert not has_permission(Role.viewer, Permission.order_create)
    assert has_permission(Role.trader, Permission.order_create)
    assert Permission.market_read in permissions_for(Role.viewer)


def test_unknown_role_has_no_permissions():
    assert permissions_for("nonsense") == frozenset()
