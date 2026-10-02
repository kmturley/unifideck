"""Which other stores already hold a given Steam game.

The Steam Store ownership ribbon asks one question per store-page visit:
"the user is looking at Steam AppID N; on which non-Steam stores do they
already have it?" This module answers it by joining the live unified library
against the ``steam_real_appid`` mapping cache.

**Why on demand, not an index.** The mapping cache is written at three
different moments, and only one of them emits anything: the metadata phase
runs *after* ``sync_complete``, the metadata backfill announces itself, and
``compatibility/library.py::_persist_steam_real_appid`` writes silently. An
index invalidated by events would miss the last one forever. One pass over
~1,300 games is a few thousand dict lookups, which is cheap at human
navigation speed and always as fresh as the cache.

**Why the loop runs over the library, not the cache.** ``steam_real_appid``
has no TTL and is never pruned, so it still maps shortcuts for games that
left the library. Starting from ``get_all_games()`` means a stale mapping can
never match.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from unifideck.core.steam_appid_map import read_positive_steam_appid
from unifideck.core.store_capabilities import SUBSCRIPTION_LIBRARY_STORES
from unifideck.core.types.domain import Game
from unifideck.core.types.events import GameTag

#: Rows that do not mean "you have the game". DLC is the dangerous one: the
#: mapping is a fuzzy title search, so a DLC titled like its base game can
#: resolve to the base game's AppID and claim ownership of it.
NOT_OWNERSHIP_TAGS = frozenset({GameTag.DLC.value, GameTag.DEMO.value, GameTag.BETA.value})

#: Distinct owned titles kept per store. Two is enough to show an edition
#: mismatch; more would only crowd a chip.
_MAX_TITLES = 2


@dataclass(frozen=True)
class OwnedCopy:
    """One store's holding of the game.

    Attributes:
        store: store id (``"gog"``, ``"epic"``, …).
        titles: the distinct titles this store lists the game under, in
            library order, at most :data:`_MAX_TITLES`.
        installed: True if any copy from this store is installed.
        subscription: True for stores in
            :data:`~unifideck.core.store_capabilities.SUBSCRIPTION_LIBRARY_STORES`,
            whose rows are "playable", not "owned".
    """

    store: str
    titles: tuple[str, ...]
    installed: bool
    subscription: bool


def find_owned_copies(
    games: Iterable[Game], cache: Any, steam_app_id: int,
) -> list[OwnedCopy]:
    """Every non-Steam store holding *steam_app_id*, purchases first.

    Args:
        games: the unified library (``sync_service.get_all_games()``).
        cache: the ``CacheManager``; a cold or raising cache yields no
            mappings, so the answer is ``[]`` rather than an error.
        steam_app_id: the real Steam AppID the store page shows.

    Returns:
        One :class:`OwnedCopy` per store, sorted purchased before
        subscription, then installed first, then by store id.
    """
    if steam_app_id <= 0:
        return []
    grouped: dict[str, list[Game]] = {}
    for game in games:
        if not _counts_as_ownership(game):
            continue
        if read_positive_steam_appid(cache, game.app_id) != steam_app_id:
            continue
        grouped.setdefault(game.store, []).append(game)
    copies = [_merge(store, rows) for store, rows in grouped.items()]
    copies.sort(key=lambda c: (c.subscription, not c.installed, c.store))
    return copies


def _counts_as_ownership(game: Game) -> bool:
    """Whether *game* is a non-Steam row that means "you have it"."""
    if not game.store or game.store == "steam":
        return False
    return not any(str(tag) in NOT_OWNERSHIP_TAGS for tag in game.tags or ())


def _merge(store: str, rows: list[Game]) -> OwnedCopy:
    """Collapse one store's rows (editions, duplicates) into one copy."""
    titles: list[str] = []
    for row in rows:
        title = (row.title or "").strip()
        if title and title not in titles and len(titles) < _MAX_TITLES:
            titles.append(title)
    return OwnedCopy(
        store=store,
        titles=tuple(titles),
        installed=any(row.installed for row in rows),
        subscription=store in SUBSCRIPTION_LIBRARY_STORES,
    )
