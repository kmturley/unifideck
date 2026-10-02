"""Draw the ownership ribbon into the Steam Store page that shows a given AppID.

The frontend notices store navigation through Steam's own
``m_StoreBrowser.FinishedRequestCallbacks`` and asks the backend to draw;
this module finds the CEF page target(s) on that exact app page and
evaluates :mod:`unifideck.cdp.store_ribbon_js` in each.

Matching is by **exact AppID**, never by substring: ``/app/25735`` must not
match a page on ``/app/257350``. The target list can lag the navigation by a
moment, so a miss is retried briefly before giving up.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

from unifideck.cdp.page_inject import inject_into_target, list_page_targets
from unifideck.cdp.store_ribbon_js import build_ribbon_script

logger = logging.getLogger(__name__)

#: A Steam Store app page. Anchored on both ends of the host so a look-alike
#: host cannot match, and the AppID must end at a path, query or fragment
#: boundary.
STORE_APP_URL_RE = re.compile(
    r"^https://store\.steampowered\.com/app/(\d+)(?:[/?#]|$)",
)

_LOG_PREFIX = "store-ribbon"

#: Ports already warned about, so an unreachable debugger logs once rather
#: than on every store page. Cleared on the next successful listing.
_warned_ports: set[int] = set()


@dataclass(frozen=True)
class RibbonInjectOutcome:
    """What happened to one draw request.

    Attributes:
        shown: True when at least one page evaluated the ribbon.
        reason: ``"shown"``, ``"no_target"`` (no page on that AppID),
            ``"cdp_unavailable"`` (debugger unreachable) or
            ``"eval_failed"`` (pages matched, every evaluation failed).
        targets: how many pages were evaluated (or attempted, on failure).
    """

    shown: bool
    reason: str
    targets: int = 0


def matching_store_targets(
    targets: list[dict[str, Any]], steam_app_id: int,
) -> list[dict[str, Any]]:
    """The page targets currently on the store page of *steam_app_id*."""
    matches: list[dict[str, Any]] = []
    for target in targets:
        if target.get("type") != "page":
            continue
        found = STORE_APP_URL_RE.match(str(target.get("url", "")))
        if found and int(found.group(1)) == steam_app_id:
            matches.append(target)
    return matches


async def inject_store_ribbon(
    port: int,
    steam_app_id: int,
    payload: Mapping[str, Any],
    *,
    attempts: int = 3,
    retry_delay_s: float = 0.4,
    list_timeout_s: float = 2.0,
    ws_timeout_s: float = 5.0,
) -> RibbonInjectOutcome:
    """Evaluate the ribbon for *payload* in every page on *steam_app_id*.

    Never raises. Every matching page is drawn into: each one re-checks its
    own ``location.pathname`` before rendering, so a page that navigated on
    in the meantime simply ignores the call.
    """
    script = build_ribbon_script(payload)
    for attempt in range(max(1, attempts)):
        try:
            targets = await list_page_targets(port, timeout=list_timeout_s)
        except (TimeoutError, aiohttp.ClientError, OSError, ValueError) as exc:
            _warn_unreachable(port, exc)
            return RibbonInjectOutcome(shown=False, reason="cdp_unavailable")
        _warned_ports.discard(port)
        matches = matching_store_targets(targets, steam_app_id)
        if matches:
            return await _inject_all(matches, script, ws_timeout_s)
        if attempt + 1 < attempts:
            await asyncio.sleep(retry_delay_s)
    return RibbonInjectOutcome(shown=False, reason="no_target")


async def _inject_all(
    matches: list[dict[str, Any]], script: str, ws_timeout_s: float,
) -> RibbonInjectOutcome:
    """Evaluate *script* in each matched page; succeed if any page took it."""
    drawn = 0
    for target in matches:
        if await inject_into_target(
            target, [script], ws_timeout=ws_timeout_s, logger_prefix=_LOG_PREFIX,
        ):
            drawn += 1
    if drawn:
        return RibbonInjectOutcome(shown=True, reason="shown", targets=drawn)
    logger.warning(
        "[%s] %d Steam Store page(s) matched but none evaluated the "
        "ownership ribbon",
        _LOG_PREFIX, len(matches),
    )
    return RibbonInjectOutcome(
        shown=False, reason="eval_failed", targets=len(matches),
    )


def _warn_unreachable(port: int, exc: BaseException) -> None:
    """Rule 8: say what was lost and where we looked, once per port."""
    if port in _warned_ports:
        return
    _warned_ports.add(port)
    logger.warning(
        "[%s] Steam's CEF debugger is unreachable at "
        "http://127.0.0.1:%d/json (config `cdp.port`): the 'already owned' "
        "ribbon cannot be drawn on Steam Store pages: %s",
        _LOG_PREFIX, port, exc,
    )
