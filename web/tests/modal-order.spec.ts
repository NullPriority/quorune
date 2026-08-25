import { expect, test, type Browser, type Page, type TestInfo } from "@playwright/test";
import {
  annotateJourneyMetrics,
  driveUntil,
  viewRevision,
} from "./support/progress";

const modalDeck = `Commander:
1 Modal Choice Witness

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
  ).toBeVisible({ timeout: 90_000 });
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

async function startDuel(browser: Browser) {
  const hostContext = await browser.newContext();
  const opponentContext = await browser.newContext();
  const host = await hostContext.newPage();
  const opponent = await opponentContext.newPage();
  await host.route(/\/api\/v1\/rooms$/, async (route) => {
    const request = route.request();
    const payload = request.postDataJSON() as Record<string, unknown>;
    await route.continue({
      postData: JSON.stringify({ ...payload, seed: 620317 }),
      headers: { ...request.headers(), "content-type": "application/json" },
    });
  });
  await enter(host, "Modal host");
  await enter(opponent, "Modal opponent");
  await host.getByTestId("room-size").selectOption("2");
  await host.getByTestId("create-room").click();
  const invite = await host.getByTestId("room-invite").textContent();
  expect(invite).toBeTruthy();
  await opponent.getByTestId("invite-code").fill(invite!);
  await opponent.getByTestId("seat-select").selectOption("B");
  await opponent.getByTestId("join-room").click();
  await submitDeck(host, "Modal order", "Modal Choice Witness", modalDeck);
  await submitDeck(opponent, "Modal defender", "Yargle and Multani", defenderDeck);
  await host.getByTestId("start-game").click();
  return { hostContext, opponentContext, host, opponent };
}

test("@browser-rules @modal modal clicks preserve targets and submit printed order", async ({ browser }, testInfo) => {
  test.setTimeout(240_000);
  const { hostContext, opponentContext, host, opponent } = await startDuel(browser);
  const pages = [host, opponent] as const;
  try {
    await submitImmediateAction(host, "keep");
    await submitImmediateAction(opponent, "keep");

    const commander = host
      .getByTestId("player-A")
      .locator(".command-zone .card-tile")
      .filter({ hasText: "Modal Choice Witness" });
    const commanderRef = await commander.getAttribute("data-card-ref");
    expect(commanderRef).toBeTruthy();
    const castId = `action-cast:${commanderRef}`;
    await driveUntil(
      pages,
      () => actionIsReady(host, castId),
      testInfo,
      { label: "expose the zero-mana modal commander cast" },
    );
    await host.getByTestId(castId).click();
    await expect(host.getByTestId("choice-dialog")).toBeVisible();
    await submitOpenChoice(host);
    await driveUntil(
      pages,
      async () =>
        (await host
          .getByTestId("own-battlefield")
          .locator(`[data-card-ref="${commanderRef}"]`)
          .count()) === 1,
      testInfo,
      { label: "resolve the modal commander" },
    );

    const activationId = `action-activate:${commanderRef}:ab1`;
    await driveUntil(
      pages,
      () => actionIsReady(host, activationId),
      testInfo,
      { label: "expose the modal activation" },
    );
    let submittedChoices: Record<string, unknown> | null = null;
    await host.route(/\/api\/v1\/games\/[^/]+\/commands$/, async (route) => {
      const payload = route.request().postDataJSON() as {
        action_id?: string;
        choices?: Record<string, unknown>;
      };
      if (payload.action_id?.startsWith("activate:")) {
        submittedChoices = payload.choices ?? null;
      }
      await route.continue();
    });
    await host.getByTestId(activationId).click();
    const dialog = host.getByTestId("choice-dialog");
    await expect(dialog).toBeVisible();

    await host.getByTestId("choice-mode-mode_3").check();
    const modeThreeTarget = dialog
      .locator(".target-group")
      .filter({ hasText: "mode_3_target_1" })
      .getByRole("checkbox", { name: "Seat B" });
    await modeThreeTarget.check();
    await host.getByTestId("choice-mode-mode_1").check();
    await expect(modeThreeTarget).toBeChecked();
    await dialog
      .locator(".target-group")
      .filter({ hasText: "mode_1_target_1" })
      .getByRole("checkbox", { name: "Seat A" })
      .check();
    await submitOpenChoice(host);

    expect(submittedChoices).toEqual({
      modes: ["mode_1", "mode_3"],
      targets: {
        mode_3_target_1: ["B"],
        mode_1_target_1: ["A"],
      },
    });
    await driveUntil(
      pages,
      async () =>
        (await host.getByTestId("player-A").getByLabel("42 life").count()) === 1
        && (await host.getByTestId("player-B").getByLabel("38 life").count()) === 1,
      testInfo,
      { label: "resolve both canonical modal effects" },
    );
  } finally {
    await annotateJourneyMetrics(pages, 2, testInfo);
    await Promise.all([
      hostContext.close().catch(() => undefined),
      opponentContext.close().catch(() => undefined),
    ]);
  }
});
