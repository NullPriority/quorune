import { expect, test } from "@playwright/test";
import { boardCard, castOffer, clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";

test("@browser-rules aim a cast at a graveyard, choose its card, and retain the link", async ({ browser }, info) => {
  test.setTimeout(300_000);
  const commander = "Yargle and Multani";
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander, cards: "1 Viscera Seer\n1 Ornithopter\n1 Raise Dead\n96 Swamp" },
    { commander, cards: "99 Swamp" },
  ], 6710, { width: 1366, height: 650 });
  const [host] = pages;
  try {
    const tray = host.getByTestId("decision-panel");
    const land = tray.locator('[data-testid^="action-play-land:"]').first();
    await driveTo(pages, land, info, "Find the first land play");
    await clickMove(host, land);
    for (const name of ["Viscera Seer", "Ornithopter"]) {
      const offer = castOffer(host, name);
      await driveTo(pages, offer, info, `Offer ${name}`);
      await clickMove(host, offer);
      await driveTo(pages, boardCard(host, name), info, `Resolve ${name}`);
    }
    const activation = tray.locator('[data-testid^="action-activate:"]').filter({ hasText: "Viscera Seer" });
    await driveTo(pages, activation, info, "Offer Seer’s sacrifice");
    await activation.click();
    const draft = host.getByTestId("public-action-draft");
    await boardCard(host, "Ornithopter").click();
    await clickMove(host, draft.getByTestId("submit-choice"));
    const top = host.getByTestId("contextual-decision").getByRole("button", { name: "Top", exact: true });
    await driveTo(pages, top, info, "Resolve Seer and inspect the private choice");
    await host.getByTestId("contextual-decision").locator(".scry-preview .card-tile").focus();
    await expect(host.locator(".floating-card-preview")).toBeVisible();
    await clickMove(host, top);

    const raise = castOffer(host, "Raise Dead");
    await driveTo(pages, raise, info, "Offer Raise Dead with a creature in the graveyard");
    await raise.click();
    await expect(draft).toContainText("Raise Dead");
    await expect(draft.getByText("Casting cost", { exact: true })).toHaveCount(0);
    const graveyard = host.getByTestId("zone-A-graveyard");
    await expect(graveyard).toHaveClass(/zone-target/);
    await graveyard.hover();
    const arrow = host.getByTestId("target-arrow-layer");
    await expect(arrow).toHaveAttribute("data-arrow-count", "1");
    await evidence(host, info, "aiming-at-graveyard");
    await graveyard.click();
    const drawer = host.getByTestId("zone-browser");
    await expect(drawer).toBeVisible();
    await host.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(draft).toBeVisible();
    await graveyard.click();
    const creature = drawer.getByRole("button", { name: "Select Ornithopter" });
    await creature.click();
    await expect(creature).toHaveAttribute("aria-pressed", "true");
    await drawer.getByRole("button", { name: "Back to table" }).click();
    await expect(draft).toContainText("1 / 1 · Ornithopter");
    await expect(arrow).toHaveAttribute("data-arrow-count", "1");
    await host.getByTestId("auto-pass-toggle").click();
    await clickMove(host, draft.getByTestId("submit-choice"));
    await expect(arrow).toHaveAttribute("data-arrow-count", "1");
    await host.locator(".board-detach summary").click();
    const boardsMenu = host.locator(".board-detach-options");
    await boardsMenu.getByLabel("Seat B").check();
    const popup = host.waitForEvent("popup");
    await boardsMenu.getByRole("button", { name: "Open detached boards" }).click();
    const detached = await popup;
    await expect(detached.getByTestId("stack-panel")).toContainText("Raise Dead");
    await expect(detached.getByTestId("player-B")).toBeVisible();
    await expect(detached.getByTestId("own-hand")).toHaveCount(0);
    await detached.close();
    const inHand = host.getByTestId("own-hand").getByText("Ornithopter", { exact: true });
    await driveTo(pages, inHand, info, "Resolve Raise Dead and return the selected card to hand");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-rules a raised spell aims at a legal player by keyboard or pointer", async ({ browser }, info) => {
  test.setTimeout(180_000);
  const commander = "Yargle and Multani";
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander, cards: "1 Duress\n98 Swamp" },
    { commander, cards: "99 Swamp" },
  ], 6710, { width: 1366, height: 650 });
  const [host] = pages;
  try {
    const land = host.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
    await driveTo(pages, land, info, "Find a land for Duress");
    await clickMove(host, land);
    const spell = castOffer(host, "Duress");
    await driveTo(pages, spell, info, "Offer a spell that targets an opponent");
    await spell.click();
    const opponent = host.getByRole("button", { name: /^Select Seat B,/ });
    await expect(host.getByRole("button", { name: /^Select Seat A,/ })).toHaveCount(0);
    await opponent.focus();
    const arrow = host.getByTestId("target-arrow-layer");
    await expect(arrow).toHaveAttribute("data-arrow-count", "1");
    await opponent.hover();
    await expect(arrow).toHaveAttribute("data-arrow-count", "1");
    await opponent.click();
    const draft = host.getByTestId("public-action-draft");
    await expect(draft).toContainText("1 / 1 · Seat B");
    const command = host.waitForResponse((response) => response.request().method() === "POST"
      && response.url().endsWith("/commands") && response.request().postDataJSON().action_id.startsWith("cast:"));
    await clickMove(host, draft.getByTestId("submit-choice"));
    const response = await command;
    expect((await response.json()).receipt.code).toBe("accepted");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
