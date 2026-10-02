"""Which non-Steam stores hold a Steam game: the ownership ribbon's join.

The ribbon tells a user "you already own this" on a page where they are about
to pay. A false positive costs a sale they wanted; a false negative costs the
whole feature. These tests pin the decisions that keep the join honest: both
AppID forms resolve, the ``-1`` sentinel and stale mappings never match, rows
that are not ownership (DLC, demos, betas, Steam itself) are skipped, and
subscription libraries are kept apart from purchases.
"""
from __future__ import annotations

from typing import Any

from unifideck.core.cross_store_ownership import OwnedCopy, find_owned_copies
from unifideck.core.steam_appid_map import STEAM_REAL_APPID_NS
from unifideck.core.types.domain import Game

BG2 = 257350
SIGNED = -1867837430
UNSIGNED = SIGNED & 0xFFFFFFFF


class _Cache:
    """The one read the join performs."""

    def __init__(self, entries: dict[str, Any]) -> None:
        self.entries = entries

    def get(self, namespace: str, key: str) -> Any:
        assert namespace == STEAM_REAL_APPID_NS
        return self.entries.get(key)


class _RaisingCache:
    def get(self, namespace: str, key: str) -> Any:
        raise RuntimeError("cache unavailable")


def _game(app_id: int, store: str, title: str = "Baldur's Gate II", **kw: Any) -> Game:
    return Game(app_id=app_id, store=store, store_game_id=f"{store}-{app_id}", title=title, **kw)


def test_finds_a_copy_mapped_under_the_signed_form() -> None:
    games = [_game(SIGNED, "gog")]
    copies = find_owned_copies(games, _Cache({str(SIGNED): BG2}), BG2)

    assert copies == [OwnedCopy("gog", ("Baldur's Gate II",), False, False)]


def test_finds_a_copy_whose_app_id_is_unsigned() -> None:
    """``Game.app_id`` is signed today, but the read must not depend on it."""
    games = [_game(UNSIGNED, "gog")]

    assert [c.store for c in find_owned_copies(games, _Cache({str(SIGNED): BG2}), BG2)] == ["gog"]


def test_the_no_counterpart_sentinel_and_junk_never_match() -> None:
    games = [_game(1, "gog"), _game(2, "epic"), _game(3, "amazon")]
    cache = _Cache({"1": -1, "2": 0, "3": "257350"})

    assert find_owned_copies(games, cache, BG2) == []


def test_a_mapping_for_a_game_no_longer_in_the_library_is_ignored() -> None:
    """``steam_real_appid`` is never pruned; the library is the source."""
    cache = _Cache({"99": BG2, str(SIGNED): 1091500})
    games = [_game(SIGNED, "gog")]

    assert find_owned_copies(games, cache, BG2) == []


def test_two_copies_on_one_store_merge_into_one() -> None:
    games = [
        _game(1, "gog", "Baldur's Gate II"),
        _game(2, "gog", "Baldur's Gate II: Enhanced Edition", installed=True),
        _game(3, "gog", "Baldur's Gate II"),
    ]
    cache = _Cache({"1": BG2, "2": BG2, "3": BG2})

    assert find_owned_copies(games, cache, BG2) == [
        OwnedCopy(
            "gog",
            ("Baldur's Gate II", "Baldur's Gate II: Enhanced Edition"),
            installed=True,
            subscription=False,
        ),
    ]


def test_subscription_rows_are_marked_and_sort_after_purchases() -> None:
    games = [_game(1, "microsoft"), _game(2, "gog"), _game(3, "amazon", installed=True)]
    cache = _Cache({"1": BG2, "2": BG2, "3": BG2})

    copies = find_owned_copies(games, cache, BG2)

    assert [(c.store, c.subscription) for c in copies] == [
        ("amazon", False),  # installed first
        ("gog", False),
        ("microsoft", True),
    ]


def test_non_ownership_rows_are_skipped() -> None:
    """A DLC fuzzy-matched to its base game must not claim the base game."""
    games = [
        _game(1, "gog", tags=["dlc"]),
        _game(2, "epic", tags=["demo"]),
        _game(3, "amazon", tags=["beta"]),
        _game(4, "steam"),
        _game(5, "itch", tags=["native"]),
    ]
    cache = _Cache({str(i): BG2 for i in range(1, 6)})

    assert [c.store for c in find_owned_copies(games, cache, BG2)] == ["itch"]


def test_a_raising_or_missing_cache_yields_nothing() -> None:
    games = [_game(1, "gog")]

    assert find_owned_copies(games, _RaisingCache(), BG2) == []
    assert find_owned_copies(games, None, BG2) == []


def test_a_non_positive_app_id_short_circuits() -> None:
    games = [_game(1, "gog")]

    assert find_owned_copies(games, _Cache({"1": BG2}), 0) == []
    assert find_owned_copies(games, _Cache({"1": BG2}), -5) == []

