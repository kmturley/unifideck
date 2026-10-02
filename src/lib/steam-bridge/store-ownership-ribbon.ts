/**
 * Steam Store "already owned elsewhere" ribbon: navigation detection.
 *
 * The Gaming Mode store is a separate BrowserView drawn ABOVE Steam's own
 * window, so nothing rendered from this React tree can appear on a store
 * page. This module only notices where the store is; the backend
 * (`show_store_ownership`) decides whether the game is owned elsewhere and,
 * only then, draws the ribbon into the page over CDP
 * (`py_modules/unifideck/cdp/store_ribbon.py`).
 *
 * Detection uses Steam's own store-browser callback list,
 * `GamepadUIMainWindowInstance.m_StoreBrowser.FinishedRequestCallbacks`,
 * with no CDP and no polling. Verified on-device (2026-10-02): it fires with
 * `(url, title)` for steam://openurl navigations, in-page link clicks and
 * Back, and the browser object survives leaving the store. It is created
 * lazily, the first time the store opens, so registration is retried on
 * every route change into `/steamweb`.
 *
 * Back/forward loads a fresh document, so every finished request is sent
 * and none is deduplicated by URL.
 */
import { call } from "@decky/api";
import { Router } from "@decky/ui";
import i18n from "i18next";
import { rpcRoutes } from "../../api/rpc-routes";
import { unwrapRpcEnvelope } from "../../api/useRPC";
import { STORE_VISUALS } from "../../types/store";
import { storeIconSpecs, type IconSpec } from "./store-icon-spec";
import type {
  GamepadMainWindowInternals,
  SteamStoreBrowser,
} from "../../types/steam";
import {
  STORE_OWNERSHIP_ENABLED_EVENT,
  isStoreOwnershipEnabled,
} from "../store-ownership-setting";

/** A Steam Store app page; the AppID must end at a path/query/hash boundary. */
export const STORE_APP_URL_RE =
  /^https:\/\/store\.steampowered\.com\/app\/(\d+)(?:[/?#]|$)/;

const STORE_ROUTE = "/steamweb";
/** Coalesces redirect bursts (app → agecheck → app) into one draw. */
const DEBOUNCE_MS = 300;
/** The store BrowserView is created lazily after the route changes. */
const RESOLVE_RETRIES = 8;
const RESOLVE_INTERVAL_MS = 250;
const LOG = "[Unifideck] Store ownership ribbon:";

/** Translated ribbon text. The page has no i18next, so it travels with the call. */
export interface RibbonStrings {
  tag_owned: string;
  tag_cloud: string;
  message_owned: string;
  message_cloud: string;
  installed: string;
  via: string;
  dir: "ltr" | "rtl";
  store_labels: Record<string, string>;
  /** Store logos as SVG data; the page falls back to a dot without one. */
  store_icons: Record<string, IconSpec>;
}

interface ShowStoreOwnershipResult {
  shown?: boolean;
  reason?: string;
  stores?: string[];
}

export function parseStoreAppId(url: unknown): number | null {
  if (typeof url !== "string") return null;
  const match = STORE_APP_URL_RE.exec(url);
  if (!match) return null;
  const appId = Number(match[1]);
  return Number.isSafeInteger(appId) && appId > 0 ? appId : null;
}

export function buildRibbonStrings(): RibbonStrings {
  const t = (key: string): string => String(i18n.t(key));
  const storeLabels: Record<string, string> = {};
  for (const [id, visual] of Object.entries(STORE_VISUALS)) {
    storeLabels[id] = visual.display_name;
  }
  return {
    tag_owned: t("storeOwnership.tagOwned"),
    tag_cloud: t("storeOwnership.tagCloud"),
    message_owned: t("storeOwnership.messageOwned"),
    message_cloud: t("storeOwnership.messageCloud"),
    installed: t("storeOwnership.installed"),
    via: t("storeOwnership.via"),
    dir: typeof i18n.dir === "function" && i18n.dir() === "rtl" ? "rtl" : "ltr",
    store_labels: storeLabels,
    store_icons: storeIconSpecs(),
  };
}

/** react-router history hands listeners a location (v4) or `{ location }` (v5). */
function pathnameOf(update: unknown): string {
  const u = update as
    | { pathname?: string; location?: { pathname?: string } }
    | undefined;
  return u?.location?.pathname ?? u?.pathname ?? "";
}

function gamepadWindow(): GamepadMainWindowInternals | undefined {
  return Router.WindowStore?.GamepadUIMainWindowInstance as
    | GamepadMainWindowInternals
    | undefined;
}

/**
 * Start watching the Gaming Mode store. Returns the disposer for
 * `teardown.ts`. Honours the QAM toggle live: off unregisters everything.
 */
export function startStoreOwnershipRibbon(): () => void {
  const warned = new Set<string>();
  const warnOnce = (key: string, message: string) => {
    if (warned.has(key)) return;
    warned.add(key);
    console.warn(`${LOG} ${message}`);
  };

  let active = false;
  let browser: SteamStoreBrowser | null = null;
  let registration: { Unregister(): void } | null = null;
  let unlisten: (() => void) | null = null;
  let debounce: ReturnType<typeof setTimeout> | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const draw = async (appId: number) => {
    try {
      const raw = await call<[number, RibbonStrings], unknown>(
        rpcRoutes.showStoreOwnership,
        appId,
        buildRibbonStrings(),
      );
      const result = unwrapRpcEnvelope<ShowStoreOwnershipResult | null>(raw, {
        route: rpcRoutes.showStoreOwnership,
        throwing: false,
      });
      if (result?.reason === "cdp_unavailable") {
        warnOnce(
          "cdp",
          "Steam's CEF debugger is unreachable (backend `cdp.port`), so the ribbon cannot be drawn on Store pages. See the Unifideck plugin log",
        );
      }
    } catch (e) {
      warnOnce("rpc", `show_store_ownership failed: ${String(e)}`);
    }
  };

  const onFinished = (url: unknown) => {
    if (!active) return;
    clearTimeout(debounce);
    const appId = parseStoreAppId(url);
    if (appId === null) return;
    debounce = setTimeout(() => void draw(appId), DEBOUNCE_MS);
  };

  /** True once there is nothing left to retry (registered, or unusable). */
  const ensureRegistered = (): boolean => {
    const win = gamepadWindow();
    if (!win) {
      warnOnce(
        "window",
        "Router.WindowStore.GamepadUIMainWindowInstance not found (desktop / non-Big-Picture UI). The ribbon only works in Gaming Mode",
      );
      return true;
    }
    const next = win.m_StoreBrowser;
    if (!next) return false;
    if (next === browser) return true;
    registration?.Unregister();
    registration = null;
    browser = next;
    const callbacks = next.FinishedRequestCallbacks;
    if (typeof callbacks?.Register !== "function") {
      warnOnce(
        "register",
        "GamepadUIMainWindowInstance.m_StoreBrowser.FinishedRequestCallbacks has no Register(). Steam changed its store browser, so the ribbon is disabled",
      );
      return true;
    }
    registration = callbacks.Register((url) => onFinished(url));
    onFinished(next.m_URL); // a store page already open before we registered
    return true;
  };

  const resolveSoon = (attempt = 0) => {
    clearTimeout(retry);
    if (!active || ensureRegistered()) return;
    if (attempt + 1 >= RESOLVE_RETRIES) {
      warnOnce(
        "browser",
        "GamepadUIMainWindowInstance.m_StoreBrowser not found after opening the Store. The ribbon is off until the next Store visit",
      );
      return;
    }
    retry = setTimeout(() => resolveSoon(attempt + 1), RESOLVE_INTERVAL_MS);
  };

  const onRoute = (update: unknown) => {
    if (pathnameOf(update).startsWith(STORE_ROUTE)) resolveSoon();
  };

  const activate = () => {
    if (active) return;
    active = true;
    const win = gamepadWindow();
    const history = win?.m_history;
    if (typeof history?.listen === "function") {
      unlisten = history.listen(onRoute) ?? null;
    } else if (win) {
      warnOnce(
        "history",
        "GamepadUIMainWindowInstance.m_history.listen not found. The ribbon can only attach to a Store that was already open when the plugin loaded",
      );
    }
    if (
      !ensureRegistered() &&
      pathnameOf(history?.location).startsWith(STORE_ROUTE)
    ) {
      resolveSoon();
    }
  };

  const deactivate = () => {
    active = false;
    clearTimeout(debounce);
    clearTimeout(retry);
    registration?.Unregister();
    registration = null;
    browser = null;
    unlisten?.();
    unlisten = null;
  };

  const onSetting = (e: Event) => {
    const on = (e as CustomEvent<boolean>).detail;
    if (on === true) activate();
    else if (on === false) deactivate();
  };

  window.addEventListener(STORE_OWNERSHIP_ENABLED_EVENT, onSetting);
  if (isStoreOwnershipEnabled()) activate();

  return () => {
    window.removeEventListener(STORE_OWNERSHIP_ENABLED_EVENT, onSetting);
    deactivate();
  };
}
