import { expect, test, type Page } from "@playwright/test";
import { startPlaytestPod, submitSingleCleanupDiscard } from "./support/playtest";
import { viewRevision } from "./support/progress";

type Marks = {
  gestureAt: number;
  commands: Record<string, { sent: number; receipt: number }>;
  revisions: Record<number, { received: number; ingested?: number; painted?: number }>;
  publicLogRequests: number;
};
type TimedWindow = Window & { playtestMarks: Marks };
type Sample = Record<string, number | string>;

async function observeTiming(page: Page) {
  await page.addInitScript(() => {
    const marks: Marks = { gestureAt: 0, commands: {}, revisions: {}, publicLogRequests: 0 };
    (window as TimedWindow).playtestMarks = marks;
    document.addEventListener("click", (event) => {
      if (event.target instanceof Element && event.target.closest('[data-testid="action-pass"]')) {
        marks.gestureAt = performance.now();
      }
    }, true);

    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const path = typeof input === "string" ? input : input instanceof URL ? input.pathname : input.url;
      if (/\/games\/[^/]+\/events(?:\?|$)/.test(path)) marks.publicLogRequests += 1;
      if (!path.endsWith("/commands") || typeof init?.body !== "string") return fetch(input, init);
      const id = JSON.parse(init.body).command_id as string;
      const command = { sent: performance.now(), receipt: 0 };
      marks.commands[id] = command;
      return fetch(input, init).then((result) => { command.receipt = performance.now(); return result; });
    };

    const Socket = window.WebSocket;
    class TimedSocket extends Socket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        if (!String(url).includes("/api/v1/games/")) return;
        this.addEventListener("message", (event) => {
          const received = performance.now();
          const message = JSON.parse(event.data);
          if (message.type === "projection") marks.revisions[message.packet.view_revision] = { received };
        });
      }
    }
    window.WebSocket = TimedSocket;
  });
}

test("record loopback pass and phase timing on a warmed four-seat table", async ({ browser }, info) => {
  test.skip(process.env.QUORUNE_TEST_TIMING !== "1", "Opt in to collecting timing samples");
  test.setTimeout(360_000);
  const { pages, contexts } = await startPlaytestPod(browser, Array.from({ length: 4 }, () => ({
    commander: "Yargle and Multani", cards: "99 Swamp",
  })), 1939, { width: 1366, height: 650 }, undefined, observeTiming);
  try {
    for (const page of pages) {
      const toggle = page.getByTestId("auto-pass-toggle");
      if (await toggle.getAttribute("aria-pressed") === "true") await toggle.click();
      await page.evaluate(() => {
        const table = document.querySelector<HTMLElement>(".game-shell")!;
        const state = (window as TimedWindow).playtestMarks;
        const observer = new MutationObserver(() => {
          const rev = Number(table.dataset.viewRevision);
          const packet = state.revisions[rev];
          if (!packet) return;
          packet.ingested = performance.now();
          requestAnimationFrame(() => requestAnimationFrame(() => {
            packet.painted = performance.now();
          }));
        });
        observer.observe(table, { attributes: true, attributeFilter: ["data-view-revision"] });
      });
    }
    const samples: Sample[] = [];
    let boundary = "";
    let boundaryStarted = 0;
    const anActor = async () => {
      const ready = await Promise.all(pages.map(async (page) => {
        const pass = page.getByTestId("action-pass");
        return await pass.isVisible() && await pass.isEnabled().catch(() => false);
      }));
      return pages[ready.indexOf(true)];
    };
    for (let i = 0; i < 60; i++) {
      let page: Page;
      await expect.poll(async () => {
        page = await anActor();
        if (page) return true;
        await submitSingleCleanupDiscard(pages);
        return false;
      }).toBe(true);
      await page!.bringToFront();
      const shell = page!.locator(".game-shell");
      const before = await viewRevision(page!);
      const step = await shell.getAttribute("data-step");
      const phase = await shell.getAttribute("data-phase");
      if (boundary !== `${phase}/${step}`) {
        boundary = `${phase}/${step}`;
        boundaryStarted = performance.now();
      }
      const responsePromise = page!.waitForResponse((result) => result.request().method() === "POST"
        && result.url().endsWith("/commands"));
      await page!.getByTestId("action-pass").click();
      const response = await responsePromise;
      const { receipt } = await response.json();
      expect(receipt.code).toBe("accepted");
      const revision = Number(receipt.state_revision);
      expect(revision).toBeGreaterThan(before);
      await expect.poll(() => viewRevision(page!)).toBe(revision);
      await expect.poll(() => page!.evaluate((rev) =>
        (window as TimedWindow).playtestMarks.revisions[rev]?.painted ?? 0, revision)).toBeGreaterThan(0);
      const browser = await page!.evaluate(({ rev, commandId }) => {
        const mark = (window as TimedWindow).playtestMarks;
        return { gesture: mark.gestureAt, ...mark.revisions[rev], ...mark.commands[commandId], logs: mark.publicLogRequests };
      }, { rev: revision, commandId: receipt.command_id });
      const gameId = await shell.getAttribute("data-game-id");
      const progress = await (await page!.request.get(`/api/v1/games/${gameId}/progress`)).json();
      const command = progress.last_command;
      expect(command.revision).toBe(revision);
      expect(command.interaction_id).toBe(receipt.command_id);
      const row: Sample = {
        command: i + 1, revision, boundary, interactionId: receipt.command_id,
        gestureToSendMs: browser.sent - browser.gesture,
        receiptRoundTripMs: browser.receipt - browser.sent,
        queueMs: command.queue_ms, executionMs: command.execution_ms,
        persistenceMs: command.persistence_ms, actorMs: command.actor_ms,
        sendToProjectionMs: browser.received - browser.sent,
        receiptToProjectionMs: browser.received - browser.receipt,
        ingestionMs: browser.ingested! - browser.received,
        paintMs: browser.painted! - browser.ingested!,
        gestureToPaintMs: browser.painted! - browser.gesture,
        cumulativePublicLogRequests: browser.logs,
      };
      if (`${await shell.getAttribute("data-phase")}/${await shell.getAttribute("data-step")}` !== boundary) {
        row.boundaryToPaintMs = performance.now() - boundaryStarted;
      }
      samples.push(row);
      if ((i + 1) % 15 === 0) console.log(`Collected ${i + 1} pass samples`);
    }
    await info.attach("loopback-pass-timing", { body: JSON.stringify(samples, null, 2), contentType: "application/json" });
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
