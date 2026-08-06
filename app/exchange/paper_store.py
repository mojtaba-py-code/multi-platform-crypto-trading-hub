"""In-memory working object for paper trading.

The :class:`PaperLedger` is the mutable balances/orders view an adapter operates
against during a single request. Durability is provided by the database (see
:class:`~app.repositories.paper_ledger_repository.PaperLedgerRepository`), which
loads a ledger before an operation and saves it afterwards; the ledger itself is
just the in-memory representation for the duration of that operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.exchange.models import Balance, Order


@dataclass
class PaperLedger:
    """Mutable simulated balances and orders for a single paper account."""

    quote_asset: str = "USDT"
    starting_balance: Decimal = Decimal(100_000)
    balances: dict[str, Balance] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quote_asset not in self.balances:
            self.balances[self.quote_asset] = Balance(
                self.quote_asset, Decimal(str(self.starting_balance)), Decimal(0)
            )
