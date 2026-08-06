"""Role-based access control.

A small, explicit permission model. Each :class:`Role` maps to a set of
:class:`Permission` values; the API layer checks permissions rather than roles
directly, so the mapping can evolve without touching endpoints.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    admin = "admin"
    trader = "trader"
    viewer = "viewer"


class Permission(StrEnum):
    # Account / exchange credential management
    account_read = "account:read"
    account_write = "account:write"
    # Market data
    market_read = "market:read"
    # Portfolio
    portfolio_read = "portfolio:read"
    # Trading
    order_read = "order:read"
    order_create = "order:create"
    order_cancel = "order:cancel"
    # Administration
    admin_manage = "admin:manage"


_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.market_read,
        Permission.portfolio_read,
        Permission.order_read,
        Permission.account_read,
    }
)
_TRADER: frozenset[Permission] = _VIEWER | frozenset(
    {Permission.account_write, Permission.order_create, Permission.order_cancel}
)
_ADMIN: frozenset[Permission] = _TRADER | frozenset({Permission.admin_manage})

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.viewer: _VIEWER,
    Role.trader: _TRADER,
    Role.admin: _ADMIN,
}


def permissions_for(role: Role | str) -> frozenset[Permission]:
    try:
        role_enum = Role(role)
    except ValueError:
        return frozenset()
    return ROLE_PERMISSIONS.get(role_enum, frozenset())


def has_permission(role: Role | str, permission: Permission) -> bool:
    return permission in permissions_for(role)
