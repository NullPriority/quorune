import { expect, test, type Page } from "@playwright/test";
import { castOffer, clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";
import { captureRealZoom, zoomContext } from "./support/browserZoom";

test("@browser-rules private hand shortcuts show the offered costs for Flashback and Unearth", async ({ browser }, info) => {
  test.setTimeout(360_000);
  const deskName = "Mishra's Research Desk";
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Mishra, Eminent One", cards: `1 Faithless Looting\n1 ${deskName}\n1 Sol Ring\n10 Mountain\n86 Swamp` },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
  ], 6710, { width: 1366, height: 650 }, await zoomContext(browser, info));
  const [host, opponent] = pages;
  const tray = host.getByTestId("decision-panel");
  const shortcuts = host.getByTestId("hand-zone-offers");
  const fromZone = (name: string) => shortcuts.locator("[data-zone-offer]").filter({ has: host.getByText(name, { exact: true }) });
  const offeredLand = (page: Page, name: string) => page.getByTestId("decision-panel")
    .locator('[data-testid^="action-play-land:"]').filter({ hasText: name }).first();
  try {
    const mountain = offeredLand(host, "Mountain");
    await driveTo(pages, mountain, info, "Play the Mountain for Faithless Looting");
    await clickMove(host, mountain);
    const looting = castOffer(host, "Faithless Looting");
    await driveTo(pages, looting, info, "Cast Faithless Looting from hand");
    await clickMove(host, looting);
    const discard = tray.getByTestId("action-choose");
    await driveTo(pages, discard, info, "Resolve Looting and discard two cards");
    await discard.click();
    const choice = host.getByTestId("choice-dialog");
    await choice.getByRole("checkbox", { name: deskName, exact: true }).check();
    await choice.getByRole("checkbox", { name: "Swamp", exact: true }).first().check();
    await clickMove(host, choice.getByTestId("submit-choice"));

    const swamp = offeredLand(host, "Swamp");
    await driveTo(pages, swamp, info, "Reach the next turn and play the Swamp");
    await clickMove(host, swamp);
    const desk = fromZone(deskName);
    await driveTo(pages, desk, info, "Find the available Unearth ability in the hand area");
    await expect(desk.getByText("Graveyard", { exact: true })).toBeVisible();
    await expect(host.getByTestId("own-hand").getByText(deskName, { exact: true })).toHaveCount(0);
    await expect(opponent.getByTestId("hand-zone-offers").getByText(deskName, { exact: true })).toHaveCount(0);
    await expect(desk.getByTestId("card-offer-cost")).toBeHidden();
    await desk.hover();
    const unearthCost = desk.getByTestId("card-offer-cost");
    await expect(unearthCost).toBeVisible();
    await expect(unearthCost.locator(".ms-1")).toHaveCount(1);
    await expect(unearthCost.locator(".ms-r")).toHaveCount(1);
    expect(await host.evaluate(async () => (await document.fonts.load("14px Mana")).some((font) => font.status === "loaded"))).toBe(true);

    const ring = castOffer(host, "Sol Ring");
    await driveTo(pages, ring, info, "Add Sol Ring for the Flashback cost");
    await clickMove(host, ring);
    const flashback = fromZone("Faithless Looting");
    await driveTo(pages, flashback, info, "Find the currently payable Flashback in the hand area");
    await expect(opponent.getByTestId("hand-zone-offers").getByText("Faithless Looting", { exact: true })).toHaveCount(0);
    await captureRealZoom(host, info);
    await flashback.hover();
    const flashbackCost = flashback.getByTestId("card-offer-cost");
    await expect(flashbackCost.locator(".ms-2")).toHaveCount(1);
    await expect(flashbackCost.locator(".ms-r")).toHaveCount(1);
    await expect(flashbackCost).toBeVisible();
    await expect(tray.getByRole("button", { name: /Cast Faithless Looting/ })).toHaveCount(0);
    await expect(host.getByTestId("hover-card-preview")).toHaveCount(0);
    const [price, card, name] = await Promise.all([
      flashbackCost.boundingBox(), flashback.boundingBox(), flashback.getByText("Faithless Looting", { exact: true }).boundingBox(),
    ]);
    expect(price!.x).toBeGreaterThanOrEqual(card!.x);
    expect(price!.y).toBeGreaterThanOrEqual(card!.y);
    expect(price!.x + price!.width).toBeLessThanOrEqual(card!.x + card!.width);
    expect(price!.y + price!.height).toBeLessThanOrEqual(name!.y);
    await evidence(host, info, "private-flashback-cost-over-card");
    await flashback.click();
    const cast = host.getByTestId("selected-card-actions").getByRole("button", { name: /Cast Faithless Looting/ });
    await expect(cast).toBeVisible();
    await cast.click();
    await clickMove(host, host.getByTestId("choice-dialog").getByTestId("submit-choice"));
    await expect(fromZone("Faithless Looting")).toHaveCount(0);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-rules a card offered from exile can be played from the private hand area", async ({ browser }, info) => {
  test.setTimeout(240_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Mishra, Eminent One", cards: "1 Reckless Impulse\n98 Mountain" },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
  ], 6710);
  const [host, opponent] = pages;
  try {
    const land = host.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
    for (let turn = 1; turn <= 2; turn++) {
      await driveTo(pages, land, info, `Play a Mountain on turn ${turn}`);
      await clickMove(host, land);
    }
    const impulse = castOffer(host, "Reckless Impulse");
    await driveTo(pages, impulse, info, "Cast the spell that lets the pilot play exiled cards");
    await clickMove(host, impulse);
    const exile = host.getByTestId("zone-A-exile");
    await driveTo(pages, exile, info, "Resolve the spell and adjust the exile count");
    await expect(exile).toContainText("2");
    const exiled = host.getByTestId("hand-zone-offers").locator("[data-zone-offer]")
      .filter({ has: host.getByText("Exile", { exact: true }) });
    await driveTo(pages, exiled.first(), info, "Reach the next land play and find the two offered cards");
    await expect(exiled).toHaveCount(2);
    await expect(opponent.getByTestId("hand-zone-offers").getByText("Exile", { exact: true })).toHaveCount(0);
    const handSize = await host.getByTestId("own-hand").locator(".card-tile").count();
    await exiled.first().click();
    await clickMove(host, host.getByTestId("selected-card-actions").getByRole("button", { name: /Play Mountain/ }));
    await expect(exile).toContainText("1");
    await expect(exiled).toHaveCount(0);
    await expect(host.getByTestId("own-hand").locator(".card-tile")).toHaveCount(handSize);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
