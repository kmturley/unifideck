"""Platform words in a store title, for Steam store search.

Xbox product titles carry platform markers that Steam's names never have:
"DOOM Eternal Standard Edition (PC)", "Mafia: Definitive Edition for XBOX
One". Steam's storesearch returns nothing for those strings, so
``steam.library`` strips them before searching.

Kept apart from :mod:`unifideck.utils.title_match` for now: PR #461 splits
that module into a package, and this lands first so the PR still merges
cleanly. Once it has merged, these tables fold back into the title_match
suffix table and this module goes away.
"""
from __future__ import annotations

from unifideck.utils.title_match import strip_edition_suffix

# Longest first, and "for <platform>" before the bare platform, so "X for
# Xbox One" loses all three words instead of leaving "X for". The bare
# "pc" is what "(PC)" normalises to ("DOOM Eternal Standard Edition (PC)").
PLATFORM_SUFFIXES: tuple[str, ...] = (
    "for xbox series xs", "for xbox one", "for windows 10",
    "for pc", "for windows", "for xbox",
    "xbox series xs edition", "xbox one edition", "xbox edition",
    "xbox series xs", "xbox one version", "xbox one",
    "pc edition", "windows 10 edition", "windows edition",
    "console edition",
    "windows", "console", "xs", "pc",
)

# Edition names the title_match table does not carry yet. "digital …"
# must go before the edition it qualifies, or "Digital Standard Edition"
# loses only "Standard Edition" and leaves "digital" behind.
_SEARCH_EDITION_SUFFIXES: tuple[str, ...] = (
    "digital standard edition", "digital deluxe edition",
)


def _strip_trailing(normalized: str, suffixes: tuple[str, ...]) -> str:
    """Strip trailing *suffixes* until none is left."""
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if normalized.endswith(" " + suffix):
                normalized = normalized[: -(len(suffix) + 1)].strip()
                changed = True
                break
    return normalized


def strip_platform_suffix(normalized: str) -> str:
    """Strip trailing platform words only (:data:`PLATFORM_SUFFIXES`).

    The narrow search query: "mafia definitive edition for xbox one" →
    "mafia definitive edition". Edition words stay, so a store search
    still finds the edition that is its own game instead of the bare
    franchise ("Mafia", the 2002 game).
    """
    return _strip_trailing(normalized, PLATFORM_SUFFIXES)


def strip_search_suffixes(normalized: str) -> str:
    """Strip platform and edition words: the wide search query.

    "doom eternal standard edition pc" → "doom eternal". Repeats until
    nothing changes, because the two kinds of suffix can be interleaved.
    """
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = strip_platform_suffix(normalized)
        normalized = _strip_trailing(normalized, _SEARCH_EDITION_SUFFIXES)
        normalized = strip_edition_suffix(normalized)
    return normalized
