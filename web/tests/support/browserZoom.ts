import { mkdir, writeFile } from "node:fs/promises";
import { expect, type Browser, type BrowserContext, type Page, type TestInfo } from "@playwright/test";

export async function zoomContext(browser: Browser, info: TestInfo): Promise<BrowserContext | undefined> {
  if (process.env.QUORUNE_TEST_REAL_ZOOM !== "1") return undefined;
  if (browser.browserType().name() !== "chromium") throw new Error("The real zoom witness uses Chromium's tabs API");
  const extension = info.outputPath("zoom-extension");
  await mkdir(extension, { recursive: true });
  await writeFile(`${extension}/manifest.json`, JSON.stringify({
    name: "Quorune test zoom",
    version: "1.0",
    manifest_version: 3,
    permissions: ["tabs"],
    background: { service_worker: "zoom.js" },
  }));
  await writeFile(`${extension}/zoom.js`, 'chrome.runtime.onInstalled.addListener(() => {});');
  return browser.browserType().launchPersistentContext("", {
    channel: "chromium",
    headless: true,
    baseURL: info.project.use.baseURL,
    viewport: { width: 1366, height: 650 },
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    ...(process.env.QUORUNE_RECORD_PLAYTEST === "1" ? {
      recordVideo: { dir: info.outputPath("recordings"), size: { width: 1366, height: 650 } },
    } : {}),
  });
}

type TabsApi = {
  tabs: {
    query: (selector: { url: string }) => Promise<{ id: number }[]>;
    setZoom: (tab: number, factor: number) => Promise<void>;
    getZoom: (tab: number) => Promise<number>;
  };
};

export async function captureRealZoom(page: Page, info: TestInfo): Promise<void> {
  if (process.env.QUORUNE_TEST_REAL_ZOOM !== "1") return;
  const context = page.context();
  const worker = context.serviceWorkers()[0] ?? await context.waitForEvent("serviceworker");
  const measurements = [];
  async function zoom(factor: number) {
    const applied = await worker.evaluate(async ({ url, scale }) => {
      const browser = (globalThis as unknown as { chrome: TabsApi }).chrome;
      const [tab] = await browser.tabs.query({ url });
      await browser.tabs.setZoom(tab.id, scale);
      return browser.tabs.getZoom(tab.id);
    }, { url: page.url(), scale: factor });
    expect(applied).toBeCloseTo(factor, 2);
  }
  for (const [width, height] of [[1366, 650], [1536, 746], [1920, 962]]) {
    await zoom(1);
    await page.setViewportSize({ width, height });
    const baseline = await page.evaluate(() => devicePixelRatio);
    for (const scale of [0.8, 1, 1.25, 1.5, 2]) {
      await zoom(scale);
      await expect.poll(() => page.evaluate((base) => devicePixelRatio / base, baseline)).toBeCloseTo(scale, 2);
      const result = await page.evaluate(() => {
        const dock = document.querySelector("#decision-tray")!.getBoundingClientRect();
        const hand = document.querySelector(".hand-scroll")?.getBoundingClientRect();
        const shortcutName = document.querySelector("[data-zone-offer] .card-copy strong")?.getBoundingClientRect();
        return {
          width: innerWidth, height: innerHeight, pixelRatio: devicePixelRatio,
          dockTop: dock.top, dockBottom: dock.bottom, scrollHeight: document.documentElement.scrollHeight,
          shortcutNameFits: hand && shortcutName ? shortcutName.bottom <= hand.bottom : null,
          shortcutNameBottom: shortcutName?.bottom ?? null, handBottom: hand?.bottom ?? null,
        };
      });
      expect(result.dockTop).toBeGreaterThanOrEqual(0);
      expect(result.dockBottom).toBeLessThanOrEqual(result.height);
      expect(result.scrollHeight).toBe(result.height);
      if (scale === 2 && result.shortcutNameFits !== null) expect(result.shortcutNameFits, JSON.stringify(result)).toBe(true);
      await expect(page.getByTestId("open-game-menu")).toBeVisible();
      measurements.push({ baseViewport: [width, height], scale, ...result });
      await page.screenshot({ path: info.outputPath(`table-${result.width}x${result.height}-zoom-${Math.round(scale * 100)}.png`) });
    }
  }
  await zoom(1);
  await page.setViewportSize({ width: 1366, height: 650 });
  await info.attach("real-browser-zoom", { body: JSON.stringify(measurements, null, 2), contentType: "application/json" });
}
