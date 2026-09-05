import { expect, test, type Browser, type BrowserContext, type Locator, type Page, type TestInfo } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import {
  annotateJourneyMetrics,
  currentDecisionId,
  driveUntil,
  submitAuthorizedPass,
  viewRevision,
} from "./support/progress";

const COLD_DECK_VALIDATION_TIMEOUT_MS = 90_000;

async function enter(page: Page, name: string) {
  await page.goto("/");
  await page.getByTestId("display-name").fill(name);
  await page.getByTestId("create-guest").click();
  await expect(page.getByRole("heading", { name: "Find your table" })).toBeVisible();
}

async function submitNamedDeck(page: Page, name: string, commander: string, text: string) {
  await page.getByTestId("deck-name").fill(name);
  await page.getByTestId("commander-name").fill(commander);
  await page.getByTestId("deck-list").fill(text);
  await page.getByTestId("submit-deck").click();
  // These duplicated lists exercise the browser protocol, not matchup or
  // semantic-coverage evidence. A draft mechanic contract may correctly keep
  // the ready list behind a visible fail-closed fidelity warning. The first
  // hosted validation also cold-populates compiler/preflight caches.
  await expect(
    page.locator(".success-banner, .warning-banner").filter({ hasText: /Deck (validated|accepted)/ }),
  ).toBeVisible({ timeout: COLD_DECK_VALIDATION_TIMEOUT_MS });
  await expect(page.getByTestId("deck-ready-summary")).toContainText(name);
}

async function submitDeck(page: Page, seat: string, text: string) {
  const zimone = seat === "A" || seat === "C";
  await submitNamedDeck(
    page,
    `Deck ${seat}`,
    zimone ? "Zimone and Dina" : "Mishra, Eminent One",
    text,
  );
}

async function expectCardSurface(page: Page, seat: string) {
  const firstCard = page.getByTestId("own-hand").locator(".hand-card").first();
  const name = await firstCard.locator(".card-copy strong").textContent();
  expect(name).toBeTruthy();
  const visibleFaceName = name!.split(" // ", 1)[0];
  await firstCard.hover();
  await expect(page.getByTestId("card-inspector")).toBeVisible();
  await expect(page.getByTestId("card-inspector")).toContainText(visibleFaceName);
  await expect(page.getByTestId(`zone-${seat}-graveyard`)).toBeDisabled();
  await expect(page.getByTestId(`zone-${seat}-exile`)).toBeDisabled();
}

async function startFourPlayerGame(browser: Browser): Promise<{ contexts: BrowserContext[]; pages: Page[]; invite: string }> {
  const contexts: BrowserContext[] = [];
  const pages: Page[] = [];
  for (const seat of "ABCD") {
    const context = await browser.newContext();
    contexts.push(context);
    const page = await context.newPage();
    pages.push(page);
    await enter(page, `Choices ${seat}`);
  }
  await pages[0].getByTestId("create-room").click();
  const invite = await pages[0].getByTestId("room-invite").textContent();
  expect(invite).toBeTruthy();
  for (let index = 1; index < 4; index += 1) {
    await pages[index].getByTestId("invite-code").fill(invite!);
    await pages[index].getByTestId("seat-select").selectOption("ABCD"[index]);
    await pages[index].getByTestId("join-room").click();
  }
  const zimone = await readFile(path.resolve("..", "examples", "zimone-and-dina.txt"), "utf8");
  const mishra = await readFile(path.resolve("..", "examples", "mishra-eminent-one.txt"), "utf8");
  for (let index = 0; index < 4; index += 1) {
    const seat = "ABCD"[index];
    await submitDeck(pages[index], seat, seat === "A" || seat === "C" ? zimone : mishra);
  }
  await expect(pages[0].getByTestId("start-game")).toBeEnabled();
  await pages[0].getByTestId("start-game").click();
  for (let index = 0; index < pages.length; index += 1) {
    const page = pages[index];
    await expect(page.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
    await expect(page.getByTestId("decision-panel")).toBeVisible();
    await expectCardSurface(page, "ABCD"[index]);
  }
  return { contexts, pages, invite: invite! };
}

async function submitImmediateAction(page: Page, actionId: string) {
  const revision = await viewRevision(page);
  await page.getByTestId(`action-${actionId}`).click();
  await expect.poll(() => viewRevision(page)).toBeGreaterThan(revision);
}

async function submitOpenChoice(page: Page, force = false) {
  const revision = await viewRevision(page);
  if (force) {
    await page.getByTestId("submit-choice").click({ force: true });
  } else {
    await page.getByTestId("submit-choice").click();
  }
  await expect.poll(() => viewRevision(page)).toBeGreaterThan(revision);
}

async function submitFormAction(page: Page, actionId: string, forceChoice = false) {
  await page.getByTestId(`action-${actionId}`).click();
  await expect(page.getByTestId("choice-dialog")).toBeVisible();
  await submitOpenChoice(page, forceChoice);
}

async function submitMaybeFormAction(
  page: Page,
  actionId: string,
  clickTimeout = 15_000,
  forceChoice = false,
) {
  const revision = await viewRevision(page);
  const dialog = page.getByTestId("choice-dialog");
  await page.getByTestId(`action-${actionId}`).click({ timeout: clickTimeout });
  await expect
    .poll(async () => (await dialog.isVisible()) || (await viewRevision(page)) > revision)
    .toBe(true);
  if (await dialog.isVisible()) {
    await submitOpenChoice(page, forceChoice);
  }
}

async function ensureFullControl(page: Page) {
  const toggle = page.getByTestId("auto-pass-toggle");
  if (await toggle.getAttribute("aria-pressed") === "true") {
    await toggle.click();
  }
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(toggle).toContainText("Hold every priority");
}

async function ensureAutoPass(page: Page) {
  const toggle = page.getByTestId("auto-pass-toggle");
  if (await toggle.getAttribute("aria-pressed") === "false") {
    await toggle.click();
  }
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(toggle).toContainText("Auto-pass enabled");
}

async function actionIsReady(action: Locator): Promise<boolean> {
  // Priority can advance between the visibility and enabled checks. A vanished
  // capability is a normal projection transition, not a test failure.
  if (!(await action.isVisible())) return false;
  return action.isEnabled({ timeout: 250 }).catch(() => false);
}

async function submitSingleCleanupDiscard(
  pages: readonly Page[],
): Promise<boolean> {
  for (const page of pages) {
    const discard = page.getByTestId("action-discard");
    if (!(await actionIsReady(discard))) continue;
    await discard.click();
    await expect(page.getByTestId("choice-dialog")).toBeVisible();
    await page.locator('[data-testid^="choice-cards-"]').first().check();
    await submitOpenChoice(page);
    return true;
  }
  return false;
}

async function advanceToDecision(
  pages: readonly Page[],
  decisionPage: Page,
  expectedText: string,
  testInfo: TestInfo,
  durabilityTimeout = 90_000,
) {
  const panel = decisionPage.getByTestId("decision-panel");
  await driveUntil(
    pages,
    async () => (await panel.textContent())?.includes(expectedText) ?? false,
    testInfo,
    {
      label: `advance to decision ${expectedText}`,
      noProgressMs: durabilityTimeout,
    },
  );
}

async function advanceOtherSeats(
  pages: readonly Page[],
  heldPage: Page,
): Promise<boolean> {
  for (const page of pages) {
    if (page === heldPage) continue;
    // Bind the pass to the decision observed before the click. A projection
    // transition must not turn this response into a pass from the held window.
    const decisionId = await currentDecisionId(page);
    if (!decisionId) continue;
    const result = await submitAuthorizedPass(page, decisionId);
    if (result !== "unavailable") return true;
  }
  // Returning true tells driveUntil not to fall back to an all-seat pass. The
  // held seat may already own the strategic decision while its surrounding
  // phase projection is still settling.
  await new Promise((resolve) => setTimeout(resolve, 200));
  return true;
}

async function submitEmptyAttackDeclaration(
  pages: readonly Page[],
): Promise<boolean> {
  for (const page of pages) {
    const decisionId = await currentDecisionId(page);
    if (!decisionId) continue;
    const panel = page.locator(
      `[data-testid="decision-panel"][data-decision-id="${decisionId}"]`,
    );
    const attack = panel.getByTestId("action-attack");
    if (!(await attack.isVisible().catch(() => false))) continue;
    if (!(await attack.isEnabled({ timeout: 250 }).catch(() => false))) continue;

    const revision = await viewRevision(page);
    try {
      await attack.click({ timeout: 2_000 });
      await page.getByTestId("choice-dialog").waitFor({
        state: "visible",
        timeout: 2_000,
      });
      await submitOpenChoice(page);
      return true;
    } catch (error) {
      if (
        (await viewRevision(page)) > revision
        || (await currentDecisionId(page)) !== decisionId
      ) {
        return true;
      }
      throw error;
    }
  }
  return false;
}

async function declineSeatOpportunity(
  pages: readonly Page[],
  seatPage: Page,
  opportunity: Locator,
  expectedActiveSeat: string,
  expectedStep: "Main Phase 1" | "Main Phase 2",
  testInfo: TestInfo,
  durabilityTimeout = 90_000,
) {
  const step = seatPage.getByTestId("exact-step-label");
  const shell = seatPage.locator(".game-shell");
  const expectedPhase = expectedStep === "Main Phase 1"
    ? "precombat_main"
    : "postcombat_main";
  const windowSnapshot = async () => shell.evaluate((element) => {
    const root = element as HTMLElement;
    return {
      activePlayer: root.dataset.activePlayer || "",
      phase: root.dataset.phase || "",
      step: root.querySelector<HTMLElement>('[data-testid="exact-step-label"]')
        ?.textContent || "",
    };
  });
  const atExpectedWindow = async () => {
    const snapshot = await windowSnapshot();
    return snapshot.activePlayer === expectedActiveSeat
      && snapshot.phase === expectedPhase
      && snapshot.step === expectedStep;
  };
  let exposedDecisionId = "";
  await driveUntil(
    pages,
    async () => {
      const decisionIdBefore = await currentDecisionId(seatPage);
      if (!decisionIdBefore || !(await atExpectedWindow())) return false;
      if (!(await actionIsReady(opportunity))) return false;
      if (!(await actionIsReady(seatPage.getByTestId("action-pass")))) return false;
      const decisionIdAfter = await currentDecisionId(seatPage);
      if (decisionIdAfter !== decisionIdBefore) return false;
      exposedDecisionId = decisionIdBefore;
      return true;
    },
    testInfo,
    {
      label: `expose ${expectedStep} seat opportunity`,
      noProgressMs: durabilityTimeout,
      advance: async () => {
        const snapshot = await windowSnapshot();
        if (
          snapshot.activePlayer !== expectedActiveSeat
          || snapshot.phase !== expectedPhase
        ) {
          // The same action can be offered in an earlier main phase. Let the
          // shared driver pass that exact authorized decision instead of
          // holding it merely because the action text matches our later goal.
          return false;
        }
        // Once the authoritative phase is correct, hold this seat while its
        // exact step label and decision controls settle, and advance only a
        // separately authorized response owned by another seat.
        return advanceOtherSeats(pages, seatPage);
      },
    },
  );
  expect(exposedDecisionId).not.toBe("");
  expect(await currentDecisionId(seatPage)).toBe(exposedDecisionId);
  expect(await atExpectedWindow()).toBe(true);
  expect(await step.textContent()).toBe(expectedStep);
  expect(await actionIsReady(opportunity)).toBe(true);
  const result = await submitAuthorizedPass(seatPage, exposedDecisionId);
  if (result !== "submitted") {
    throw new Error(`The intended seat pass ${result} after opportunity exposure`);
  }
}

async function passUntilProjection(
  pages: readonly Page[],
  projected: () => Promise<boolean>,
  testInfo: TestInfo,
  durabilityTimeout = 90_000,
) {
  await driveUntil(pages, projected, testInfo, {
    label: "wait for projected state",
    noProgressMs: durabilityTimeout,
  });
}

async function advanceToActionReady(
  pages: readonly Page[],
  action: Locator,
  actionPage: Page,
  testInfo: TestInfo,
  durabilityTimeout = 45_000,
  holdWindow?: () => Promise<boolean>,
) {
  // Stop advancing as soon as the exact capability exists, even if React still
  // has it disabled behind the previous command's serialization lock. A goal
  // based only on a card's draggable projection can otherwise submit the main-
  // phase pass while that projection is still settling.
  await driveUntil(pages, async () => actionIsReady(action), testInfo, {
    label: "advance to exact ready action",
    noProgressMs: durabilityTimeout,
    advance: async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
      // Snapshot before the window checks below. If a new strategic decision
      // arrives while those checks run, the old capability races closed.
      const observedDecisions = await Promise.all(
        pages.map((page) => currentDecisionId(page)),
      );
      // Auto-pass cannot answer the mandatory cleanup discard that follows a
      // turn where this durability witness retained eight cards. Resolve that
      // scripted single-card choice before searching for the next land offer.
      if (await submitSingleCleanupDiscard(pages)) return true;
      if (await action.count()) return advanceOtherSeats(pages, actionPage);
      if (holdWindow && await holdWindow()) {
        return advanceOtherSeats(pages, actionPage);
      }
      // A land search can cross a later active player's declare-attackers
      // decision. Declaring no attackers is the legal deterministic advance;
      // ordinary priority passing cannot answer this mandatory choice.
      if (await submitEmptyAttackDeclaration(pages)) return true;
      for (let index = 0; index < pages.length; index += 1) {
        const decisionId = observedDecisions[index];
        if (!decisionId) continue;
        const result = await submitAuthorizedPass(pages[index], decisionId);
        if (result !== "unavailable") return true;
      }
      return false;
    },
  });
}

const browserTriggerDeck = `Commander:
1 Mishra, Eminent One

Mainboard:
1 Sunscorched Desert
1 Orcish Bowmasters
1 Sol Ring
32 Island
32 Swamp
32 Mountain
`;

const browserResponseDeck = `Commander:
1 Zimone and Dina

Mainboard:
1 An Offer You Can't Refuse
33 Island
33 Swamp
32 Forest
`;

const browserCombatDeck = `Commander:
1 Zimone and Dina

Mainboard:
33 Island
33 Swamp
33 Forest
`;

const browserCombatDefenderDeck = `Commander:
1 Mishra, Eminent One

Mainboard:
33 Island
33 Swamp
33 Mountain
`;

// This intentionally duplicated vanilla-commander list is a deterministic
// lifecycle witness. It proves natural browser completion, never matchup
// strength or broader Oracle coverage.
const browserNaturalWinnerDeck = `Commander:
1 Yargle and Multani

Mainboard:
50 Swamp
49 Forest
`;

const browserSpireGardenDeck = `Commander:
1 Saskia the Unyielding

Mainboard:
1 Spire Garden
49 Forest
49 Mountain
`;

test("@browser-lifecycle four shared-cookie browser tabs retain isolated lobby seats", async ({ browser }, testInfo) => {
  const context = await browser.newContext();
  const pages: Page[] = [];
  try {
    for (const seat of "ABCD") {
      const page = await context.newPage();
      pages.push(page);
      await enter(page, `Smoke ${seat}`);
    }
    await pages[0].getByTestId("create-room").click();
    const invite = await pages[0].getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    for (let index = 1; index < pages.length; index += 1) {
      const seat = "ABCD"[index];
      await pages[index].getByTestId("invite-code").fill(invite!);
      await pages[index].getByTestId("seat-select").selectOption(seat);
      await pages[index].getByTestId("join-room").click();
    }
    for (let index = 0; index < pages.length; index += 1) {
      await expect(pages[index].getByTestId(`seat-${"ABCD"[index]}`)).toHaveClass(/mine/);
    }
    await expect(pages[0].getByTestId("room-invite")).toHaveText(invite!);
  } finally {
    await annotateJourneyMetrics(pages, 1, testInfo);
    await context.close();
  }
});

test("@smoke @browser-lifecycle @reconnect @lifecycle four shared-cookie browser tabs retain isolated seats through mulligans and reconnect", async ({ browser }, testInfo) => {
  // This journey cold-validates four full decks, persists stop/resume and four
  // mulligan declarations, reloads shared-cookie tabs, and records metrics.
  // Hosted filesystems can complete every assertion near the suite default.
  test.setTimeout(180_000);
  const contexts: BrowserContext[] = [];
  const pages: Page[] = [];
  try {
    // One context deliberately shares its cookie jar across all pages. The
    // application must still bind each tab to its own guest/seat session.
    const context = await browser.newContext();
    contexts.push(context);
    for (const seat of "ABCD") {
      const page = await context.newPage();
      pages.push(page);
      await enter(page, `Browser ${seat}`);
    }

    await pages[0].getByTestId("create-room").click();
    const invite = await pages[0].getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    for (let index = 1; index < 4; index += 1) {
      await pages[index].getByTestId("invite-code").fill(invite!);
      await pages[index].getByTestId("seat-select").selectOption("ABCD"[index]);
      await pages[index].getByTestId("join-room").click();
      await expect(pages[index].getByTestId(`seat-${"ABCD"[index]}`)).toContainText(`Browser ${"ABCD"[index]}`);
    }
    await pages[1].reload();
    await pages[2].reload();
    await expect(pages[1].getByTestId("seat-B")).toHaveClass(/mine/);
    await expect(pages[2].getByTestId("seat-C")).toHaveClass(/mine/);

    const zimone = await readFile(path.resolve("..", "examples", "zimone-and-dina.txt"), "utf8");
    const mishra = await readFile(path.resolve("..", "examples", "mishra-eminent-one.txt"), "utf8");
    for (let index = 0; index < 4; index += 1) {
      const seat = "ABCD"[index];
      await submitDeck(pages[index], seat, seat === "A" || seat === "C" ? zimone : mishra);
    }

    await expect(pages[0].getByTestId("room-invite")).toHaveText(invite!);
    await pages[0].getByTestId("replace-invite").click();
    await expect(pages[0].getByText("A new invite code was created.")).toBeVisible();
    const replacementInvite = await pages[0].getByTestId("room-invite").textContent();
    expect(replacementInvite).toBeTruthy();
    expect(replacementInvite).not.toEqual(invite);
    await pages[0].reload();
    await expect(pages[0].getByTestId("room-invite")).toHaveText(replacementInvite!);

    await pages[0].getByTestId("unready-deck").click();
    await expect(pages[0].getByTestId("submit-deck")).toBeVisible();
    await expect(pages[0].getByTestId("seat-A")).toContainText("WAITING");
    await expect(pages[0].getByTestId("start-game")).toBeDisabled();
    await submitDeck(pages[0], "A", zimone);

    await expect(pages[0].getByTestId("start-game")).toBeEnabled();
    await pages[0].getByTestId("start-game").click();
    for (let index = 0; index < pages.length; index += 1) {
      const page = pages[index];
      await expect(page.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
      await expect(page.getByTestId("decision-panel")).toBeVisible();
      await expectCardSurface(page, "ABCD"[index]);
      await ensureFullControl(page);
    }
    const handA = await pages[0].getByTestId("own-hand").textContent();
    const handB = await pages[1].getByTestId("own-hand").textContent();
    expect(handA).not.toEqual(handB);

    for (const page of pages) {
      await expect(page.getByTestId("game-status")).toHaveText("ACTIVE");
    }
    await expect(pages[0].getByTestId("stop-game")).toBeVisible();
    for (const member of pages.slice(1)) {
      await expect(member.getByTestId("stop-game")).toHaveCount(0);
    }
    await pages[1].getByTestId("inspect-game").click();
    await expect(pages[1].getByTestId("game-inspection")).toContainText("active");
    await pages[0].getByTestId("stop-reason").fill("Browser lifecycle regression");
    await pages[0].getByTestId("stop-game").click();
    for (const page of pages) {
      await expect(page.getByTestId("game-status")).toHaveText("PAUSED");
      await expect(page.getByTestId("paused-banner")).toContainText("Browser lifecycle regression");
    }
    for (const pausedSeat of pages) {
      await expect(pausedSeat.locator('[data-testid^="action-"]')).toHaveCount(0);
      await expect(pausedSeat.getByTestId("paused-decision")).toContainText(
        "No player action or priority pass is pending",
      );
    }
    await pages[1].reload();
    await expect(pages[1].getByText("LIVE", { exact: true })).toBeVisible();
    await expect(pages[1].getByTestId("game-status")).toHaveText("PAUSED");
    await expect(pages[0].getByTestId("resume-game")).toBeVisible();
    await pages[0].getByTestId("resume-game").click();
    for (const page of pages) {
      await expect(page.getByTestId("game-status")).toHaveText("ACTIVE");
      await expect(page.getByTestId("paused-banner")).toHaveCount(0);
    }

    for (let index = 0; index < 4; index += 1) {
      const revisions = await Promise.all(pages.map(viewRevision));
      await expect(pages[index].getByTestId("action-keep")).toBeVisible();
      if (index === 0) {
        let firstEnvelope: Record<string, unknown> | null = null;
        await pages[index].route("**/api/v1/games/*/commands", async (route) => {
          firstEnvelope = route.request().postDataJSON() as Record<string, unknown>;
          await route.abort("connectionfailed");
        }, { times: 1 });
        await pages[index].getByTestId("action-keep").click();
        await expect(pages[index].getByTestId("command-retry")).toBeVisible();
        const retriedRequest = pages[index].waitForRequest("**/api/v1/games/*/commands");
        await pages[index].getByRole("button", { name: "Retry exact command" }).click();
        const retriedEnvelope = (await retriedRequest).postDataJSON() as Record<string, unknown>;
        expect(retriedEnvelope.command_id).toEqual(firstEnvelope!.command_id);
        expect(retriedEnvelope).toEqual(firstEnvelope);
      } else {
        await pages[index].getByTestId("action-keep").click();
      }
      // A click only proves that the browser dispatched the command. Wait for
      // the authoritative HTTP receipt before allowing the next declaration
      // (or the reconnect below) to observe the resulting game state.
      await expect(pages[index].locator(".toast")).toContainText("Accepted keep");
      for (let seatIndex = 0; seatIndex < 4; seatIndex += 1) {
        await expect.poll(() => viewRevision(pages[seatIndex])).toBeGreaterThan(revisions[seatIndex]);
      }
    }

    // Depending on the seeded hands, the rules engine may pause for any
    // seat's meaningful upkeep response or skip pass-only windows. The
    // revision barriers above synchronize on the final declaration without
    // assuming a particular phase or priority holder.
    const projectedHandCount = await pages[0].getByTestId("own-hand").locator(".hand-card").count();
    const projectedDecision = await pages[0].getByTestId("decision-panel").textContent();
    await pages[0].reload();
    await expect(pages[0].getByText("LIVE", { exact: true })).toBeVisible();
    await expect(pages[0].getByTestId("own-hand").locator(".hand-card")).toHaveCount(projectedHandCount);
    await expect(pages[0].getByTestId("decision-panel")).toHaveText(projectedDecision!);
    await pages[0].setViewportSize({ width: 390, height: 844 });
    await expect(pages[0].getByTestId("decision-panel")).toBeVisible();
    await expect(pages[0].getByTestId("own-hand")).toBeVisible();
    const mobileViewer = pages[0].getByRole("button", { name: /^View / });
    await expect(mobileViewer).toBeVisible();
    await mobileViewer.click();
    await expect(pages[0].getByTestId("card-inspector-expanded")).toBeVisible();
    await pages[0].keyboard.press("Escape");
    await expect(pages[0].getByTestId("card-inspector-expanded")).toHaveCount(0);
    expect(await pages[0].evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  } finally {
    await annotateJourneyMetrics(pages, contexts.length, testInfo);
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-lifecycle an invited spectator receives a read-only projection and complete public log", async ({ browser }, testInfo) => {
  let playerContexts: BrowserContext[] = [];
  const metricsPages: Page[] = [];
  const spectatorContext = await browser.newContext();
  try {
    const started = await startFourPlayerGame(browser);
    playerContexts = started.contexts;
    const spectator = await spectatorContext.newPage();
    metricsPages.push(...started.pages, spectator);
    await enter(spectator, "Table spectator");
    await spectator.getByTestId("invite-code").fill(started.invite);
    await spectator.getByTestId("watch-room").click();

    await expect(spectator.getByTestId("watch-mode")).toBeVisible();
    await expect(spectator.locator(".player-board")).toHaveCount(4);
    await expect(spectator.getByTestId("own-hand")).toHaveCount(0);
    await expect(spectator.locator('[data-testid^="action-"]')).toHaveCount(0);
    await expect(spectator.getByTestId("decision-panel")).toContainText(
      "Watching the table",
    );

    await spectator.getByTestId("open-public-log").click();
    await expect(spectator.getByTestId("public-game-log")).toBeVisible();
    await expect(spectator.getByTestId("public-log-entry").first()).toBeVisible();
    const beforeLogCount = await spectator.getByTestId("public-log-entry").count();
    const beforeRevision = await viewRevision(spectator);

    await submitImmediateAction(started.pages[0], "keep");
    await expect.poll(() => viewRevision(spectator)).toBeGreaterThan(beforeRevision);
    await spectator.getByTestId("refresh-public-log").click();
    await expect.poll(async () => spectator.getByTestId("public-log-entry").count()).toBeGreaterThanOrEqual(beforeLogCount);

    await spectator.keyboard.press("Escape");
    await expect(spectator.getByTestId("public-game-log")).toHaveCount(0);
    await spectator.reload();
    await expect(spectator.getByTestId("watch-mode")).toBeVisible();
    await expect(spectator.getByTestId("own-hand")).toHaveCount(0);
    await spectator.getByTestId("open-public-log").click();
    await expect(spectator.getByTestId("public-log-entry").first()).toBeVisible();
  } finally {
    await annotateJourneyMetrics(
      metricsPages,
      playerContexts.length + 1,
      testInfo,
    );
    await spectatorContext.close();
    await Promise.all(playerContexts.map((context) => context.close()));
  }
});

test("@browser-lifecycle a shared-cookie 1v1 lobby can replace rooms, remove a player, and start a duel", async ({ browser }, testInfo) => {
  const context = await browser.newContext();
  const host = await context.newPage();
  const opponent = await context.newPage();
  host.setDefaultTimeout(15_000);
  opponent.setDefaultTimeout(15_000);
  try {
    await host.route(
      /\/api\/v1\/rooms(?:\/[^/]+\/replace)?$/,
      async (route) => {
        const request = route.request();
        const payload = request.postDataJSON() as Record<string, unknown>;
        await route.continue({
          postData: JSON.stringify({ ...payload, seed: 2 }),
          headers: {
            ...request.headers(),
            "content-type": "application/json",
          },
        });
      },
    );
    await enter(host, "Duel host");
    await enter(opponent, "Duel opponent");
    await host.getByTestId("room-size").selectOption("2");
    await host.getByTestId("create-room").click();
    await expect(host.getByTestId("seat-A")).toContainText("Duel host");
    await expect(host.getByTestId("seat-C")).toHaveCount(0);
    const staleInvite = await host.getByTestId("room-invite").textContent();
    expect(staleInvite).toBeTruthy();

    await opponent.getByTestId("invite-code").fill(staleInvite!);
    await opponent.getByTestId("seat-select").selectOption("B");
    await opponent.getByTestId("join-room").click();
    await expect(opponent.getByTestId("seat-B")).toContainText("Duel opponent");

    await host.getByTestId("new-room-size").selectOption("2");
    await host.getByTestId("new-room").click();
    await expect(host.getByTestId("room-invite")).not.toHaveText(staleInvite!);
    const invite = await host.getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    expect(invite).not.toEqual(staleInvite);
    await expect(opponent.getByRole("heading", { name: "Find your table" })).toBeVisible();

    await opponent.getByTestId("invite-code").fill(staleInvite!);
    await opponent.getByTestId("join-room").click();
    await expect(opponent.getByRole("alert")).toContainText("Invite code not found");
    await opponent.getByTestId("invite-code").fill(invite!);
    await opponent.getByTestId("join-room").click();
    await expect(opponent.getByTestId("seat-B")).toContainText("Duel opponent");

    await host.getByTestId("remove-seat-B").click();
    await expect(host.getByTestId("seat-B")).toContainText("Open seat");
    await expect(opponent.getByRole("heading", { name: "Find your table" })).toBeVisible();
    await opponent.getByTestId("invite-code").fill(invite!);
    await opponent.getByTestId("seat-select").selectOption("B");
    await opponent.getByTestId("join-room").click();

    const zimone = await readFile(path.resolve("..", "examples", "zimone-and-dina.txt"), "utf8");
    const mishra = await readFile(path.resolve("..", "examples", "mishra-eminent-one.txt"), "utf8");
    await submitDeck(host, "A", zimone);
    await submitDeck(opponent, "B", mishra);
    await expect(host.getByTestId("start-game")).toHaveText("Start duel");
    await expect(host.getByTestId("start-game")).toBeEnabled();
    await host.getByTestId("start-game").click();
    await expect(host.getByText("COMMANDER DUEL")).toBeVisible();
    await expect(opponent.getByText("COMMANDER DUEL")).toBeVisible();
    await expect(host.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
    await expect(opponent.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
    await expect(host.locator(".player-board")).toHaveCount(2);
    await expect(opponent.locator(".player-board")).toHaveCount(2);
    await expect(host.getByTestId("commander-damage-A")).toContainText("0");
    await expect(host.getByTestId("commander-damage-B")).toContainText("0");
    await expect(host.getByTestId("hand-panel")).toHaveAttribute("data-resizable", "true");
    expect(await host.getByTestId("hand-panel").evaluate((element) => {
      const style = getComputedStyle(element);
      return { position: style.position, resize: style.resize };
    })).toEqual({ position: "relative", resize: "vertical" });
    const dockBottomBefore = await host.getByTestId("table-bottom-dock").evaluate(
      (element) => Math.round(element.getBoundingClientRect().bottom),
    );
    await host.setViewportSize({ width: 1180, height: 760 });
    const viewportBottom = await host.evaluate(() => window.innerHeight);
    const dockBottomAfter = await host.getByTestId("table-bottom-dock").evaluate(
      (element) => Math.round(element.getBoundingClientRect().bottom),
    );
    expect(dockBottomAfter).toBe(viewportBottom - 8);
    expect(dockBottomAfter).not.toBe(dockBottomBefore);
    await expect(host.getByTestId("auto-pass-toggle")).toHaveAttribute("aria-pressed", "true");
    await expect(host.getByTestId("auto-mana-toggle")).toHaveAttribute("aria-pressed", "true");
    await host.getByTestId("auto-pass-toggle").click();
    await expect(host.getByTestId("auto-pass-toggle")).toContainText("Hold every priority");
    await expect(host.getByTestId("auto-pass-toggle")).toHaveAttribute("aria-pressed", "false");

    await submitImmediateAction(host, "keep");
    await submitImmediateAction(opponent, "keep");
    await expect(host.getByTestId("action-pass")).toBeVisible();
    await expect(host.getByTestId("decision-panel")).toContainText("Pass priority");
    await submitFormAction(host, "pass");
    const swamp = host
      .getByTestId("own-hand")
      .locator(".hand-card")
      .filter({ has: host.locator(".card-copy strong", { hasText: "Swamp" }) });
    await expect(swamp).toHaveCount(1);
    const swampRef = await swamp.getAttribute("data-card-ref");
    expect(swampRef).toBeTruthy();
    const battlefieldCards = host
      .getByTestId("own-battlefield")
      .locator(".card-tile");
    const battlefieldCount = await battlefieldCards.count();
    // The pass which reaches main phase can still publish after the land offer
    // appears. Submit the exact current capability and certify zone outcomes;
    // an unrelated delayed revision is not evidence that a drag succeeded.
    const playSwamp = host.getByTestId(`action-play-land:${swampRef}`);
    await advanceToActionReady([host, opponent], playSwamp, host, testInfo);
    await playSwamp.click();
    const dialog = host.getByTestId("choice-dialog");
    await expect.poll(async () =>
      (await dialog.isVisible())
      || (await battlefieldCards.count()) > battlefieldCount,
    ).toBe(true);
    if (await dialog.isVisible()) await submitOpenChoice(host);
    await expect(
      host.getByTestId("own-hand").locator(`[data-card-ref="${swampRef}"]`),
    ).toHaveCount(0);
    await expect(battlefieldCards).toHaveCount(battlefieldCount + 1);

    await host.getByTestId("action-concede").click();
    await expect(host.getByTestId("choice-dialog")).toContainText("Concede game");
    await expect(host.getByTestId("choice-confirm_concede")).toHaveValue("true");
    await host.getByTestId("cancel-choice").click();
    await expect(host.getByTestId("choice-dialog")).toHaveCount(0);
    await expect(host.getByTestId("game-status")).toHaveText("ACTIVE");

    await host.getByTestId("action-concede").click();
    await submitOpenChoice(host);
    for (const page of [host, opponent]) {
      await expect(page.getByTestId("game-status")).toHaveText("COMPLETE");
      await expect(page.getByTestId("game-over-banner")).toContainText("Seat B wins");
      await expect(page.getByTestId("turn-status-terminal")).toContainText("Game complete");
      await expect(page.getByTestId("decision-panel")).toHaveCount(0);
      await expect(page.locator('[data-testid^="action-"]')).toHaveCount(0);
    }
  } finally {
    await annotateJourneyMetrics([host, opponent], 1, testInfo);
    await context.close().catch(() => undefined);
  }
});

test("@browser-rules @turn-draw an isolated-context duel presents exact turn state and Spire Garden correctly", async ({ browser }, testInfo) => {
  test.setTimeout(180_000);
  const hostContext = await browser.newContext();
  const opponentContext = await browser.newContext();
  const host = await hostContext.newPage();
  const opponent = await opponentContext.newPage();
  await host.setViewportSize({ width: 1920, height: 1080 });
  await opponent.setViewportSize({ width: 1920, height: 1080 });
  try {
    await host.route(/\/api\/v1\/rooms$/, async (route) => {
      const request = route.request();
      const payload = request.postDataJSON() as Record<string, unknown>;
      await route.continue({
        postData: JSON.stringify({ ...payload, seed: 1 }),
        headers: { ...request.headers(), "content-type": "application/json" },
      });
    });
    await enter(host, "Turn-state host");
    await enter(opponent, "Turn-state opponent");
    await host.getByTestId("room-size").selectOption("2");
    await host.getByTestId("create-room").click();
    const invite = await host.getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    await opponent.getByTestId("invite-code").fill(invite!);
    await opponent.getByTestId("seat-select").selectOption("B");
    await opponent.getByTestId("join-room").click();
    await submitNamedDeck(host, "Spire Garden timing", "Saskia the Unyielding", browserSpireGardenDeck);
    await submitNamedDeck(opponent, "Turn-state defender", "Yargle and Multani", browserNaturalWinnerDeck);
    await host.getByTestId("start-game").click();

    await ensureFullControl(host);
    await ensureFullControl(opponent);
    await submitImmediateAction(host, "keep");
    await submitImmediateAction(opponent, "keep");

    for (const page of [host, opponent]) {
      await expect(page.getByTestId("active-turn-label")).toHaveText("Seat A's Turn · Turn 1");
      await expect(page.getByTestId("priority-label")).toContainText("Priority: Seat A");
      await expect(page.getByTestId("exact-step-label")).toHaveText("Upkeep");
      await expect(page.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
    }

    const spire = host
      .getByTestId("own-hand")
      .locator(".hand-card")
      .filter({ has: host.locator(".card-copy strong", { hasText: /^Spire Garden$/ }) });
    await expect(spire).toHaveCount(1);
    const spireRef = await spire.getAttribute("data-card-ref");
    expect(spireRef).toBeTruthy();
    const playSpire = host.getByTestId(`action-play-land:${spireRef}`);
    await spire.click();
    await expect(host.getByTestId("selected-card-actions")).toContainText(
      "Lands may be played only during your own main phase while the stack is empty",
    );

    await submitMaybeFormAction(host, "pass");
    for (const page of [host, opponent]) {
      await expect(page.getByTestId("active-turn-label")).toHaveText("Seat A's Turn · Turn 1");
      await expect(page.getByTestId("priority-label")).toContainText("Priority: Seat B");
      await expect(page.getByTestId("exact-step-label")).toHaveText("Upkeep");
    }

    const gameShell = host.locator(".game-shell");
    await advanceToActionReady(
      [host, opponent],
      playSpire,
      host,
      testInfo,
      45_000,
      async () =>
        (await gameShell.getAttribute("data-active-player")) === "A"
        && (await gameShell.getAttribute("data-phase")) === "precombat_main",
    );
    await expect(spire).toHaveAttribute("draggable", "true");
    await expect(host.getByTestId("active-turn-label")).toHaveText("Seat A's Turn · Turn 1");
    await expect(host.getByTestId("priority-label")).toContainText("Priority: Seat A");
    await expect(host.getByTestId("exact-step-label")).toHaveText("Main Phase 1");
    await expect(host.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);

    const sidebar = host.locator(".table-sidebar");
    await expect(sidebar.locator(":scope > section").first()).toHaveAttribute("data-testid", "stack-panel");
    const stackBounds = await host.getByTestId("stack-panel").boundingBox();
    expect(stackBounds).not.toBeNull();
    expect(stackBounds!.y + stackBounds!.height).toBeLessThan(1080);

    await host.getByTestId("table-settings").locator("summary").click();
    await host.getByLabel("Hand panel height").fill("360");
    await host.getByLabel("Right rail width").fill("420");
    await host.getByLabel("Right rail first").selectOption("activity");
    await host.getByLabel("Board density").selectOption("compact");
    await expect(host.locator(".game-shell")).toHaveClass(/density-compact/);
    await expect(sidebar.locator(":scope > section").first()).toHaveAttribute("data-testid", "activity-panel");

    await host.reload();
    await expect(host.locator(".game-shell")).toBeVisible();
    await expect(host.getByTestId("auto-pass-toggle")).toHaveAttribute("aria-pressed", "false");
    await expect(host.locator(".game-shell")).toHaveClass(/density-compact/);
    await expect(host.locator(".game-shell")).toHaveAttribute("style", /--right-rail-width: 420px/);
    await expect(host.locator(".table-sidebar > section").first()).toHaveAttribute("data-testid", "activity-panel");
    const dockBottom = await host.getByTestId("table-bottom-dock").evaluate(
      (element) => Math.round(element.getBoundingClientRect().bottom),
    );
    expect(dockBottom).toBe(1072);

    const beforeDrop = await viewRevision(host);
    await spire.dragTo(host.getByTestId("own-battlefield"));
    await expect.poll(() => viewRevision(host)).toBeGreaterThan(beforeDrop);
    const hostSpire = host
      .getByTestId("own-battlefield")
      .locator(".card-tile")
      .filter({ hasText: "Spire Garden" });
    const opponentSpire = opponent
      .getByTestId("player-A")
      .locator(".battlefield .card-tile")
      .filter({ hasText: "Spire Garden" });
    await expect(hostSpire).toHaveAttribute("data-tapped", "true");
    await expect(opponentSpire).toHaveAttribute("data-tapped", "true");
    await expect(host.getByTestId("priority-label")).toContainText("Priority: Seat A");
    await expect(host.getByTestId("action-pass")).toBeVisible();
    await expect(host.getByTestId("own-hand").locator(".hand-card")).toHaveCount(6);

    async function advanceUntilTurn(active: string, turn: number, step: string) {
      await driveUntil(
        [host, opponent],
        async () => {
        const activeText = await host.getByTestId("active-turn-label").textContent();
        const stepText = await host.getByTestId("exact-step-label").textContent();
          return activeText === `Seat ${active}'s Turn · Turn ${turn}`
            && stepText === step;
        },
        testInfo,
        {
          label: `advance to Seat ${active}, turn ${turn}, ${step}`,
          noProgressMs: 90_000,
          advance: () => submitSingleCleanupDiscard([host, opponent]),
        },
      );
    }

    await advanceUntilTurn("B", 2, "Draw");
    await expect(opponent.getByTestId("own-hand").locator(".hand-card")).toHaveCount(8);
    await expect(host.getByTestId("priority-label")).toContainText("Priority: Seat B");

    await advanceUntilTurn("A", 3, "Draw");
    await expect(host.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
  } finally {
    await annotateJourneyMetrics([host, opponent], 2, testInfo);
    await Promise.all([hostContext.close(), opponentContext.close()]);
  }
});

test("@browser-rules @mana-action a duel stabilizes land ETBs, permits a stack response, and resolves Bowmasters", async ({ browser }, testInfo) => {
  // This journey persists every manual mana and priority transition. Hosted
  // runners can exceed the suite default without any individual wait stalling.
  test.setTimeout(600_000);
  const hostContext = await browser.newContext();
  const opponentContext = await browser.newContext();
  const host = await hostContext.newPage();
  const opponent = await opponentContext.newPage();
  try {
    await host.route(/\/api\/v1\/rooms$/, async (route) => {
      const request = route.request();
      const payload = request.postDataJSON() as Record<string, unknown>;
      await route.continue({
        postData: JSON.stringify({ ...payload, seed: 42897 }),
        headers: { ...request.headers(), "content-type": "application/json" },
      });
    });
    await enter(host, "Trigger host");
    await enter(opponent, "Response opponent");
    await host.getByTestId("room-size").selectOption("2");
    await host.getByTestId("create-room").click();
    const invite = await host.getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    await opponent.getByTestId("invite-code").fill(invite!);
    await opponent.getByTestId("seat-select").selectOption("B");
    await opponent.getByTestId("join-room").click();

    await submitNamedDeck(host, "Trigger regression", "Mishra, Eminent One", browserTriggerDeck);
    await submitNamedDeck(opponent, "Response regression", "Zimone and Dina", browserResponseDeck);
    await host.getByTestId("start-game").click();
    await submitImmediateAction(host, "keep");
    await submitImmediateAction(opponent, "keep");

    const desert = host
      .getByTestId("own-hand")
      .locator(".hand-card")
      .filter({ has: host.locator(".card-copy strong", { hasText: "Sunscorched Desert" }) });
    await expect(desert).toHaveAttribute("draggable", "true");
    const beforeDesert = await viewRevision(host);
    await desert.dragTo(host.getByTestId("own-battlefield"));
    await expect.poll(() => viewRevision(host)).toBeGreaterThan(beforeDesert);
    await expect(host.getByTestId("decision-panel")).toContainText("Semantic.Target");
    await host.getByTestId("action-choose").click();
    await expect(host.getByTestId("choice-dialog")).toContainText("Seat B");
    await host.getByTestId("choice-target-B").check();
    await submitOpenChoice(host);
    await expect(host.getByTestId("player-B").getByLabel("39 life")).toBeVisible();
    await expect(opponent.getByTestId("player-B").getByLabel("39 life")).toBeVisible();

    async function playBasicLand(page: Page, name: "Island" | "Swamp") {
      const cards = page.getByTestId("own-hand").locator(".hand-card");
      const land = cards
        .filter({ has: page.locator(".card-copy strong", { hasText: new RegExp(`^${name}$`) }) })
        .first();
      const landRef = await land.getAttribute("data-card-ref");
      expect(landRef).toBeTruthy();
      const battlefieldCards = page
        .getByTestId("own-battlefield")
        .locator(".card-tile");
      const battlefieldCount = await battlefieldCards.count();
      const playAction = page.getByTestId(`action-play-land:${landRef}`);
      await advanceToActionReady([host, opponent], playAction, page, testInfo);
      await playAction.click();
      await expect(cards.locator(`[data-card-ref="${landRef}"]`)).toHaveCount(0);
      await expect(battlefieldCards).toHaveCount(battlefieldCount + 1);
    }

    await playBasicLand(opponent, "Island");
    await playBasicLand(host, "Swamp");

    await host.getByTestId("auto-mana-toggle").click();
    await expect(host.getByTestId("auto-mana-toggle")).toContainText("Manual mana on");
    const battlefieldSwamp = host
      .getByTestId("player-A")
      .locator(".battlefield .table-card")
      .filter({ has: host.locator(".card-copy strong", { hasText: /^Swamp$/ }) })
      .first();
    const beforeManualTap = await viewRevision(host);
    await battlefieldSwamp.click();
    await expect.poll(() => viewRevision(host)).toBeGreaterThan(beforeManualTap);
    await expect(battlefieldSwamp).toHaveAttribute("data-tapped", "true");
    await expect(host.getByTestId("player-A").locator(".zone-summary")).toContainText("B1");
    const beforeManualUndo = await viewRevision(host);
    await battlefieldSwamp.click();
    await expect.poll(() => viewRevision(host)).toBeGreaterThan(beforeManualUndo);
    await expect(battlefieldSwamp).toHaveAttribute("data-tapped", "false");
    await expect(host.getByTestId("player-A").locator(".zone-summary")).not.toContainText("B1");
    await host.getByTestId("auto-mana-toggle").click();
    await expect(host.getByTestId("auto-mana-toggle")).toContainText("Auto-mana on");

    const ring = host
      .getByTestId("own-hand")
      .locator(".hand-card")
      .filter({ has: host.locator(".card-copy strong", { hasText: "Sol Ring" }) });
    await expect(ring).toHaveAttribute("draggable", "true");
    await ring.dragTo(host.getByTestId("own-battlefield"));
    await expect(host.getByTestId("choice-dialog")).toContainText("Cast Sol Ring");
    await submitOpenChoice(host);
    await expect(opponent.locator(".stack-panel")).toContainText("Sol Ring");
    const hostTappedLand = host.getByTestId("player-A").locator(".battlefield .table-card.tapped");
    const opponentTappedLand = opponent.getByTestId("player-A").locator(".battlefield .table-card.tapped");
    await expect(hostTappedLand).toHaveCount(1);
    await expect(opponentTappedLand).toHaveCount(1);
    await expect(hostTappedLand).toHaveAttribute("data-tapped", "true");
    await expect(hostTappedLand.locator(".tapped-state")).toHaveText("TAPPED");
    await expect(opponentTappedLand.locator(".tapped-state")).toHaveText("TAPPED");
    expect(await hostTappedLand.evaluate((element) => getComputedStyle(element).transform)).not.toBe("none");

    const offer = opponent
      .getByTestId("own-hand")
      .locator(".hand-card")
      .filter({ has: opponent.locator(".card-copy strong", { hasText: "An Offer You Can't Refuse" }) });
    await expect(offer).toHaveAttribute("draggable", "true");
    await offer.dragTo(opponent.getByTestId("own-battlefield"));
    await expect(opponent.getByTestId("choice-dialog")).toContainText("Sol Ring");
    await opponent.locator('[data-testid^="choice-target-"]').first().check();
    await submitOpenChoice(opponent);

    const bowmasters = host
      .getByTestId("own-hand")
      .locator(".hand-card")
      .filter({ has: host.locator(".card-copy strong", { hasText: "Orcish Bowmasters" }) });
    await expect(bowmasters).toHaveAttribute("draggable", "true");
    await bowmasters.dragTo(host.getByTestId("own-battlefield"));
    await expect(host.getByTestId("choice-dialog")).toContainText("Cast Orcish Bowmasters");
    await submitOpenChoice(host);
    await driveUntil(
      [host, opponent],
      async () =>
        ((await host.getByTestId("decision-panel").textContent()) || "").includes(
          "Semantic.Target",
        ),
      testInfo,
      {
        label: "reach the Bowmasters target choice",
        noProgressMs: 90_000,
        overallMs: 300_000,
      },
    );
    await expect(host.getByTestId("decision-panel")).toContainText("Semantic.Target");
    await host.getByTestId("action-choose").click();
    await host.getByTestId("choice-target-B").check();
    await submitOpenChoice(host);

    await expect(host.getByTestId("player-B").getByLabel("38 life")).toBeVisible();
    await expect(host.getByTestId("own-battlefield")).toContainText("Orcish Bowmasters");
    await expect(host.getByTestId("own-battlefield")).toContainText("Army");
    await expect(host.getByTestId("game-status")).toHaveText("ACTIVE");
    await expect(host.getByTestId("paused-banner")).toHaveCount(0);
  } finally {
    await annotateJourneyMetrics([host, opponent], 2, testInfo);
    await Promise.all([hostContext.close(), opponentContext.close()]);
  }
});

test("@browser-rules @combat a duel declares an attacker in the browser and applies commander combat damage", async ({ browser }, testInfo) => {
  // This journey crosses several auto-pass windows and persists each real
  // command. Preserve assertion-driven waits while leaving hosted runners
  // enough time for durability writes and context cleanup under serial load.
  test.setTimeout(600_000);
  const hostContext = await browser.newContext();
  const opponentContext = await browser.newContext();
  const host = await hostContext.newPage();
  const opponent = await opponentContext.newPage();
  try {
    await host.route(/\/api\/v1\/rooms$/, async (route) => {
      const request = route.request();
      const payload = request.postDataJSON() as Record<string, unknown>;
      await route.continue({
        postData: JSON.stringify({ ...payload, seed: 1 }),
        headers: { ...request.headers(), "content-type": "application/json" },
      });
    });
    await enter(host, "Combat host");
    await enter(opponent, "Combat defender");
    await host.getByTestId("room-size").selectOption("2");
    await host.getByTestId("create-room").click();
    const invite = await host.getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    await opponent.getByTestId("invite-code").fill(invite!);
    await opponent.getByTestId("seat-select").selectOption("B");
    await opponent.getByTestId("join-room").click();
    await submitNamedDeck(host, "Combat attacker", "Zimone and Dina", browserCombatDeck);
    await submitNamedDeck(opponent, "Combat defender", "Mishra, Eminent One", browserCombatDefenderDeck);
    await host.getByTestId("start-game").click();
    await submitImmediateAction(host, "keep");
    await submitImmediateAction(opponent, "keep");

    async function playLand(page: Page, name?: string) {
      const cards = page.getByTestId("own-hand").locator(".hand-card");
      const land = name
        ? cards.filter({ has: page.locator(".card-copy strong", { hasText: new RegExp(`^${name}$`) }) }).first()
        : cards.first();
      const landRef = await land.getAttribute("data-card-ref");
      expect(landRef).toBeTruthy();
      const ownPlayerId = await page
        .locator('[data-testid^="player-"]')
        .filter({ has: page.getByTestId("own-battlefield") })
        .first()
        .getAttribute("data-testid");
      const ownSeat = ownPlayerId?.replace("player-", "") || null;
      expect(ownSeat).toBeTruthy();
      const battlefieldCards = page
        .getByTestId("own-battlefield")
        .locator(".card-tile");
      const battlefieldCount = await battlefieldCards.count();
      const playAction = page.getByTestId(`action-play-land:${landRef}`);
      await advanceToActionReady(
        [host, opponent],
        playAction,
        page,
        testInfo,
        90_000,
        async () => {
          const shell = page.locator(".game-shell");
          const [phase, activePlayer, hasDecision] = await Promise.all([
            shell.getAttribute("data-phase"),
            shell.getAttribute("data-active-player"),
            page.getByTestId("decision-panel").count(),
          ]);
          return (
            hasDecision > 0
            && activePlayer === ownSeat
            && (phase === "precombat_main" || phase === "postcombat_main")
          );
        },
      );
      await playAction.click();
      const dialog = page.getByTestId("choice-dialog");
      await expect.poll(async () =>
        (await dialog.isVisible())
        || (await battlefieldCards.count()) > battlefieldCount,
      ).toBe(true);
      if (await dialog.isVisible()) await submitOpenChoice(page);
      await expect(cards.locator(`[data-card-ref="${landRef}"]`)).toHaveCount(0, {
        timeout: 90_000,
      });
      await expect(battlefieldCards).toHaveCount(battlefieldCount + 1, {
        timeout: 90_000,
      });
    }

    await playLand(host, "Forest");
    await playLand(opponent);
    await playLand(host, "Swamp");
    await playLand(opponent);
    await playLand(host, "Island");

    const commander = host
      .getByTestId("player-A")
      .locator(".command-zone .card-tile")
      .filter({ has: host.locator(".card-copy strong", { hasText: "Zimone and Dina" }) });
    await expect(commander).toHaveAttribute("draggable", "true");
    // The anchored hand intentionally remains above the board while expanded.
    // Use the same cast capability in the always-visible decision tray after
    // verifying the command-zone surface is server-authorized and draggable.
    await host.getByTestId("decision-panel")
      .getByRole("button", { name: /Cast Zimone and Dina/ })
      .click();
    await expect(host.getByTestId("choice-dialog")).toContainText("Cast Zimone and Dina");
    await submitOpenChoice(host);
    await expect(host.getByTestId("own-battlefield")).toContainText("Zimone and Dina");

    await playLand(opponent);
    await playLand(host);

    await submitFormAction(host, "pass");
    await advanceToDecision(
      [host, opponent], host, "Combat.Attackers", testInfo,
    );
    await host.getByTestId("action-attack").click();
    const attackerChoice = host.locator('[data-testid^="choice-attackers-"]').first();
    await expect(attackerChoice).toBeVisible();
    await attackerChoice.selectOption("B");
    await submitOpenChoice(host);

    // Declaring no blockers is followed by multiple independently persisted
    // priority passes before combat damage. Wait for the projected result,
    // rather than assuming those durable transitions fit the suite-wide
    // assertion budget on a loaded filesystem.
    await passUntilProjection([host, opponent], async () => (
      await host.getByTestId("player-B").getByLabel("37 life").isVisible()
      && await opponent.getByTestId("player-B").getByLabel("37 life").isVisible()
    ), testInfo);
    await expect(host.getByTestId("player-B").getByLabel("37 life")).toBeVisible();
    await expect(opponent.getByTestId("player-B").getByLabel("37 life")).toBeVisible();
    await host.getByTestId("open-public-log").click();
    await expect(host.getByTestId("public-game-log")).toContainText("attacked with 1 creature");
    await expect(host.getByTestId("public-game-log")).toContainText("Combat damage was dealt");
  } finally {
    await annotateJourneyMetrics([host, opponent], 2, testInfo);
    await Promise.all([hostContext.close(), opponentContext.close()]);
  }
});

test("@browser-soak @natural-winner @persistence a trusted browser duel reaches a natural commander-damage winner", async ({ browser }, testInfo) => {
  // This intentionally natural game persists more than one hundred real
  // commands. It completes near five minutes alone and can take longer after
  // the preceding serial journeys, especially on Windows or hosted CI.
  test.setTimeout(1_800_000);
  const hostContext = await browser.newContext();
  const opponentContext = await browser.newContext();
  const host = await hostContext.newPage();
  const opponent = await opponentContext.newPage();
  const durableTransitionTimeout = 90_000;
  try {
    await host.route(/\/api\/v1\/rooms$/, async (route) => {
      const request = route.request();
      const payload = request.postDataJSON() as Record<string, unknown>;
      await route.continue({
        postData: JSON.stringify({ ...payload, seed: 1 }),
        headers: { ...request.headers(), "content-type": "application/json" },
      });
    });
    await enter(host, "Natural winner host");
    await enter(opponent, "Natural winner defender");
    await host.getByTestId("room-size").selectOption("2");
    await host.getByTestId("create-room").click();
    const invite = await host.getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    await opponent.getByTestId("invite-code").fill(invite!);
    await opponent.getByTestId("seat-select").selectOption("B");
    await opponent.getByTestId("join-room").click();
    await submitNamedDeck(host, "Natural winner A", "Yargle and Multani", browserNaturalWinnerDeck);
    await submitNamedDeck(opponent, "Natural winner B", "Yargle and Multani", browserNaturalWinnerDeck);
    await host.getByTestId("start-game").click();
    await submitImmediateAction(host, "keep");
    await submitImmediateAction(opponent, "keep");

    async function playLand(
      page: Page,
      name?: "Swamp" | "Forest",
      expectCommanderAlternative = false,
    ) {
      const cards = page.getByTestId("own-hand").locator(".hand-card");
      // This natural-winner witness runs after the other durability-heavy
      // journeys in its serial shard. A single accepted command can take more
      // than the shared helper's normal budget while prior records flush.
      // Advancing to this seat's main phase may include that turn's draw.
      const battlefieldCards = page
        .getByTestId("own-battlefield")
        .locator(".card-tile");
      const battlefieldCount = await battlefieldCards.count();
      // This durability soak exercises more than a hundred routine commands.
      // Resolve the physical card from the exact current capability after
      // advancing. An intervening mandatory cleanup discard may legally remove
      // the hand's former first card, so a reference captured before the
      // destination main phase can become stale even though other lands remain.
      const playAction = name
        ? page.getByTestId("decision-panel")
            .getByRole("button", { name: new RegExp(`^Play ${name}$`) })
            .first()
        : page.locator('[data-testid^="action-play-land:"]').first();
      const expectedActiveSeat = page === host ? "A" : "B";
      const table = page.locator(".game-shell");
      await advanceToActionReady(
        [host, opponent], playAction, page, testInfo, durableTransitionTimeout,
        async () => (
          (await table.getAttribute("data-active-player")) === expectedActiveSeat
          && (await table.getAttribute("data-phase")) === "precombat_main"
        ),
      );
      const actionTestId = await playAction.getAttribute("data-testid");
      expect(actionTestId).toMatch(/^action-play-land:[A-Z][0-9]+$/);
      if (expectCommanderAlternative) {
        // The server may advertise the land and commander in one strategic
        // decision. Choosing the land does not require a redundant task with
        // the same meaningful-action signature, so certify the alternative
        // against this exact capability before submitting the land action.
        const decisionIdBefore = await currentDecisionId(page);
        expect(decisionIdBefore).not.toBe("");
        const commanderOffer = page.getByTestId("decision-panel")
          .getByRole("button", { name: /Cast Yargle and Multani/ });
        expect(await actionIsReady(commanderOffer)).toBe(true);
        expect(await currentDecisionId(page)).toBe(decisionIdBefore);
      }
      const landRef = actionTestId!.slice("action-play-land:".length);
      const land = page
        .getByTestId("own-hand")
        .locator(`.hand-card[data-card-ref="${landRef}"]`);
      await expect(land).toHaveCount(1);
      const landName = await land.locator(".card-copy strong").textContent();
      expect(landName).toBeTruthy();
      await playAction.click();
      const dialog = page.getByTestId("choice-dialog");
      await expect.poll(async () =>
        (await dialog.isVisible())
        || (await battlefieldCards.count()) > battlefieldCount,
      ).toBe(true);
      if (await dialog.isVisible()) await submitOpenChoice(page);
      await expect(
        cards.locator(`[data-card-ref="${landRef}"]`),
      ).toHaveCount(0, {
        timeout: durableTransitionTimeout,
      });
      // Hidden-to-public projection intentionally replaces the private hand
      // reference, so certify the public battlefield count rather than
      // assuming identity continuity across that privacy boundary.
      await expect(battlefieldCards).toHaveCount(battlefieldCount + 1, {
        timeout: durableTransitionTimeout,
      });
    }

    async function declineCommanderDevelopment(page: Page) {
      // Once six mana is available, commander casting remains meaningful in
      // both main phases. Auto-pass must stop; this scripted witness declines
      // those two verified opportunities explicitly.
      // Wait specifically for this seat's commander offer before counting the
      // decline. Intervening response-window passes may belong to either seat
      // and must advance without being mistaken for a main-phase decline.
      const commanderOffer = page.getByTestId("decision-panel")
        .getByRole("button", { name: /Cast Yargle and Multani/ });
      await declineSeatOpportunity(
        [host, opponent], page, commanderOffer, "B", "Main Phase 1",
        testInfo,
        durableTransitionTimeout,
      );
      await declineSeatOpportunity(
        [host, opponent], page, commanderOffer, "B", "Main Phase 2",
        testInfo,
        durableTransitionTimeout,
      );
    }

    async function declineCommanderPostcombat(page: Page) {
      const commanderOffer = page.getByTestId("decision-panel")
        .getByRole("button", { name: /Cast Yargle and Multani/ });
      await declineSeatOpportunity(
        [host, opponent], page, commanderOffer, "B", "Main Phase 2",
        testInfo,
        durableTransitionTimeout,
      );
    }

    const requiredMana: Array<"Swamp" | "Forest"> = [
      "Swamp", "Swamp", "Forest", "Forest", "Swamp", "Forest",
    ];
    for (let turn = 0; turn < requiredMana.length; turn += 1) {
      if (turn === requiredMana.length - 1) {
        // Arm the defender's stop before the host's final development can
        // finish and Auto-pass both seats into the defender's turn. The
        // projection helper below advances the intervening stack response
        // while preserving Full Control for the upcoming main phase.
        await ensureFullControl(opponent);
      }
      await playLand(host, requiredMana[turn]);
      if (turn === requiredMana.length - 1) {
        const commander = host
          .getByTestId("player-A")
          .locator(".command-zone .card-tile")
          .filter({ has: host.locator(".card-copy strong", { hasText: "Yargle and Multani" }) });
        await expect(commander).toHaveAttribute("draggable", "true");
        await host.getByTestId("decision-panel")
          .getByRole("button", { name: /Cast Yargle and Multani/ })
          .click();
        await expect(host.getByTestId("choice-dialog")).toContainText("Cast Yargle and Multani");
        await submitOpenChoice(host);
        // The accepted cast still crosses stack priority and durability writes
        // before the permanent appears in the projected battlefield.
        await passUntilProjection([host, opponent], async () => (
          (await host.getByTestId("own-battlefield").textContent())
            ?.includes("Yargle and Multani") ?? false
        ), testInfo, durableTransitionTimeout);
      }
      await playLand(opponent);
      if (turn === requiredMana.length - 1) {
        await declineCommanderDevelopment(opponent);
        // Full Control was needed only to preserve the two strategic commander
        // offers. Return this seat to safe pass-only automation before combat
        // creates routine response windows.
        await ensureAutoPass(opponent);
      }
    }

    async function attackWithCommander() {
      // A still has a legal land play, so advancing to combat is meaningful
      // and must not be consumed by Auto-pass.
      // Depending on the exact priority handoff, either seat may briefly own a
      // server-issued pass before A receives the attackers decision. Submit
      // only that currently authorized pass and stop at the first strategic
      // combat choice.
      await advanceToDecision(
        [host, opponent], host, "Combat.Attackers", testInfo,
        durableTransitionTimeout,
      );
      await host.getByTestId("action-attack").click();
      const attackerChoice = host.locator('[data-testid^="choice-attackers-"]').first();
      await expect(attackerChoice).toBeVisible();
      await attackerChoice.selectOption("B");
      await submitOpenChoice(host);
    }

    await attackWithCommander();
    await passUntilProjection([host, opponent], async () => {
      for (const page of [host, opponent]) {
        if (!(await page.getByTestId("player-B").getByLabel("22 life").isVisible())) {
          return false;
        }
        const damage = await page.getByTestId("commander-damage-B").textContent();
        if (!damage?.includes("18 from Yargle and Multani")) return false;
      }
      return true;
    }, testInfo, durableTransitionTimeout);
    for (const page of [host, opponent]) {
      await expect(page.getByTestId("player-B").getByLabel("22 life")).toBeVisible({
        timeout: durableTransitionTimeout,
      });
      await expect(page.getByTestId("commander-damage-B")).toContainText(
        "18 from Yargle and Multani",
        { timeout: durableTransitionTimeout },
      );
    }
    await ensureFullControl(opponent);
    // Arm the defender before this pass can end the host's turn; toggling
    // afterward races the automatic transition through the next precombat
    // main phase. Damage can become visible before durability serialization
    // re-enables this pass, so wait for the exact capability instead of
    // starting an unbounded click against its disabled projection.
    const turnEndingPass = host.getByTestId("action-pass");
    await advanceToActionReady(
      [host, opponent], turnEndingPass, host, testInfo, durableTransitionTimeout,
    );
    // Auto-pass may win the narrow race after readiness is observed. That is
    // already the desired transition: the following exact Seat B land offer,
    // protected by Full Control, proves the turn advanced without skipping B.
    await submitAuthorizedPass(host);
    // B already has enough mana before this land play. Its exact precombat
    // decision offers both the land and commander, so prove the alternative
    // there instead of waiting for a duplicate post-land decision. Main Phase
    // 2 remains a distinct strategic window and is declined explicitly.
    await playLand(opponent, undefined, true);
    await declineCommanderPostcombat(opponent);
    await ensureAutoPass(opponent);

    await attackWithCommander();
    await passUntilProjection([host, opponent], async () => {
      for (const page of [host, opponent]) {
        if (await page.getByTestId("game-status").textContent() !== "COMPLETE") {
          return false;
        }
      }
      return true;
    }, testInfo, durableTransitionTimeout);
    for (const page of [host, opponent]) {
      await expect(page.getByTestId("game-status")).toHaveText("COMPLETE", {
        timeout: durableTransitionTimeout,
      });
      await expect(page.getByTestId("game-over-banner")).toContainText(
        "Seat A wins",
        { timeout: durableTransitionTimeout },
      );
      await expect(page.getByTestId("turn-status-terminal")).toContainText(
        "Game complete",
        { timeout: durableTransitionTimeout },
      );
      await expect(page.getByTestId("decision-panel")).toHaveCount(0, {
        timeout: durableTransitionTimeout,
      });
      await expect(page.locator('[data-testid^="action-"]')).toHaveCount(0, {
        timeout: durableTransitionTimeout,
      });
    }
    await host.getByTestId("open-public-log").click();
    await expect(host.getByTestId("public-game-log")).toContainText(
      "B left the game: state-based loss",
      { timeout: durableTransitionTimeout },
    );
    await expect(host.getByTestId("public-game-log")).toContainText(
      "A won the game",
      { timeout: durableTransitionTimeout },
    );
  } finally {
    await annotateJourneyMetrics([host, opponent], 2, testInfo);
    await Promise.all([hostContext.close(), opponentContext.close()]);
  }
});

test("@browser-lifecycle generic private choice form executes a penalized multiplayer mulligan", async ({ browser }, testInfo) => {
  let contexts: BrowserContext[] = [];
  let metricsPages: Page[] = [];
  try {
    const started = await startFourPlayerGame(browser);
    contexts = started.contexts;
    const pages = started.pages;
    metricsPages = pages;

    const mulliganTrigger = pages[0].getByTestId("action-mulligan");
    await mulliganTrigger.click();
    await expect(pages[0].getByTestId("choice-dialog")).toBeVisible();
    await pages[0].keyboard.press("Escape");
    await expect(pages[0].getByTestId("choice-dialog")).toHaveCount(0);
    await expect(mulliganTrigger).toBeFocused();
    await mulliganTrigger.click();
    await expect(pages[0].getByTestId("choice-dialog")).toBeVisible();
    await pages[0].getByTestId("choice-override_reason").fill("Browser choice-form coverage");
    await submitOpenChoice(pages[0]);

    for (let index = 1; index < 4; index += 1) {
      await expect(pages[index].getByTestId("action-keep")).toBeVisible();
      await submitImmediateAction(pages[index], "keep");
    }

    await expect(pages[0].getByTestId("action-mulligan")).toBeVisible();
    await pages[0].getByTestId("action-mulligan").click();
    await pages[0].getByTestId("choice-override_reason").fill("Deterministic browser regression coverage");
    await submitOpenChoice(pages[0]);

    await expect(pages[0].getByTestId("action-bottom")).toBeVisible();
    await pages[0].getByTestId("action-bottom").click();
    await expect(pages[0].getByTestId("choice-dialog")).toBeVisible();
    const firstCard = pages[0].locator('[data-testid^="choice-cards-"]').first();
    const testId = await firstCard.getAttribute("data-testid");
    expect(testId).toBeTruthy();
    await firstCard.check();
    for (const opponent of pages.slice(1)) {
      expect((await opponent.content()).includes(testId!)).toBeFalsy();
      await expect(opponent.getByTestId("choice-dialog")).toHaveCount(0);
    }
    await submitOpenChoice(pages[0]);

    await expect(pages[0].getByTestId("action-keep")).toBeVisible();
    await expect(pages[0].getByTestId("own-hand").locator(".hand-card")).toHaveCount(6);
    await submitImmediateAction(pages[0], "keep");
    // The engine may stop at a meaningful upkeep window or advance through
    // the opening draw, depending on the random hand. Either way, the bottom
    // operation above was observed authoritatively at six cards.
    expect([6, 7]).toContain(
      await pages[0].getByTestId("own-hand").locator(".hand-card").count(),
    );
    for (const opponent of pages.slice(1)) {
      await expect(opponent.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
    }
  } finally {
    await annotateJourneyMetrics(
      metricsPages,
      contexts.length,
      testInfo,
    );
    await Promise.all(contexts.map((context) => context.close()));
  }
});
