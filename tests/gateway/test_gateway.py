"""Tests for gateway middleware registration — H3."""
from aiogram import Dispatcher

from archon.chat.bot import create_dispatcher
from archon.chat.middleware import WhitelistMiddleware
from archon.gateway.gateway import register_middleware


def _whitelist_middlewares(dp: Dispatcher) -> list[object]:
    return list(dp.message.middleware._middlewares)


# ──────────────────────────────────────────────────────────────────
# create_dispatcher intentionally has no WhitelistMiddleware
# ──────────────────────────────────────────────────────────────────


def test_create_dispatcher_has_no_whitelist_middleware() -> None:
    """create_dispatcher() must NOT register WhitelistMiddleware — gateway's job."""
    dp = create_dispatcher()
    for mw in _whitelist_middlewares(dp):
        assert not isinstance(mw, WhitelistMiddleware)


# ──────────────────────────────────────────────────────────────────
# register_middleware wires WhitelistMiddleware onto the dispatcher
# ──────────────────────────────────────────────────────────────────


def test_register_middleware_adds_whitelist_middleware() -> None:
    dp = create_dispatcher()
    register_middleware(dp, allowed_user_ids=[42, 99])
    types = [type(mw) for mw in _whitelist_middlewares(dp)]
    assert WhitelistMiddleware in types


def test_register_middleware_passes_allowed_ids() -> None:
    dp = create_dispatcher()
    register_middleware(dp, allowed_user_ids=[7])
    mws = [mw for mw in _whitelist_middlewares(dp) if isinstance(mw, WhitelistMiddleware)]
    assert len(mws) == 1
    assert mws[0]._allowed == frozenset([7])
