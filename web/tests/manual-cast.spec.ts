import { expect, test } from "@playwright/test";
import { boardCard, castOffer, clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";

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
    await spell.click();
    const draft = opponent.getByTestId("public-action-draft");
    await opponent.getByTestId("stack-panel").getByRole("button", { name: "Select Sol Ring" }).click();
    await expect(draft).toContainText("1 / 1 · Sol Ring");
    await draft.getByRole("button", { name: "Use manual mana" }).click();
    const parked = opponent.locator(".manual-cast-draft");
    await expect(parked).toContainText("An Offer You Can't Refuse");
    await expect(draft).toHaveCount(0);
    await clickMove(opponent, boardCard(opponent, "Island"));
    await expect(boardCard(opponent, "Island")).toHaveAttribute("data-tapped", "true");
    await expect(opponent.getByTestId("player-B").locator(".zone-summary")).toContainText("U1");
    await parked.getByRole("button", { name: "Resume cast" }).click();
    await expect(draft).toContainText("1 / 1 · Sol Ring");
    await evidence(opponent, info, "manual-mana-resumed-target");
    await clickMove(opponent, draft.getByTestId("submit-choice"));
    await expect(opponent.getByTestId("stack-panel")).toContainText("An Offer You Can't Refuse");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
