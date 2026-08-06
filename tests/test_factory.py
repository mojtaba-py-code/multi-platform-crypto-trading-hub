"""Tests for the exchange factory's safe-by-default trading policy."""

from __future__ import annotations

import pytest
from app.config.settings import Settings
from app.core.exceptions import UnsupportedExchangeError
from app.exchange.credentials import ExchangeCredentials
from app.exchange.factory import ExchangeFactory


def _factory(*, live: bool) -> ExchangeFactory:
    return ExchangeFactory(Settings(allow_live_trading=live, use_exchange_testnet=True))


def test_live_disabled_forces_paper_even_for_live_account():
    factory = _factory(live=False)
    creds = ExchangeCredentials(api_key="k" * 10, api_secret="s" * 10)
    adapter = factory.build("binance", credentials=creds, paper=False)
    assert adapter.is_paper is True  # policy downgraded it to paper


def test_paper_account_always_paper():
    factory = _factory(live=True)
    adapter = factory.build("binance", paper=True)
    assert adapter.is_paper is True


def test_live_account_with_live_enabled_is_live():
    factory = _factory(live=True)
    creds = ExchangeCredentials(api_key="k" * 10, api_secret="s" * 10)
    adapter = factory.build("okx", credentials=creds, paper=False)
    assert adapter.is_paper is False
    assert adapter.exchange_id == "okx"


def test_live_account_requires_credentials():
    factory = _factory(live=True)
    with pytest.raises(UnsupportedExchangeError):
        factory.build("binance", credentials=None, paper=False)


def test_unsupported_exchange_rejected():
    factory = _factory(live=False)
    with pytest.raises(UnsupportedExchangeError):
        factory.build("not-a-real-exchange", paper=True)


def test_public_market_adapter():
    factory = _factory(live=False)
    adapter = factory.public_market_adapter("kraken")
    assert adapter.exchange_id == "kraken"
    assert adapter.is_paper is False
