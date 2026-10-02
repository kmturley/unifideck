// @vitest-environment jsdom
/**
 * Store-ownership ribbon detection: when the module calls the backend, and
 * when it must not. Steam's store browser is faked with the shape verified
 * on-device: `FinishedRequestCallbacks` is a getter returning a callback
 * list whose `Register` hands back `{ Unregister }`, and the browser object
 * is created lazily.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { envelope } from "../../test-support/rpc-envelope";

const callMock = vi.fn();
vi.mock("@decky/api", () => ({ call: (...a: unknown[]) => callMock(...a) }));

const routerHolder: { window: unknown } = { window: undefined };
vi.mock("@decky/ui", () => ({
  Router: {
    get WindowStore() {
      return { GamepadUIMainWindowInstance: routerHolder.window };
    },
  },
}));
vi.mock("i18next", () => ({
  default: { t: (key: string) => `t:${key}`, dir: () => "ltr" },
}));

import {
  buildRibbonStrings,
  parseStoreAppId,
  startStoreOwnershipRibbon,
} from "./store-ownership-ribbon";
import { STORE_OWNERSHIP_ENABLED_KEY, setStoreOwnershipEnabled } from "../store-ownership-setting";

type Cb = (url: string, title: string) => void;

function fakeBrowser(url = "https://store.steampowered.com/") {
  const callbacks: Cb[] = [];
  const unregister = vi.fn();
  const browser = {
    m_URL: url,
    get FinishedRequestCallbacks() {
      return {
        Register: (cb: Cb) => {
          callbacks.push(cb);
          return {
            Unregister: () => {
              unregister();
              callbacks.splice(callbacks.indexOf(cb), 1);
            },
          };
        },
      };
    },
  };
  const fire = (next: string) => {
    browser.m_URL = next;
    callbacks.slice().forEach((cb) => cb(next, "title"));
  };
  return { browser, callbacks, unregister, fire };
}

function fakeWindow(storeBrowser?: unknown, pathname = "/library/home") {
  const listeners: Array<(u: unknown) => void> = [];
  const unlisten = vi.fn();
  const win = {
    m_StoreBrowser: storeBrowser,
    m_history: {
      location: { pathname },
      listen: (fn: (u: unknown) => void) => {
        listeners.push(fn);
        return unlisten;
      },
    },
  };
  const navigate = (path: string) => listeners.forEach((fn) => fn({ pathname: path }));
  return { win, navigate, unlisten, listeners };
}

const APP_URL = "https://store.steampowered.com/app/257350/Baldurs_Gate_II_Enhanced_Edition/";

beforeEach(() => {
  vi.useFakeTimers();
  callMock.mockReset();
  callMock.mockResolvedValue(envelope({ shown: true, reason: "shown", stores: ["gog"] }));
  window.localStorage.clear();
  routerHolder.window = undefined;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("parseStoreAppId", () => {
  it.each([
    [APP_URL, 257350],
    ["https://store.steampowered.com/app/257350", 257350],
    ["https://store.steampowered.com/app/257350?snr=1_5_9__405", 257350],
    ["https://store.steampowered.com/app/257350#reviews", 257350],
    ["https://store.steampowered.com/agecheck/app/257350/", null],
    ["https://store.steampowered.com/", null],
    ["http://store.steampowered.com/app/257350/", null],
    ["https://store.steampowered.com.evil.example/app/257350/", null],
    [undefined, null],
    [42, null],
  ])("%s → %s", (url, expected) => {
    expect(parseStoreAppId(url)).toBe(expected);
  });
});

describe("buildRibbonStrings", () => {
  it("translates every key and carries the store labels", () => {
    const s = buildRibbonStrings();
    expect(s.tag_owned).toBe("t:storeOwnership.tagOwned");
    expect(s.message_cloud).toBe("t:storeOwnership.messageCloud");
    expect(s.dir).toBe("ltr");
    expect(s.store_labels.gog).toBe("GOG");
    expect(s.store_labels.microsoft).toBe("Xbox");
  });
});

describe("startStoreOwnershipRibbon", () => {
  it("handles the store page already open when it registers", async () => {
    const { browser } = fakeBrowser(APP_URL);
    routerHolder.window = fakeWindow(browser, "/steamweb").win;

    const dispose = startStoreOwnershipRibbon();
    await vi.advanceTimersByTimeAsync(300);

    expect(callMock).toHaveBeenCalledTimes(1);
    expect(callMock.mock.calls[0][0]).toBe("show_store_ownership");
    expect(callMock.mock.calls[0][1]).toBe(257350);
    expect(callMock.mock.calls[0][2].tag_owned).toBe("t:storeOwnership.tagOwned");
    dispose();
  });

  it("calls the backend on every app navigation, including a repeat (Back)", async () => {
    const fake = fakeBrowser();
    routerHolder.window = fakeWindow(fake.browser).win;
    const dispose = startStoreOwnershipRibbon();

    fake.fire(APP_URL);
    await vi.advanceTimersByTimeAsync(300);
    fake.fire("https://store.steampowered.com/app/1091500/");
    await vi.advanceTimersByTimeAsync(300);
    fake.fire(APP_URL);
    await vi.advanceTimersByTimeAsync(300);

    expect(callMock.mock.calls.map((c) => c[1])).toEqual([257350, 1091500, 257350]);
    dispose();
  });

  it("never calls the backend for non-app pages, and debounces bursts", async () => {
    const fake = fakeBrowser();
    routerHolder.window = fakeWindow(fake.browser).win;
    const dispose = startStoreOwnershipRibbon();

    fake.fire("https://store.steampowered.com/cart/");
    fake.fire(APP_URL);
    fake.fire("https://store.steampowered.com/agecheck/app/257350/");
    await vi.advanceTimersByTimeAsync(1000);

    expect(callMock).not.toHaveBeenCalled();
    dispose();
  });

  it("registers once the lazily created store browser appears", async () => {
    const fw = fakeWindow(undefined);
    routerHolder.window = fw.win;
    const dispose = startStoreOwnershipRibbon();
    const fake = fakeBrowser(APP_URL);

    fw.win.m_StoreBrowser = fake.browser;
    fw.navigate("/steamweb");
    await vi.advanceTimersByTimeAsync(300);

    expect(fake.callbacks).toHaveLength(1);
    expect(callMock).toHaveBeenCalledTimes(1);
    dispose();
  });

  it("moves its registration when Steam replaces the store browser", async () => {
    const first = fakeBrowser();
    const fw = fakeWindow(first.browser);
    routerHolder.window = fw.win;
    const dispose = startStoreOwnershipRibbon();
    const second = fakeBrowser();

    fw.win.m_StoreBrowser = second.browser;
    fw.navigate("/steamweb");

    expect(first.unregister).toHaveBeenCalledTimes(1);
    expect(second.callbacks).toHaveLength(1);
    dispose();
  });

  it("the disposer unregisters and unlistens", async () => {
    const fake = fakeBrowser();
    const fw = fakeWindow(fake.browser);
    routerHolder.window = fw.win;

    startStoreOwnershipRibbon()();
    fake.fire(APP_URL);
    await vi.advanceTimersByTimeAsync(1000);

    expect(fake.unregister).toHaveBeenCalledTimes(1);
    expect(fw.unlisten).toHaveBeenCalledTimes(1);
    expect(callMock).not.toHaveBeenCalled();
  });

  it("warns once and does not throw outside Big Picture", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    routerHolder.window = undefined;

    const dispose = startStoreOwnershipRibbon();
    window.dispatchEvent(
      new CustomEvent("unifideck:store-ownership-enabled-change", { detail: false }),
    );
    window.dispatchEvent(
      new CustomEvent("unifideck:store-ownership-enabled-change", { detail: true }),
    );

    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain("GamepadUIMainWindowInstance");
    dispose();
  });

  it("does nothing while disabled, and attaches when enabled live", async () => {
    window.localStorage.setItem(STORE_OWNERSHIP_ENABLED_KEY, "0");
    const fake = fakeBrowser(APP_URL);
    routerHolder.window = fakeWindow(fake.browser).win;

    const dispose = startStoreOwnershipRibbon();
    expect(fake.callbacks).toHaveLength(0);

    setStoreOwnershipEnabled(true);
    await vi.advanceTimersByTimeAsync(300);

    expect(fake.callbacks).toHaveLength(1);
    expect(callMock).toHaveBeenCalledTimes(1);
    dispose();
  });

  it("warns once when the backend cannot reach CDP", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    callMock.mockResolvedValue(
      envelope({ shown: false, reason: "cdp_unavailable", stores: ["gog"] }),
    );
    const fake = fakeBrowser();
    routerHolder.window = fakeWindow(fake.browser).win;
    const dispose = startStoreOwnershipRibbon();

    fake.fire(APP_URL);
    await vi.advanceTimersByTimeAsync(300);
    fake.fire(APP_URL);
    await vi.advanceTimersByTimeAsync(300);

    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain("cdp.port");
    dispose();
  });
});
