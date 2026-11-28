import { expect, test } from "@playwright/test";
import { boardCard, castOffer, clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";

test("@browser-rules a finite loop uses fresh capabilities and can hold priority afterward", async ({ browser }, info) => {
  const deck = { commander: "Yargle and Multani", cards: "99 Forest" };
  const { pages, contexts } = await startPlaytestPod(browser, [deck, deck], 13509);
  const host = pages[0];
  try {
    const play = host.getByTestId("own-hand").getByRole("button", { name: "Play Forest", exact: true }).first();
    await driveTo(pages, play, info, "Offer the first Forest");
    await clickMove(host, play);
    await host.getByTestId("auto-mana-toggle").click();

    const loop = host.locator(".loop-controls");
    await loop.locator("summary").click();
    const forest = boardCard(host, "Forest");
    const undo = host.locator(".mana-help").getByRole("button", { name: "Undo mana from Forest", exact: true });
    async function record() {
      await loop.getByRole("button", { name: "Record loop", exact: true }).click();
      await clickMove(host, forest);
      await expect(forest).toHaveAttribute("data-tapped", "true");
      await clickMove(host, undo);
      await expect(forest).toHaveAttribute("data-tapped", "false");
      await expect(loop.locator("summary")).toContainText("Ready");
      await loop.locator("summary").click();
      await expect(loop.getByText("Each player keeps their usual response windows.", { exact: false })).toBeVisible();
    }

    await record();
    const shell = host.locator(".game-shell");
    const immediate = Number(await shell.getAttribute("data-command-count"));
    await loop.getByRole("button", { name: "Hold priority now", exact: true }).click();
    await expect(loop.getByText("Holding priority", { exact: true })).toBeVisible();
    expect(Number(await shell.getAttribute("data-command-count"))).toBe(immediate);
    await loop.getByRole("button", { name: "Release loop hold", exact: true }).click();

    await record();
    const before = Number(await shell.getAttribute("data-command-count"));
    await loop.getByRole("spinbutton", { name: "Loop repeats" }).fill("2");
    await loop.getByRole("button", { name: "Repeat recorded loop", exact: true }).click();
    await expect(loop.getByText("Holding priority", { exact: true })).toBeVisible();
    await expect.poll(async () => Number(await shell.getAttribute("data-command-count"))).toBe(before + 4);
    await expect(forest).toHaveAttribute("data-tapped", "false");
    await expect(host.getByLabel("Your mana pool")).toContainText("Empty");
    await loop.getByRole("button", { name: "Release loop hold", exact: true }).click();
    await record();
    const resume = Number(await shell.getAttribute("data-command-count"));
    await loop.getByLabel("After these repeats").selectOption("resume");
    await loop.getByRole("spinbutton", { name: "Loop repeats" }).fill("1");
    await loop.getByRole("button", { name: "Repeat recorded loop", exact: true }).click();
    await expect(loop.getByRole("button", { name: "Record loop", exact: true })).toBeVisible();
    await expect.poll(async () => Number(await shell.getAttribute("data-command-count"))).toBe(resume + 2);
    await expect(loop.getByText("Holding priority", { exact: true })).toHaveCount(0);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-rules @mana-action manual payment preserves a cast target across mana activation", async ({ browser }, info) => {
  test.setTimeout(300_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Yargle and Multani", cards: "1 Sol Ring\n98 Swamp" },
    { commander: "Mishra, Eminent One", cards: "1 An Offer You Can't Refuse\n98 Island" },
  ], 323);
  const [host, opponent] = pages;
  try {
    for (const page of pages) {
      const land = page.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
      await driveTo(pages, land, info, "Play a mana source");
      await clickMove(page, land);
    }
    await opponent.getByTestId("auto-pass-toggle").click();
    const ring = castOffer(host, "Sol Ring");
    await driveTo(pages, ring, info, "Offer the no-choice Sol Ring cast");
    await clickMove(host, ring);
    await expect(host.getByTestId("choice-dialog")).toHaveCount(0);

    const spell = castOffer(opponent, "An Offer You Can't Refuse");
    await driveTo(pages, spell, info, "Offer the counterspell response");
    const stack = opponent.getByTestId("stack-panel");
    await expect(host.getByTestId("stack-panel")).toContainText("Sol Ring");
    await stack.getByRole("button", { name: "Minimize stack", exact: true }).click();
    await expect(opponent.getByTestId("stack-toggle")).toHaveText("Show stack (1)");
    await expect(stack).toHaveCount(0);
    await spell.click();
    const draft = opponent.getByTestId("public-action-draft");
    await stack.getByRole("button", { name: "Select Sol Ring" }).click();
    await expect(draft).toContainText("1 / 1 · Sol Ring");
    await draft.getByRole("button", { name: "Use manual mana" }).click();
    const parked = opponent.locator(".manual-cast-draft");
    await expect(parked).toContainText("An Offer You Can't Refuse");
    await expect(draft).toHaveCount(0);
    await clickMove(opponent, boardCard(opponent, "Island"));
    await expect(boardCard(opponent, "Island")).toHaveAttribute("data-tapped", "true");
    await expect(opponent.getByTestId("player-B").locator(".zone-summary")).toContainText("U1");
    await expect(opponent.getByRole("button", { name: "Undo mana from Island", exact: true }).first()).toBeVisible();
    if (!await opponent.getByTestId("activity-panel").isVisible()) await opponent.getByTestId("stack-toggle").click();
    await opponent.getByTestId("activity-panel").getByRole("button", { name: "Full log" }).click();
    const log = opponent.getByTestId("public-game-log");
    await expect(log.getByText("Activated Island for mana.", { exact: true })).toBeVisible();
    await expect(log.getByText("Tapped Swamp for 1 black mana.", { exact: true })).toBeVisible();
    await expect(log.getByText("Played Island.", { exact: true })).toBeVisible();
    await log.getByRole("button", { name: "Close", exact: true }).click();
    await parked.getByRole("button", { name: "Resume cast" }).click();
    await expect(draft).toContainText("1 / 1 · Sol Ring");
    await evidence(opponent, info, "manual-mana-resumed-target");
    await clickMove(opponent, draft.getByTestId("submit-choice"));
    if (!await stack.isVisible()) await opponent.getByTestId("stack-toggle").click();
    const counter = stack.locator(".stack-row").first();
    const target = stack.locator(".stack-row").last();
    const forwardLink = counter.getByRole("button", { name: "Sol Ring · Stack #2" });
    await expect(forwardLink).toBeVisible();
    await forwardLink.click();
    await expect(target.getByRole("button", { name: "An Offer You Can't Refuse · Stack #1" })).toBeVisible();
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
