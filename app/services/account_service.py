"""Exchange-account management: credential encryption and adapter construction."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.exchange.base import ExchangeAdapter
from app.exchange.credentials import ExchangeCredentials
from app.exchange.factory import ExchangeFactory
from app.exchange.paper_store import PaperLedger
from app.exchange.registry import get_info
from app.models.account import ExchangeAccount
from app.repositories.account_repository import AccountRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.paper_ledger_repository import PaperLedgerRepository
from app.security.crypto import SecretCipher

log = get_logger(__name__)


class AccountService:
    def __init__(
        self,
        *,
        accounts: AccountRepository,
        audit: AuditRepository,
        cipher: SecretCipher,
        factory: ExchangeFactory,
    ) -> None:
        self._accounts = accounts
        self._audit = audit
        self._cipher = cipher
        self._factory = factory

    async def create_paper_account(
        self, *, user_id: str, exchange_id: str, label: str, market_type: str
    ) -> ExchangeAccount:
        account = ExchangeAccount(
            user_id=user_id,
            exchange_id=exchange_id,
            label=label,
            market_type=market_type,
            is_paper=True,
        )
        await self._accounts.add(account)
        await self._audit.record(
            action="account.create_paper",
            user_id=user_id,
            detail={"exchange": exchange_id, "label": label},
        )
        return account

    async def create_live_account(
        self,
        *,
        user_id: str,
        exchange_id: str,
        label: str,
        market_type: str,
        api_key: str,
        api_secret: str,
        passphrase: str | None,
    ) -> ExchangeAccount:
        info = get_info(exchange_id)
        if info is None:
            raise ValidationError(f"Unsupported exchange: {exchange_id}")
        if info.requires_passphrase and not passphrase:
            raise ValidationError(f"{info.name} requires a passphrase.")

        account = ExchangeAccount(
            user_id=user_id,
            exchange_id=exchange_id,
            label=label,
            market_type=market_type,
            is_paper=False,
            api_key_enc=self._cipher.encrypt(api_key),
            api_secret_enc=self._cipher.encrypt(api_secret),
            passphrase_enc=self._cipher.encrypt(passphrase) if passphrase else None,
        )
        await self._accounts.add(account)
        # Only non-sensitive metadata is audited.
        await self._audit.record(
            action="account.create_live",
            user_id=user_id,
            detail={"exchange": exchange_id, "label": label},
        )
        log.info("live_account_created", user_id=user_id, exchange=exchange_id)
        return account

    async def list_accounts(self, user_id: str) -> list[ExchangeAccount]:
        return await self._accounts.list_for_user(user_id)

    async def get_account(self, *, account_id: str, user_id: str) -> ExchangeAccount:
        account = await self._accounts.get_for_user(account_id, user_id)
        if account is None:
            raise NotFoundError("Account not found.")
        return account

    async def delete_account(self, *, account_id: str, user_id: str) -> None:
        account = await self.get_account(account_id=account_id, user_id=user_id)
        await self._accounts.delete(account)
        await self._audit.record(
            action="account.delete", user_id=user_id, detail={"id": account_id}
        )

    def _decrypt_credentials(self, account: ExchangeAccount) -> ExchangeCredentials | None:
        if account.is_paper or not account.api_key_enc or not account.api_secret_enc:
            return None
        return ExchangeCredentials(
            api_key=self._cipher.decrypt(account.api_key_enc),
            api_secret=self._cipher.decrypt(account.api_secret_enc),
            passphrase=self._cipher.decrypt(account.passphrase_enc)
            if account.passphrase_enc
            else None,
        )

    def _is_paper_effective(self, account: ExchangeAccount) -> bool:
        """Whether this account trades on paper (own mode or server policy)."""
        return account.is_paper or not self._factory.live_trading_enabled

    def build_adapter(
        self, account: ExchangeAccount, *, ledger: PaperLedger | None = None
    ) -> ExchangeAdapter:
        """Construct a ready-to-use adapter for an account.

        The factory enforces the server's live-trading policy, so a live account
        is transparently downgraded to paper when live trading is disabled. For
        paper adapters, pass the durable ``ledger`` loaded from the database.
        """
        credentials = self._decrypt_credentials(account)
        return self._factory.build(
            account.exchange_id,
            credentials=credentials,
            paper=account.is_paper,
            market_type=account.market_type,
            ledger=ledger,
        )

    async def load_paper_ledger(
        self, account: ExchangeAccount, *, for_update: bool = False
    ) -> PaperLedger:
        repo = PaperLedgerRepository(self._accounts.session)
        return await repo.load(account.id, for_update=for_update)

    async def save_paper_ledger(self, account: ExchangeAccount, ledger: PaperLedger) -> None:
        repo = PaperLedgerRepository(self._accounts.session)
        await repo.save(account.id, ledger)

    @asynccontextmanager
    async def adapter_session(
        self, account: ExchangeAccount, *, persist: bool = False
    ) -> AsyncIterator[ExchangeAdapter]:
        """Yield an adapter, wiring the durable paper ledger and cleanup.

        For paper accounts the ledger is loaded from the database, injected into
        the adapter, and (when ``persist``) saved back afterwards. The adapter's
        network client is always closed on exit.
        """
        ledger: PaperLedger | None = None
        if self._is_paper_effective(account):
            ledger = await self.load_paper_ledger(account, for_update=persist)
        adapter = self.build_adapter(account, ledger=ledger)
        try:
            yield adapter
        finally:
            try:
                if persist and ledger is not None:
                    await self.save_paper_ledger(account, ledger)
            finally:
                await adapter.close()
