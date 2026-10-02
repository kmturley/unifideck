"""``show_store_ownership``: check order and the no-CDP fast path.

The frontend calls this on **every** store app page. Nearly all of them are
games the user does not own anywhere else, so the endpoint's cost on that
path is the feature's cost: one in-memory join, and never a CDP connection.
"""
from __future__ import annotations

from typing import Any

import pytest

from unifideck.cdp.store_ribbon import RibbonInjectOutcome
from unifideck.core.types.domain import Game
from unifideck.rpc.mixins import store_ownership
from unifideck.rpc.mixins.store_ownership import StoreOwnershipRPCMixin

BG2 = 257350
STRINGS = {
    "tag_owned": "Owned",
    "tag_cloud": "Cloud",
    "message_owned": "You already own this game on:",
    "message_cloud": "Playable via Xbox Cloud Gaming",
    "installed": "Installed",
    "via": "via Unifideck",
    "dir": "ltr",
    "store_labels": {"gog": "GOG"},
}


class _Cache:
    def __init__(self, entries: dict[str, Any]) -> None:
        self.entries = entries

    def get(self, namespace: str, key: str) -> Any:
        return self.entries.get(key)


class _Sync:
    def __init__(self, games: list[Game]) -> None:
        self.games = games

    def get_all_games(self) -> list[Game]:
        return list(self.games)


class _Config:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _plugin(games: list[Game], mappings: dict[str, int], port: int = 8080) -> StoreOwnershipRPCMixin:
    plugin = StoreOwnershipRPCMixin()
    plugin.cache = _Cache(mappings)
    plugin.sync_service = _Sync(games)
    plugin.config = _Config({"cdp.port": port})
    return plugin


@pytest.fixture
def no_cdp(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _forbidden(*_: Any, **__: Any) -> RibbonInjectOutcome:
        raise AssertionError("CDP must not be touched on this path")

    monkeypatch.setattr(store_ownership, "inject_store_ribbon", _forbidden)


async def test_not_owned_returns_before_any_cdp_work(no_cdp: None) -> None:
    plugin = _plugin([Game(app_id=1, store="gog", store_game_id="1", title="Other")], {"1": 10})

    assert await plugin.show_store_ownership(BG2, STRINGS) == {
        "shown": False, "reason": "not_owned", "stores": [],
    }


async def test_bad_appid_and_missing_sync_service(no_cdp: None) -> None:
    plugin = _plugin([], {})

    assert (await plugin.show_store_ownership("nope", STRINGS))["reason"] == "bad_appid"

    plugin.sync_service = None
    assert (await plugin.show_store_ownership(BG2, STRINGS))["reason"] == "not_owned"


async def test_a_raising_sync_service_degrades_to_not_owned(no_cdp: None) -> None:
    class _Broken:
        def get_all_games(self) -> list[Game]:
            raise RuntimeError("sync in flight")

    plugin = _plugin([], {})
    plugin.sync_service = _Broken()

    assert (await plugin.show_store_ownership(BG2, STRINGS))["reason"] == "not_owned"


async def test_owned_but_bad_strings_draws_nothing(no_cdp: None) -> None:
    plugin = _plugin([Game(app_id=1, store="gog", store_game_id="1", title="BG2")], {"1": BG2})

    assert await plugin.show_store_ownership(BG2, {"tag_owned": "Owned"}) == {
        "shown": False, "reason": "bad_strings", "stores": ["gog"],
    }


async def test_owned_draws_on_the_configured_port(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _inject(port: int, appid: int, payload: dict[str, Any]) -> RibbonInjectOutcome:
        seen.update(port=port, appid=appid, payload=payload)
        return RibbonInjectOutcome(shown=True, reason="shown", targets=1)

    monkeypatch.setattr(store_ownership, "inject_store_ribbon", _inject)
    plugin = _plugin(
        [Game(app_id=1, store="gog", store_game_id="1", title="BG2")], {"1": BG2}, port=9333,
    )

    result = await plugin.show_store_ownership(str(BG2), STRINGS)

    assert result == {"shown": True, "reason": "shown", "stores": ["gog"]}
    assert seen["port"] == 9333
    assert seen["appid"] == BG2
    assert seen["payload"]["sections"][0]["chips"][0]["label"] == "GOG"


async def test_an_unexpected_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_: Any, **__: Any) -> RibbonInjectOutcome:
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(store_ownership, "inject_store_ribbon", _boom)
    plugin = _plugin([Game(app_id=1, store="gog", store_game_id="1", title="BG2")], {"1": BG2})

    assert await plugin.show_store_ownership(BG2, STRINGS) == {
        "shown": False, "reason": "error", "stores": [],
    }
