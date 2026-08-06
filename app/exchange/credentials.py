"""Value object carrying decrypted exchange credentials.

Instances are short-lived and live only in memory while a request is served.
The ``__repr__`` is overridden so credentials can never be accidentally logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExchangeCredentials:
    api_key: str
    api_secret: str
    passphrase: str | None = None  # required by OKX, KuCoin, Bitget, ...
    subaccount: str | None = None
    testnet: bool = True

    # Marked so it is obvious in code review that this holds secrets.
    _sensitive: bool = field(default=True, repr=False, init=False)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"ExchangeCredentials(api_key='***', api_secret='***', "
            f"passphrase={'***' if self.passphrase else None}, "
            f"subaccount={self.subaccount!r}, testnet={self.testnet})"
        )

    def is_complete(self) -> bool:
        return bool(self.api_key and self.api_secret)
