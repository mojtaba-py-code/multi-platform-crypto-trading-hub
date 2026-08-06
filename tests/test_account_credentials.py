"""Exchange API credentials: encrypted at rest, and never able to trade live
unless the operator has switched live trading on.

These are the two properties that make it defensible to store someone else's
exchange keys at all, so they are asserted against the real database rows rather
than through mocks.
"""

from __future__ import annotations

import pytest
from app.core.exceptions import NotFoundError, ValidationError
from app.exchange.factory import ExchangeFactory
from app.exchange.paper import PaperAdapter
from app.models.user import User
from app.repositories.account_repository import AccountRepository
from app.repositories.audit_repository import AuditRepository
from app.security.crypto import get_password_hasher, get_secret_cipher
from app.services.account_service import AccountService
from tests.fakes import FakeFactory

_API_KEY = "AK-live-3f9c2b71"
_API_SECRET = "SK-live-8d4e1a06"
_PASSPHRASE = "pp-live-5c7b"


def _service(session, *, factory=None) -> AccountService:
    return AccountService(
        accounts=AccountRepository(session),
        audit=AuditRepository(session),
        cipher=get_secret_cipher(),
        factory=factory or FakeFactory(live=False),
    )


async def _user(session) -> User:
    user = User(
        email="keys@example.com",
        password_hash=get_password_hasher().hash("a-password-long-enough"),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_live_credentials_are_encrypted_in_the_database(session):
    user = await _user(session)
    service = _service(session)

    account = await service.create_live_account(
        user_id=user.id,
        exchange_id="okx",
        label="live-1",
        market_type="spot",
        api_key=_API_KEY,
        api_secret=_API_SECRET,
        passphrase=_PASSPHRASE,
    )
    await session.commit()

    stored = " ".join(
        filter(None, [account.api_key_enc, account.api_secret_enc, account.passphrase_enc])
    )
    for plaintext in (_API_KEY, _API_SECRET, _PASSPHRASE):
        assert plaintext not in stored

    # ...and they still round-trip for the caller that needs them.
    creds = service._decrypt_credentials(account)
    assert (creds.api_key, creds.api_secret, creds.passphrase) == (
        _API_KEY,
        _API_SECRET,
        _PASSPHRASE,
    )


@pytest.mark.asyncio
async def test_audit_trail_records_the_connection_without_the_secrets(session):
    from app.models.audit import AuditLog
    from sqlalchemy import select

    user = await _user(session)
    await _service(session).create_live_account(
        user_id=user.id,
        exchange_id="binance",
        label="live-1",
        market_type="spot",
        api_key=_API_KEY,
        api_secret=_API_SECRET,
        passphrase=None,
    )
    await session.commit()

    entries = (await session.execute(select(AuditLog))).scalars().all()
    actions = {e.action for e in entries}
    assert "account.create_live" in actions
    serialised = str([e.detail for e in entries])
    assert _API_KEY not in serialised and _API_SECRET not in serialised


@pytest.mark.asyncio
async def test_unsupported_exchange_is_refused(session):
    user = await _user(session)
    with pytest.raises(ValidationError):
        await _service(session).create_live_account(
            user_id=user.id,
            exchange_id="not-an-exchange",
            label="x",
            market_type="spot",
            api_key=_API_KEY,
            api_secret=_API_SECRET,
            passphrase=None,
        )


@pytest.mark.asyncio
async def test_exchange_requiring_a_passphrase_rejects_a_missing_one(session):
    user = await _user(session)
    with pytest.raises(ValidationError, match="passphrase"):
        await _service(session).create_live_account(
            user_id=user.id,
            exchange_id="okx",  # OKX mandates a passphrase
            label="x",
            market_type="spot",
            api_key=_API_KEY,
            api_secret=_API_SECRET,
            passphrase=None,
        )


@pytest.mark.asyncio
async def test_a_live_account_still_trades_on_paper_while_live_trading_is_off(session):
    """The safety switch is server-side and cannot be overridden per account."""
    user = await _user(session)
    service = _service(session, factory=ExchangeFactory(_settings_with(allow_live_trading=False)))

    account = await service.create_live_account(
        user_id=user.id,
        exchange_id="binance",
        label="live-1",
        market_type="spot",
        api_key=_API_KEY,
        api_secret=_API_SECRET,
        passphrase=None,
    )
    assert account.is_paper is False  # the account itself is a live one...
    assert isinstance(service.build_adapter(account), PaperAdapter)  # ...but it cannot trade live


@pytest.mark.asyncio
async def test_accounts_are_scoped_to_their_owner(session):
    owner = await _user(session)
    other = User(
        email="intruder@example.com",
        password_hash=get_password_hasher().hash("a-password-long-enough"),
    )
    session.add(other)
    await session.flush()

    service = _service(session)
    account = await service.create_paper_account(
        user_id=owner.id, exchange_id="binance", label="mine", market_type="spot"
    )
    await session.commit()

    with pytest.raises(NotFoundError):
        await service.get_account(account_id=account.id, user_id=other.id)


def _settings_with(**overrides):
    from app.config.settings import Settings

    return Settings(**overrides)
