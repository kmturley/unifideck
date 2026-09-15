"""Tests for the display-only cross-store duplicate grouping.

Exercises ``annotate_duplicate_groups`` against the exact duplicate
examples surfaced by the user (Behind the Frame, Bus Simulator 21,
Dungeon of Naheulbeuk, Doors) plus the negative case that must never
group (sequels).
"""
from __future__ import annotations

from unifideck.core.game_grouping import annotate_duplicate_groups
from unifideck.core.types import Game
from unifideck.steam.owned_games import OwnedApp


def _g(store: str, title: str) -> Game:
    return Game(
        app_id=0,
        store=store,
        store_game_id=f"{store}-{title}".lower().replace(" ", "-"),
        title=title,
    )


def test_unique_titles_are_not_grouped():
    games = [_g("epic", "Game A"), _g("gog", "Game B")]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id is None
    assert out[1].dedupe_group_id is None


def test_behind_the_frame_colon_matches_across_stores():
    games = [
        _g("epic", "Behind the Frame: The Finest Scenery"),
        _g("amazon", "Behind the Frame: The Finest Scenery"),
    ]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id is not None
    assert out[0].dedupe_group_id == out[1].dedupe_group_id


def test_bus_simulator_colon_vs_no_colon():
    games = [
        _g("epic", "Bus Simulator 21 Next Stop"),
        _g("amazon", "Bus Simulator 21: Next Stop"),
    ]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id == out[1].dedupe_group_id


def test_dungeon_of_naheulbeuk_casing_and_word_order_of_articles():
    games = [
        _g("amazon", "The Dungeon Of Naheulbeuk: The Amulet Of Chaos"),
        _g("epic", "The Dungeon of Naheulbeuk: The Amulet of Chaos"),
    ]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id == out[1].dedupe_group_id


def test_doors_colon_vs_hyphen():
    games = [
        _g("amazon", "Doors: Paradox"),
        _g("epic", "Doors - Paradox"),
    ]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id == out[1].dedupe_group_id


def test_sequel_never_grouped_with_base_game():
    games = [_g("epic", "Beholder"), _g("amazon", "Beholder 2")]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id is None
    assert out[1].dedupe_group_id is None


def test_edition_variant_groups_and_carries_edition_label():
    games = [
        _g("epic", "Cyberpunk 2077"),
        _g("gog", "Cyberpunk 2077: Ultimate Edition"),
    ]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id == out[1].dedupe_group_id
    assert out[0].edition_label is None
    assert out[1].edition_label == "Ultimate Edition"


def test_publisher_prefix_variant_still_groups():
    games = [
        _g("epic", "Splinter Cell Chaos Theory"),
        _g("ubisoft", "Tom Clancy's Splinter Cell Chaos Theory"),
    ]
    out = annotate_duplicate_groups(games)
    assert out[0].dedupe_group_id is not None
    assert out[0].dedupe_group_id == out[1].dedupe_group_id


def test_grouping_never_changes_count_or_order():
    games = [
        _g("epic", "Behind the Frame: The Finest Scenery"),
        _g("gog", "Unrelated Game"),
        _g("amazon", "Behind the Frame: The Finest Scenery"),
    ]
    out = annotate_duplicate_groups(games)
    assert len(out) == 3
    assert [g.store for g in out] == ["epic", "gog", "amazon"]


def test_steam_owned_match_sets_app_id():
    games = [_g("epic", "Disco Elysium")]
    out = annotate_duplicate_groups(
        games,
        steam_owned={"disco elysium": OwnedApp(appid=632470, title="Disco Elysium")},
    )
    assert out[0].steam_owned_app_id == 632470


def test_steam_owned_no_match_leaves_field_none():
    games = [_g("epic", "Disco Elysium")]
    out = annotate_duplicate_groups(
        games,
        steam_owned={"some other game": OwnedApp(appid=12345, title="Some Other Game")},
    )
    assert out[0].steam_owned_app_id is None


def test_steam_owned_is_independent_of_dedupe_group():
    """A singleton title (no cross-store dupe) can still carry
    steam_owned_app_id — the two fields answer different questions."""
    games = [_g("epic", "Disco Elysium")]
    out = annotate_duplicate_groups(
        games,
        steam_owned={"disco elysium": OwnedApp(appid=632470, title="Disco Elysium")},
    )
    assert out[0].dedupe_group_id is None
    assert out[0].steam_owned_app_id == 632470


def test_steam_owned_respects_sequel_boundary():
    """Beholder 2 must not match a Steam-owned "Beholder"."""
    games = [_g("amazon", "Beholder 2")]
    out = annotate_duplicate_groups(
        games,
        steam_owned={"beholder": OwnedApp(appid=705810, title="Beholder")},
    )
    assert out[0].steam_owned_app_id is None


def test_steam_owned_none_is_a_noop():
    games = [_g("epic", "Disco Elysium")]
    out = annotate_duplicate_groups(games, steam_owned=None)
    assert out[0].steam_owned_app_id is None


def test_steam_owned_edition_label_extracted_from_steam_title():
    """Disco Elysium's real regression: the Epic copy and the Steam
    listing are BOTH "The Final Cut" — the switcher must show that, not
    a blank/default label, for the synthetic Steam entry."""
    games = [_g("epic", "Disco Elysium")]
    out = annotate_duplicate_groups(
        games,
        steam_owned={
            "disco elysium": OwnedApp(
                appid=632470, title="Disco Elysium - The Final Cut",
            ),
        },
    )
    assert out[0].steam_owned_edition_label == "The Final Cut"


def test_steam_owned_edition_label_none_when_steam_title_has_no_suffix():
    games = [_g("epic", "Disco Elysium")]
    out = annotate_duplicate_groups(
        games,
        steam_owned={"disco elysium": OwnedApp(appid=632470, title="Disco Elysium")},
    )
    assert out[0].steam_owned_edition_label is None


def test_steam_owned_edition_label_independent_of_this_games_own_edition():
    """The Steam copy's edition label must come from the Steam title,
    not get contaminated by this game's own (possibly different) one."""
    games = [_g("epic", "Disco Elysium - Definitive Edition")]
    out = annotate_duplicate_groups(
        games,
        steam_owned={
            "disco elysium definitive edition": OwnedApp(
                appid=632470, title="Disco Elysium - The Final Cut",
            ),
        },
    )
    assert out[0].edition_label == "Definitive Edition"
    assert out[0].steam_owned_edition_label == "The Final Cut"
