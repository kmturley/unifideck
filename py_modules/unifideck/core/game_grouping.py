"""Cross-store duplicate-card grouping — display layer only.

py_modules/unifideck/core/game_grouping.py

Unlike :mod:`cross_source_dedupe` (which *collapses* duplicates into a
single shortcut, disabled by default), this module never removes or
reorders a game. It only stamps each :class:`~unifideck.core.types.Game`
with a ``dedupe_group_id`` shared by every other copy of the same title
across stores, plus an ``edition_label`` when the title carries a
recognised edition/variant suffix. Every store's copy keeps its own real
Steam shortcut and ``app_id`` — the frontend uses the group id purely to
render one card with a multi-store badge cluster (``src/lib/game-
grouping.ts``) and a store-switcher on the detail page
(``src/lib/library-filters``'s ``getGroupSiblings``).

Wiring: ``SyncService._aggregate_results`` calls
:func:`annotate_duplicate_groups` unconditionally after the (usually
no-op) collapse step, gated by ``dedup.ui_grouping_enabled`` (default
true).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from unifideck.utils.title_match import (
    PUBLISHER_PREFIXES,
    extract_edition_label,
    normalize_for_match,
    strip_edition_suffix,
    titles_match,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from unifideck.steam.owned_games import OwnedApp

    from .types import Game


def _bucket_keys(title: str) -> set[str]:
    """First normalised word, plus the publisher-prefix-stripped first
    word when a known prefix is present.

    A single first-word bucket would miss the exact case
    ``titles_match`` is designed to accept — "Splinter Cell" vs "Tom
    Clancy's Splinter Cell" share no first word. Adding the
    prefix-stripped variant as a second bucket key costs nothing (the
    prefix table is 10 entries) and keeps the O(N + bucket^2) shape,
    since only titles that actually carry one of these prefixes gain an
    extra bucket membership.
    """
    normalized = normalize_for_match(title)
    if not normalized:
        return {""}
    keys = {normalized.split()[0]}
    for prefix in PUBLISHER_PREFIXES:
        if normalized.startswith(prefix + " "):
            stripped = normalized[len(prefix) :].strip()
            if stripped:
                keys.add(stripped.split()[0])
            break
    return keys


class _UnionFind:
    """Union-find over game indices — split out of
    :func:`annotate_duplicate_groups` purely to keep that function's
    cyclomatic/cognitive complexity within the repo's lint threshold.
    """

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def _bucket_by_title(games: Sequence[Game]) -> dict[str, list[int]]:
    buckets: dict[str, list[int]] = {}
    for index, game in enumerate(games):
        for key in _bucket_keys(game.title):
            buckets.setdefault(key, []).append(index)
    return buckets


def _union_matching_titles(
    games: Sequence[Game],
    buckets: dict[str, list[int]],
) -> _UnionFind:
    uf = _UnionFind(len(games))
    for indices in buckets.values():
        for pos, i in enumerate(indices):
            for j in indices[pos + 1 :]:
                if titles_match(games[i].title, games[j].title):
                    uf.union(i, j)
    return uf


def _group_members(games: Sequence[Game], uf: _UnionFind) -> dict[int, list[int]]:
    members: dict[int, list[int]] = {}
    for i in range(len(games)):
        members.setdefault(uf.find(i), []).append(i)
    return members


def _assign_group_ids(games: list[Game], members: dict[int, list[int]]) -> None:
    for root, indices in members.items():
        group_id = None
        if len(indices) > 1:
            base_title = games[root].title
            group_id = strip_edition_suffix(normalize_for_match(base_title))
        for i in indices:
            games[i].dedupe_group_id = group_id
            games[i].edition_label = extract_edition_label(games[i].title)


def annotate_duplicate_groups(
    games: Sequence[Game],
    *,
    steam_owned: Mapping[str, OwnedApp] | None = None,
) -> list[Game]:
    """Stamp ``dedupe_group_id`` / ``edition_label`` on every game.

    Pure and order-preserving: same length, same order, same objects
    (mutated in place) as the input. A group of size 1 gets
    ``dedupe_group_id = None`` — there is nothing to visually merge, and
    ``None`` lets the frontend treat "ungrouped" as its own singleton
    group without a special case.

    Matching uses :func:`titles_match`, which already guards sequels
    ("Beholder" vs "Beholder 2") via its version-token gate and accepts
    edition/publisher-prefix variants — the exact behaviour this feature
    needs, reused rather than re-implemented.

    Bucketed by the first normalised word before the pairwise
    ``titles_match`` comparison so a library of N games costs roughly
    O(N + sum(bucket_size^2)) rather than O(N^2) — buckets are typically
    small (few titles share a first word), so this stays fast even for
    libraries in the low thousands.

    ``steam_owned`` (``{normalized title: OwnedApp}``, from
    ``steam.owned_games.get_all_owned_app_ids``) is optional — when
    given, every game whose title matches an entry also gets
    ``steam_owned_app_id`` (and ``steam_owned_edition_label``, extracted
    from the Steam copy's own original title) set, independent of
    ``dedupe_group_id``. This is how a duplicate group (or even a
    singleton title) learns the user already owns the same game on real
    Steam, which Unifideck's own sync never sees since it only ever
    aggregates Epic/GOG/Amazon/Ubisoft/Battle.net/Microsoft.
    """
    games = list(games)
    buckets = _bucket_by_title(games)
    uf = _union_matching_titles(games, buckets)
    members = _group_members(games, uf)
    _assign_group_ids(games, members)

    if steam_owned:
        _annotate_steam_owned(games, steam_owned)

    return games


def _bucket_steam_owned(
    steam_owned: Mapping[str, OwnedApp],
) -> dict[str, list[tuple[str, OwnedApp]]]:
    owned_buckets: dict[str, list[tuple[str, OwnedApp]]] = {}
    for normalized_title, owned_app in steam_owned.items():
        if not normalized_title:
            continue
        owned_buckets.setdefault(normalized_title.split()[0], []).append(
            (normalized_title, owned_app),
        )
    return owned_buckets


def _find_steam_owned_match(
    game: Game,
    owned_buckets: dict[str, list[tuple[str, OwnedApp]]],
) -> OwnedApp | None:
    for key in _bucket_keys(game.title):
        for normalized_title, owned_app in owned_buckets.get(key, []):
            if titles_match(game.title, normalized_title):
                return owned_app
    return None


def _annotate_steam_owned(
    games: list[Game], steam_owned: Mapping[str, OwnedApp],
) -> None:
    """Set ``steam_owned_app_id``/``steam_owned_edition_label`` on every
    game matching a Steam title.

    Bucketed the same way as the cross-store pass: ``steam_owned``'s
    keys are already normalised (``steam.owned_games`` uses the simple
    ``normalize_title_for_matching``), so they're grouped by first word
    once up front rather than re-normalised per comparison.

    The edition label comes from the Steam copy's own original title
    (``OwnedApp.title``), via the same :func:`extract_edition_label`
    used for every other store — NOT copied from whichever Unifideck
    game matched it, since title-matching tolerates edition differences
    on purpose (that's how a base game groups with its "Ultimate
    Edition" copy) and assuming they're the same edition would just
    trade one wrong label for another.
    """
    owned_buckets = _bucket_steam_owned(steam_owned)
    for game in games:
        owned_app = _find_steam_owned_match(game, owned_buckets)
        if owned_app is None:
            continue
        game.steam_owned_app_id = owned_app.appid
        game.steam_owned_edition_label = extract_edition_label(owned_app.title)
