// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

vi.mock("@decky/api", () => ({ call: vi.fn() }));
vi.mock("../../api/useRPC", () => ({
  unwrapRpcEnvelope: (raw: unknown) => raw,
}));
vi.mock("../protondb-cache", () => ({
  meetsGreatOnCurrentDevice: vi.fn(),
  getCachedCompatByTitle: vi.fn(),
  getCachedRating: vi.fn(),
  loadCompatCacheFromBackend: vi.fn(),
}));
vi.mock("../library-facets", () => ({
  getCompatByShortcutAppId: vi.fn(),
  loadFacets: vi.fn(),
}));
vi.mock("../device-type", () => ({
  activeCompatTrack: () => "deck",
}));
vi.mock("../../api/event-bus-client", () => ({
  EventBusClient: {
    subscribe: vi.fn(),
  },
}));
// The real module is localStorage-backed, and this test environment's
// `window.localStorage` doesn't implement `setItem`/`getItem` (a known
// jsdom/vitest-environment limitation — see the pre-existing, unrelated
// `collection-manager.test.ts` failures for the same root cause). An
// in-memory stand-in keeps these tests deterministic without depending
// on that.
vi.mock("../group-duplicates-setting", () => {
  let enabled = false;
  return {
    GROUP_DUPLICATES_EVENT: "unifideck:group-duplicates-change",
    isGroupDuplicatesEnabled: () => enabled,
    setGroupDuplicatesEnabled: (on: boolean) => {
      enabled = on;
    },
  };
});

import { call } from "@decky/api";
import { meetsGreatOnCurrentDevice } from "../protondb-cache";
import {
  runFilter,
  unifideckGameCache,
  validThirdPartyCache,
  loadUnifideckCache,
  isUnifideckCacheLoaded,
  updateUnifideckCache,
  isHiddenDuplicate,
  getGroupSiblings,
  appIdsMatch,
  type UnifideckGameInput,
} from "./index";
import {
  isGroupDuplicatesEnabled,
  setGroupDuplicatesEnabled,
} from "../group-duplicates-setting";
import type { SteamAppOverview } from "../../types/steam";

const NON_STEAM_APP_TYPE = 1073741824;
const mockCall = vi.mocked(call);

// Every test in this file should start from the documented default (off).
afterEach(() => {
  setGroupDuplicatesEnabled(false);
});

describe("library-filters/index.ts installed filter", () => {
  beforeEach(() => {
    unifideckGameCache.clear();
    validThirdPartyCache.clear();
  });

  it("includes an installed Steam game", () => {
    const app = {
      appid: 12345,
      app_type: 1, // Native Steam Game
      installed: true,
      display_name: "Steam Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(true);
  });

  it("excludes an uninstalled Steam game", () => {
    const app = {
      appid: 12345,
      app_type: 1,
      installed: false,
      display_name: "Steam Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(false);
  });

  it("includes an installed Unified game", () => {
    unifideckGameCache.set(999, {
      store: "epic",
      isInstalled: true,
    });

    const app = {
      appid: 999,
      app_type: NON_STEAM_APP_TYPE,
      installed: true,
      display_name: "Unified Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(true);
  });

  it("excludes an uninstalled Unified game", () => {
    unifideckGameCache.set(999, {
      store: "epic",
      isInstalled: false,
    });

    const app = {
      appid: 999,
      app_type: NON_STEAM_APP_TYPE,
      installed: true, // Steam might report it as installed because it's a shortcut
      display_name: "Unified Game",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(false);
  });

  it("excludes non-Unifideck third-party shortcuts", () => {
    const app = {
      appid: 777,
      app_type: NON_STEAM_APP_TYPE,
      installed: true,
      display_name: "Custom Shortcut",
    } as unknown as SteamAppOverview;

    const result = runFilter({ type: "installed", params: { installed: true } }, app);
    expect(result).toBe(false);
  });
});

describe('"all" filter hides non-primary cross-store duplicates', () => {
  beforeEach(() => {
    unifideckGameCache.clear();
    validThirdPartyCache.clear();
    setGroupDuplicatesEnabled(true); // this describe block tests the hidden case
  });

  function appFor(appId: number): SteamAppOverview {
    return {
      appid: appId,
      app_type: NON_STEAM_APP_TYPE,
      installed: false,
      display_name: "whatever",
    } as unknown as SteamAppOverview;
  }

  it("shows only the primary tile for a duplicate group, hides the rest", () => {
    const games: UnifideckGameInput[] = [
      {
        appId: 1,
        store: "epic",
        isInstalled: false,
        title: "Doors - Paradox",
        dedupeGroupId: "doors paradox",
      },
      {
        appId: 2,
        store: "amazon",
        isInstalled: false,
        title: "Doors: Paradox",
        dedupeGroupId: "doors paradox",
      },
    ];
    updateUnifideckCache(games);

    const visible = games.filter((g) =>
      runFilter({ type: "all", params: {} }, appFor(g.appId)),
    );
    expect(visible).toHaveLength(1);
    // Fixed store priority (no installed copy): epic beats amazon.
    expect(visible[0].appId).toBe(1);
    expect(isHiddenDuplicate(2)).toBe(true);
    expect(isHiddenDuplicate(1)).toBe(false);
  });

  it("prefers an installed copy as the surviving tile over store priority", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false, dedupeGroupId: "g" },
      { appId: 2, store: "amazon", isInstalled: true, dedupeGroupId: "g" },
    ];
    updateUnifideckCache(games);

    expect(isHiddenDuplicate(2)).toBe(false);
    expect(isHiddenDuplicate(1)).toBe(true);
  });

  it("leaves ungrouped games untouched", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false },
      { appId: 2, store: "gog", isInstalled: false },
    ];
    updateUnifideckCache(games);

    const visible = games.filter((g) =>
      runFilter({ type: "all", params: {} }, appFor(g.appId)),
    );
    expect(visible).toHaveLength(2);
  });

  it("still exposes every sibling via getGroupSiblings for the detail-page switcher", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false, dedupeGroupId: "g" },
      { appId: 2, store: "amazon", isInstalled: true, dedupeGroupId: "g" },
    ];
    updateUnifideckCache(games);

    expect(getGroupSiblings(1).map((s) => s.appId).sort()).toEqual([1, 2]);
    expect(getGroupSiblings(2).map((s) => s.appId).sort()).toEqual([1, 2]);
  });
});

describe("Steam-owned cross-reference", () => {
  beforeEach(() => {
    unifideckGameCache.clear();
    validThirdPartyCache.clear();
    setGroupDuplicatesEnabled(true); // this describe block tests the hidden case
  });

  function appFor(appId: number): SteamAppOverview {
    return {
      appid: appId,
      app_type: NON_STEAM_APP_TYPE,
      installed: false,
      display_name: "whatever",
    } as unknown as SteamAppOverview;
  }

  it("hides a singleton Unifideck game already owned on real Steam", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false, steamOwnedAppId: 632470 },
    ];
    updateUnifideckCache(games);

    expect(isHiddenDuplicate(1)).toBe(true);
    expect(runFilter({ type: "all", params: {} }, appFor(1))).toBe(false);
  });

  it("hides every cross-store copy when any of them is also Steam-owned", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false, dedupeGroupId: "g" },
      {
        appId: 2,
        store: "amazon",
        isInstalled: true,
        dedupeGroupId: "g",
        steamOwnedAppId: 632470,
      },
    ];
    updateUnifideckCache(games);

    expect(isHiddenDuplicate(1)).toBe(true);
    expect(isHiddenDuplicate(2)).toBe(true);
  });

  it("leaves a non-Steam-owned game visible", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false },
    ];
    updateUnifideckCache(games);

    expect(isHiddenDuplicate(1)).toBe(false);
    expect(runFilter({ type: "all", params: {} }, appFor(1))).toBe(true);
  });

  it("adds a synthetic steam sibling for an otherwise-ungrouped title", () => {
    const games: UnifideckGameInput[] = [
      {
        appId: 1,
        store: "epic",
        isInstalled: false,
        title: "Disco Elysium",
        steamOwnedAppId: 632470,
      },
    ];
    updateUnifideckCache(games);

    const siblings = getGroupSiblings(1);
    expect(siblings).toHaveLength(2);
    expect(siblings.map((s) => s.store).sort()).toEqual(["epic", "steam"]);
    expect(siblings.find((s) => s.store === "steam")?.appId).toBe(632470);
  });

  it("carries the Steam copy's own edition label onto the synthetic sibling", () => {
    const games: UnifideckGameInput[] = [
      {
        appId: 1,
        store: "epic",
        isInstalled: false,
        title: "Disco Elysium",
        steamOwnedAppId: 632470,
        steamOwnedEditionLabel: "The Final Cut",
      },
    ];
    updateUnifideckCache(games);

    const steamSibling = getGroupSiblings(1).find((s) => s.store === "steam");
    expect(steamSibling?.editionLabel).toBe("The Final Cut");
  });

  it("appends the steam sibling onto an existing cross-store group", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false, dedupeGroupId: "g" },
      {
        appId: 2,
        store: "amazon",
        isInstalled: false,
        dedupeGroupId: "g",
        steamOwnedAppId: 632470,
        steamOwnedEditionLabel: "The Final Cut",
      },
    ];
    updateUnifideckCache(games);

    const siblings = getGroupSiblings(1);
    expect(siblings.map((s) => s.store).sort()).toEqual(["amazon", "epic", "steam"]);
    // The label came from the entry that actually carried steamOwnedAppId
    // (appId 2), even though we queried from a different member (appId 1).
    expect(siblings.find((s) => s.store === "steam")?.editionLabel).toBe(
      "The Final Cut",
    );
  });

  it("resolves siblings when queried by the real Steam appid (native Steam page)", () => {
    const games: UnifideckGameInput[] = [
      {
        appId: 1,
        store: "epic",
        isInstalled: false,
        title: "Disco Elysium",
        steamOwnedAppId: 632470,
      },
    ];
    updateUnifideckCache(games);

    // The native Disco Elysium app-details page is opened by its own real
    // Steam appid (632470), which is never a key in unifideckGameCache —
    // only Unifideck shortcut appIds are. getGroupSiblings must still find
    // the Epic copy via the reverse index.
    const siblings = getGroupSiblings(632470);
    expect(siblings.map((s) => s.store).sort()).toEqual(["epic", "steam"]);
  });

  it("resolves siblings by real Steam appid for a multi-store group too", () => {
    const games: UnifideckGameInput[] = [
      { appId: 1, store: "epic", isInstalled: false, dedupeGroupId: "g" },
      {
        appId: 2,
        store: "amazon",
        isInstalled: false,
        dedupeGroupId: "g",
        steamOwnedAppId: 632470,
      },
    ];
    updateUnifideckCache(games);

    const siblings = getGroupSiblings(632470);
    expect(siblings.map((s) => s.store).sort()).toEqual(["amazon", "epic", "steam"]);
  });

  it("returns [] for an unrelated real Steam appid with no Unifideck match", () => {
    expect(getGroupSiblings(999999)).toEqual([]);
  });
});

describe("appIdsMatch", () => {
  it("matches identical values", () => {
    expect(appIdsMatch(12345, 12345)).toBe(true);
  });

  it("matches an unsigned shortcut appid against its signed (negative) variant", () => {
    const unsigned = 3894638122; // > 0x7fffffff
    const signed = unsigned - 0x100000000; // negative int32 form
    expect(appIdsMatch(unsigned, signed)).toBe(true);
    expect(appIdsMatch(signed, unsigned)).toBe(true);
  });

  it("does not match unrelated appids", () => {
    expect(appIdsMatch(1, 2)).toBe(false);
  });
});

describe('"Group duplicates" setting', () => {
  beforeEach(() => {
    unifideckGameCache.clear();
    validThirdPartyCache.clear();
  });

  afterEach(() => {
    vi.mocked(meetsGreatOnCurrentDevice).mockReset();
  });

  function appFor(appId: number): SteamAppOverview {
    return {
      appid: appId,
      app_type: NON_STEAM_APP_TYPE,
      installed: true,
      display_name: "whatever",
    } as unknown as SteamAppOverview;
  }

  const duplicateGames: UnifideckGameInput[] = [
    { appId: 1, store: "epic", isInstalled: true, dedupeGroupId: "g" },
    { appId: 2, store: "amazon", isInstalled: true, dedupeGroupId: "g" },
  ];

  it("defaults to off — isGroupDuplicatesEnabled() is false with no prior choice", () => {
    expect(isGroupDuplicatesEnabled()).toBe(false);
  });

  it("off by default: the All Games tab shows every store's copy separately", () => {
    updateUnifideckCache(duplicateGames);

    const visible = duplicateGames.filter((g) =>
      runFilter({ type: "all", params: {} }, appFor(g.appId)),
    );
    expect(visible).toHaveLength(2);
  });

  it("on: the All Games tab collapses to the primary tile only", () => {
    setGroupDuplicatesEnabled(true);
    updateUnifideckCache(duplicateGames);

    const visible = duplicateGames.filter((g) =>
      runFilter({ type: "all", params: {} }, appFor(g.appId)),
    );
    expect(visible).toHaveLength(1);
  });

  it("off by default: the Installed tab shows every store's copy separately", () => {
    updateUnifideckCache(duplicateGames);

    const visible = duplicateGames.filter((g) =>
      runFilter(
        { type: "installed", params: { installed: true } },
        appFor(g.appId),
      ),
    );
    expect(visible).toHaveLength(2);
  });

  it("on: the Installed tab also collapses duplicates to the primary tile", () => {
    setGroupDuplicatesEnabled(true);
    updateUnifideckCache(duplicateGames);

    const visible = duplicateGames.filter((g) =>
      runFilter(
        { type: "installed", params: { installed: true } },
        appFor(g.appId),
      ),
    );
    expect(visible).toHaveLength(1);
  });

  it("off by default: the Great on Deck tab shows every store's copy separately", () => {
    vi.mocked(meetsGreatOnCurrentDevice).mockReturnValue(true);
    updateUnifideckCache(duplicateGames);

    const visible = duplicateGames.filter((g) =>
      runFilter({ type: "deckCompat", params: {} }, appFor(g.appId)),
    );
    expect(visible).toHaveLength(2);
  });

  it("on: the Great on Deck tab also collapses duplicates to the primary tile", () => {
    vi.mocked(meetsGreatOnCurrentDevice).mockReturnValue(true);
    setGroupDuplicatesEnabled(true);
    updateUnifideckCache(duplicateGames);

    const visible = duplicateGames.filter((g) =>
      runFilter({ type: "deckCompat", params: {} }, appFor(g.appId)),
    );
    expect(visible).toHaveLength(1);
  });

  it("isHiddenDuplicate itself is unaffected by the setting — only the tab filters gate on it", () => {
    updateUnifideckCache(duplicateGames);
    // Group membership is computed unconditionally; "off" only stops the
    // tab filters from acting on it.
    expect(isHiddenDuplicate(1) || isHiddenDuplicate(2)).toBe(true);
  });
});

describe("loadUnifideckCache fail-open (UD-043 / UD-008)", () => {
  beforeEach(async () => {
    // Reset the module-level retry counter via a successful load so
    // each test starts from a clean retry budget regardless of order.
    mockCall.mockReset();
    mockCall.mockResolvedValueOnce([]);
    await loadUnifideckCache();
    unifideckGameCache.clear();
    mockCall.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  const okRow = (appId: number) => ({
    app_id: appId,
    store: "epic",
    installed: true,
    store_game_id: String(appId),
  });

  it("populates the cache on a successful RPC", async () => {
    mockCall.mockResolvedValueOnce([okRow(999)]);
    await loadUnifideckCache();
    expect(unifideckGameCache.has(999)).toBe(true);
    expect(isUnifideckCacheLoaded()).toBe(true);
  });

  it("does NOT wipe an existing cache when the RPC fails", async () => {
    // Seed a good cache first.
    mockCall.mockResolvedValueOnce([okRow(999)]);
    await loadUnifideckCache();
    expect(unifideckGameCache.has(999)).toBe(true);

    // A later transient failure must not clear the previously-loaded
    // games — that was the UD-043 "synced but 0 shown" bug.
    mockCall.mockRejectedValueOnce(new Error("network down"));
    await loadUnifideckCache();
    expect(unifideckGameCache.has(999)).toBe(true);
  });

  it("retries with backoff after a failure, then succeeds", async () => {
    // First call rejects, the scheduled retry resolves.
    mockCall.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce([okRow(1234)]);

    await loadUnifideckCache();
    expect(unifideckGameCache.has(1234)).toBe(false); // not yet

    // Advance past the backoff window (generous — the base delay
    // scales with the module-level retry count, which other tests in
    // this file may have bumped). The scheduled retry fires + resolves.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(unifideckGameCache.has(1234)).toBe(true);
  });
});
