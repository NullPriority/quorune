import { expect, type Browser, type BrowserContext, type Locator, type Page, type TestInfo } from "@playwright/test";
import { driveUntil, viewRevision } from "./progress";
import { startRoomGame } from "./startup";

export type PlaytestDeck = { commander: string; cards: string };

export async function clickMove(page: Page, control: Locator) {
  const revision = await viewRevision(page);
  await control.click();
  await expect.poll(() => viewRevision(page)).toBeGreaterThan(revision);
}

export async function startPlaytestPod(
  browser: Browser, decks: readonly PlaytestDeck[], seed: number,
  viewport = { width: 1366, height: 650 },
  hostContext?: BrowserContext,
  onPage?: (page: Page) => void | Promise<void>,
): Promise<{ contexts: BrowserContext[]; pages: Page[] }> {
  const contexts = await Promise.all(decks.map((_, index) => index === 0 && hostContext
    ? hostContext : browser.newContext({ viewport })));
  try {
    const pages = await Promise.all(contexts.map((context) => context.pages()[0] ?? context.newPage()));
    if (onPage) await Promise.all(pages.map(onPage));
    const [host, ...opponents] = pages;
    await host.route(/\/api\/v1\/rooms$/, async (route) => {
      const request = route.request();
      await route.continue({
        postData: JSON.stringify({ ...request.postDataJSON(), seed }),
        headers: { ...request.headers(), "content-type": "application/json" },
      });
    });
    await Promise.all(pages.map(async (page, index) => {
      await page.goto("/");
      await page.getByTestId("display-name").fill(`Playtester ${"ABCD"[index]}`);
      await page.getByTestId("create-guest").click();
      await expect(page.getByRole("heading", { name: "Find your table" })).toBeVisible();
    }));
    await host.getByTestId("room-size").selectOption(String(decks.length));
    await host.getByTestId("create-room").click();
    const invite = await host.getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    await Promise.all(opponents.map(async (page, index) => {
      await page.getByTestId("invite-code").fill(invite!);
      await page.getByTestId("seat-select").selectOption("BCD"[index]);
      await page.getByTestId("join-room").click();
    }));
    await Promise.all(pages.map(async (page, index) => {
      const { commander, cards } = decks[index];
      await page.getByTestId("deck-name").fill(`Playtest deck ${"ABCD"[index]}`);
      await page.getByTestId("commander-name").fill(commander);
      await page.getByTestId("deck-list").fill(`Commander:\n1 ${commander}\n\nMainboard:\n${cards}\n`);
      await page.getByTestId("submit-deck").click();
      await expect(page.locator(".success-banner, .warning-banner")
        .filter({ hasText: /Deck (validated|accepted)/ })).toBeVisible({ timeout: 60_000 });
    }));
    await startRoomGame(host);
    for (const page of pages) await clickMove(page, page.getByTestId("action-keep"));
    return { contexts, pages };
  } catch (error) {
    await Promise.all(contexts.map((context) => context.close()));
    throw error;
  }
}

export function castOffer(page: Page, name: string) {
  return page.getByTestId("decision-panel").locator('[data-testid^="action-cast:"]').filter({ hasText: name }).first();
}

export function boardCard(page: Page, name: string) {
  return page.getByTestId("own-battlefield").locator(".card-tile").filter({
    has: page.getByText(name, { exact: true }),
  });
}

export async function submitSingleCleanupDiscard(pages: readonly Page[]): Promise<boolean> {
  for (const page of pages) {
    const form = page.locator('[data-testid="decision-panel"][data-decision-kind="cleanup.discard"]')
      .getByTestId("contextual-decision");
    const submit = form.getByTestId("submit-choice");
    if (!(await submit.isVisible()) || !(await submit.isEnabled({ timeout: 250 }).catch(() => false))) continue;
    await form.locator('[data-testid^="choice-cards-"]').first().click();
    await clickMove(page, submit);
    return true;
  }
  return false;
}

export async function driveTo(pages: readonly Page[], target: Locator, testInfo: TestInfo, label: string) {
  await driveUntil(pages, async () => await target.isVisible() && await target.isEnabled().catch(() => false), testInfo, {
    label,
    overallMs: 180_000,
    noProgressMs: 30_000,
    advance: () => submitSingleCleanupDiscard(pages),
  });
}

export async function evidence(page: Page, testInfo: TestInfo, scenario: string) {
  const viewport = page.viewportSize();
  const label = `${scenario}-${viewport?.width}x${viewport?.height}`;
  const screenshot = testInfo.outputPath(`${label}.png`);
  await page.screenshot({ path: screenshot });
  await testInfo.attach(label, { path: screenshot, contentType: "image/png" });
}
