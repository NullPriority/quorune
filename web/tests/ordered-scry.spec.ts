import { expect, test, type Browser, type Page, type TestInfo } from "@playwright/test";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { startRoomGame } from "./support/startup";
import {
  annotateJourneyMetrics,
  driveUntil,
  submitAuthorizedPass,
  viewRevision,
} from "./support/progress";

const execFileAsync = promisify(execFile);

const orderedScryDeck = `Commander:
1 Ordered Scry Witness

Mainboard:
99 Island
`;

const defenderDeck = `Commander:
1 Yargle and Multani

Mainboard:
50 Swamp
49 Forest
`;

async function enter(page: Page, name: string) {
  await page.goto("/");
  await page.getByTestId("display-name").fill(name);
  await page.getByTestId("create-guest").click();
  await expect(page.getByRole("heading", { name: "Find your table" })).toBeVisible();
}

async function submitDeck(
  page: Page,
  name: string,
  commander: string,
  deck: string,
) {
  await page.getByTestId("deck-name").fill(name);
  await page.getByTestId("commander-name").fill(commander);
  await page.getByTestId("deck-list").fill(deck);
  await page.getByTestId("submit-deck").click();
  await expect(
    page.locator(".success-banner, .warning-banner").filter({
      hasText: /Deck (validated|accepted)/,
    }),
  ).toBeVisible();
}

async function submitImmediateAction(page: Page, actionId: string) {
  const revision = await viewRevision(page);
  await page.getByTestId(`action-${actionId}`).click();
  await expect.poll(() => viewRevision(page)).toBeGreaterThan(revision);
}

async function submitOpenChoice(page: Page) {
  const revision = await viewRevision(page);
  await page.getByTestId("submit-choice").click();
  await expect.poll(() => viewRevision(page)).toBeGreaterThan(revision);
}

async function actionIsReady(page: Page, testId: string): Promise<boolean> {
  const action = page.getByTestId(testId);
  if (!(await action.isVisible().catch(() => false))) return false;
  return action.isEnabled({ timeout: 250 }).catch(() => false);
}

async function submitSingleCleanupDiscard(
  pages: readonly Page[],
): Promise<boolean> {
  for (const page of pages) {
    if (!(await actionIsReady(page, "action-discard"))) continue;
    await page.getByTestId("action-discard").click();
    await expect(page.getByTestId("choice-dialog")).toBeVisible();
    await page.locator('[data-testid^="choice-cards-"]').first().check();
    await submitOpenChoice(page);
    return true;
  }
  return false;
}

async function driveToAction(
  pages: readonly Page[],
  page: Page,
  testId: string,
  testInfo: TestInfo,
) {
  await driveUntil(
    pages,
    () => actionIsReady(page, testId),
    testInfo,
    {
      label: `expose ${testId}`,
      noProgressMs: 90_000,
      advance: () => submitSingleCleanupDiscard(pages),
    },
  );
}

async function verifyExactReplay(page: Page) {
  const gameId = await page.locator(".game-shell").getAttribute("data-game-id");
  expect(gameId).toBeTruthy();
  const runtime = process.env.MTG_E2E_RUNTIME_RESOLVED;
  const cardDb = process.env.MTG_E2E_CARD_DB_RESOLVED;
  const python = process.env.MTG_E2E_PYTHON_RESOLVED;
  if (!runtime || !cardDb || !python) {
    throw new Error("Playwright did not publish its isolated replay paths");
  }
  const repository = path.resolve("..");
  const record = path.join(runtime, "games", gameId!);
  await expect
    .poll(
      () =>
        page.evaluate(async (id) => {
          const response = await fetch(`/api/v1/games/${id}/progress`, {
            credentials: "same-origin",
          });
          if (!response.ok) return null;
          const progress = (await response.json()) as {
            persistence?: { pending?: boolean };
          };
          return progress.persistence?.pending ?? null;
        }, gameId!),
      { message: "wait for the exact command journal to reach durable storage" },
    )
    .toBe(false);
  const { stdout } = await execFileAsync(
    python,
    [path.join(repository, "simctl.py"), "replay", record, "--db", cardDb],
    { cwd: repository, windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
  );
  const replay = JSON.parse(stdout.trim()) as {
    ok?: boolean;
    mode?: string;
    final_state_hash?: string;
    expected_state_hash?: string;
  };
  expect(replay.ok).toBe(true);
  expect(replay.mode).toBe("command_replay");
  expect(replay.final_state_hash).toBe(replay.expected_state_hash);
}

async function startDuel(browser: Browser) {
  const hostContext = await browser.newContext();
  const opponentContext = await browser.newContext();
  const host = await hostContext.newPage();
  const opponent = await opponentContext.newPage();
  await host.route(/\/api\/v1\/rooms$/, async (route) => {
    const request = route.request();
    const payload = request.postDataJSON() as Record<string, unknown>;
    await route.continue({
      postData: JSON.stringify({ ...payload, seed: 70122 }),
      headers: { ...request.headers(), "content-type": "application/json" },
    });
  });
  await enter(host, "Ordered Scry host");
  await enter(opponent, "Ordered Scry opponent");
  await host.getByTestId("room-size").selectOption("2");
  await host.getByTestId("create-room").click();
  const invite = await host.getByTestId("room-invite").textContent();
  expect(invite).toBeTruthy();
  await opponent.getByTestId("invite-code").fill(invite!);
  await opponent.getByTestId("seat-select").selectOption("B");
  await opponent.getByTestId("join-room").click();
  await submitDeck(host, "Ordered Scry", "Ordered Scry Witness", orderedScryDeck);
  await submitDeck(opponent, "Scry defender", "Yargle and Multani", defenderDeck);
  await startRoomGame(host);
  return { hostContext, opponentContext, host, opponent };
}

test("@browser-rules @scry @privacy @persistence ordered Scry is accessible, private, durable, and exactly replayable", async ({ browser }, testInfo) => {
  test.setTimeout(300_000);
  const { hostContext, opponentContext, host, opponent } = await startDuel(browser);
  const pages = [host, opponent] as const;
  try {
    await submitImmediateAction(host, "keep");
    await submitImmediateAction(opponent, "keep");

    const island = host.getByTestId("own-hand").locator(".hand-card").first();
    const islandRef = await island.getAttribute("data-card-ref");
    expect(islandRef).toBeTruthy();
    const playLand = `action-play-land:${islandRef}`;
    await driveToAction(pages, host, playLand, testInfo);
    await host.getByTestId(playLand).click();
    if (await host.getByTestId("choice-dialog").isVisible()) {
      await submitOpenChoice(host);
    }
    await expect(
      host.getByTestId("own-hand").locator(`[data-card-ref="${islandRef}"]`),
    ).toHaveCount(0);

    const commander = host
      .getByTestId("player-A")
      .locator(".command-zone .card-tile")
      .filter({ hasText: "Ordered Scry Witness" });
    await expect(commander).toHaveAttribute("draggable", "true");
    const cast = host
      .getByTestId("decision-panel")
      .getByRole("button", { name: /Cast Ordered Scry Witness/ });
    await driveUntil(pages, async () => cast.isEnabled().catch(() => false), testInfo, {
      label: "expose the Scry commander cast",
      noProgressMs: 90_000,
    });
    await cast.click();
    await expect(host.getByTestId("choice-dialog")).toContainText("Cast Ordered Scry Witness");
    await submitOpenChoice(host);

    await driveUntil(
      pages,
      () => actionIsReady(host, "action-choose"),
      testInfo,
      { label: "resolve the commander and expose its private Scry choice" },
    );
    const decisionId = await host
      .getByTestId("decision-panel")
      .getAttribute("data-decision-id");
    expect(decisionId).toBeTruthy();
    await expect(opponent.getByTestId("action-choose")).toHaveCount(0);

    await host.getByTestId("stop-reason").fill("Persist the pending ordered-Scry choice");
    await host.getByTestId("stop-game").click();
    await expect(host.getByTestId("game-status")).toHaveText("PAUSED");
    await expect(opponent.getByTestId("game-status")).toHaveText("PAUSED");
    await Promise.all([host.reload(), opponent.reload()]);
    await expect(host.getByTestId("resume-game")).toBeVisible();
    await host.getByTestId("resume-game").click();
    await expect(host.getByTestId("game-status")).toHaveText("ACTIVE");
    await expect(host.getByTestId("decision-panel")).toHaveAttribute(
      "data-decision-id",
      decisionId!,
    );

    await host.getByTestId("action-choose").click();
    const dialog = host.getByTestId("choice-dialog");
    await expect(dialog).toBeVisible();
    const destinations = dialog.locator('select[data-testid^="choice-cards-"]');
    await expect(destinations).toHaveCount(4);
    const refs: string[] = [];
    for (let index = 0; index < 4; index += 1) {
      const selector = destinations.nth(index);
      const testId = await selector.getAttribute("data-testid");
      expect(testId).toMatch(/^choice-cards-/);
      refs.push(testId!.slice("choice-cards-".length));
      await expect(selector).toHaveAccessibleName(
        new RegExp(`^Choose a destination for Island, looked-at card ${index + 1} of 4$`),
      );
    }
    const opponentHtml = await opponent.content();
    for (const ref of refs) expect(opponentHtml).not.toContain(ref);
    await expect(opponent.getByTestId("choice-dialog")).toHaveCount(0);

    await destinations.nth(2).selectOption("bottom");
    await destinations.nth(3).selectOption("bottom");
    const groups = dialog.locator(".choice-partition-group");
    const top = groups.filter({ hasText: "Top of library" });
    const bottom = groups.filter({ hasText: "Bottom of library" });
    await expect(top).toContainText("next card drawn");
    await expect(bottom).toContainText("nearest the top of this bottom group");

    const moveTop = top.getByRole("button", {
      name: "Move Island, item 2 of 2, earlier in Top of library",
    });
    await moveTop.focus();
    await host.keyboard.press("Enter");
    await bottom.getByRole("button", {
      name: "Move Island, item 2 of 2, earlier in Bottom of library",
    }).click();
    const orderedRefs = async (group: ReturnType<Page["locator"]>) =>
      group.locator("ol li").evaluateAll((rows) =>
        rows.map((row) => row.getAttribute("data-card-ref")),
      );
    expect(await orderedRefs(top)).toEqual([refs[1], refs[0]]);
    expect(await orderedRefs(bottom)).toEqual([refs[3], refs[2]]);

    const nextDrawRef = refs[1];
    await submitOpenChoice(host);
    for (const ref of refs) expect(await opponent.content()).not.toContain(ref);
    await verifyExactReplay(host);

    await host.reload();
    await driveUntil(
      pages,
      async () =>
        (await host
          .getByTestId("own-hand")
          .locator(`[data-card-ref="${nextDrawRef}"]`)
          .count()) === 1,
      testInfo,
      {
        label: "draw the exact card placed first in the Scry top group",
        noProgressMs: 90_000,
        advance: () => submitSingleCleanupDiscard(pages),
      },
    );
    await expect(
      host.getByTestId("own-hand").locator(`[data-card-ref="${nextDrawRef}"]`),
    ).toHaveCount(1);
    for (const ref of refs) expect(await opponent.content()).not.toContain(ref);
    await host.reload();
    await expect(
      host.getByTestId("own-hand").locator(`[data-card-ref="${nextDrawRef}"]`),
    ).toHaveCount(1);
  } finally {
    await annotateJourneyMetrics(pages, 2, testInfo);
    await Promise.all([hostContext.close(), opponentContext.close()]);
  }
});
