import { expect, test } from "@playwright/test";
import { castOffer, clickMove, driveTo, evidence, projectedState, startPlaytestPod } from "./support/playtest";
import { completeCastGesture, openGameMenu } from "./support/startup";
import { driveUntil, viewRevision } from "./support/progress";

test("@browser-lifecycle opponents can be focused and crowded card groups page without scrolling", async ({ browser }, info) => {
  test.setTimeout(300_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Mishra, Eminent One", cards: "1 Ornithopter\n1 Memnite\n1 Phyrexian Walker\n1 Sol Ring\n95 Mountain" },
    { commander: "Zimone and Dina", cards: "99 Island" },
    { commander: "Adeline, Resplendent Cathar", cards: "99 Plains" },
    { commander: "Krenko, Tin Street Kingpin", cards: "99 Mountain" },
  ], 97249, { width: 1280, height: 720 });
  const [host, observer] = pages;
  for (const page of pages) {
    page.setDefaultTimeout(12_000);
    page.setDefaultNavigationTimeout(18_000);
  }
  try {
    const land = host.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
    await driveTo(pages, land, info, "Play the first Mountain");
    await clickMove(host, land);
    for (const name of ["Sol Ring", "Ornithopter", "Memnite", "Phyrexian Walker"]) {
      const offer = castOffer(host, name);
      await driveTo(pages, offer, info, `Offer ${name}`);
      await completeCastGesture(host, () => offer.click());
    }
    await driveUntil(pages, async () => {
      const { players } = await projectedState(host);
      return players.A.bf.some((card: { n: string }) => card.n === "Phyrexian Walker");
    }, info, { label: "Resolve the final artifact creature" });
    for (const page of pages) {
      const toggle = page.getByTestId("auto-pass-toggle");
      if (await toggle.getAttribute("aria-pressed") === "true") await toggle.click();
    }

    const views = observer.getByRole("group", { name: "Battlefield views" });
    const visible = observer.locator(".player-board:visible");
    await expect(visible).toHaveCount(4);
    const revision = await viewRevision(observer);
    await views.getByRole("button", { name: "Seat A", exact: true }).click();
    await expect(visible).toHaveCount(2);
    const opponent = observer.getByTestId("player-A");
    const lands = opponent.getByRole("group", { name: "Lands", exact: true });
    const creatures = opponent.getByRole("group", { name: "Creatures", exact: true });
    const support = opponent.getByRole("group", { name: "Other permanents", exact: true });
    await expect(lands).toContainText("Mountain");
    await expect(creatures).toContainText("Memnite");
    await expect(creatures).toContainText("Ornithopter");
    await expect(creatures).toContainText("Phyrexian Walker");
    await expect(support).toContainText("Sol Ring");
    expect((await lands.boundingBox())!.y).toBeLessThan((await creatures.boundingBox())!.y);
    const own = host.getByTestId("player-A");
    expect((await own.getByRole("group", { name: "Creatures", exact: true }).boundingBox())!.y)
      .toBeLessThan((await own.getByRole("group", { name: "Lands", exact: true }).boundingBox())!.y);
    await evidence(observer, info, "focused-opponent-artifact-creatures");
    await views.getByRole("button", { name: "Seat C", exact: true }).click();
    await expect(observer.getByTestId("player-C")).toBeVisible();
    await expect(opponent).toBeHidden();
    await views.getByRole("button", { name: "All opponents", exact: true }).click();
    await expect(visible).toHaveCount(4);

    await observer.setViewportSize({ width: 780, height: 650 });
    const next = creatures.getByRole("button", { name: "Next Creatures page" });
    await expect(next).toBeVisible();
    const seen = new Set<string>();
    for (let page = 0; page < 3; page++) {
      for (const name of await creatures.locator(".card-copy strong").allTextContents()) seen.add(name);
      const clip = await creatures.locator(".card-page-track").evaluate((track) => ({
        shown: track.clientWidth, content: track.scrollWidth, overflow: getComputedStyle(track).overflowX,
      }));
      expect(clip.overflow).toBe("hidden");
      expect(clip.content).toBeLessThanOrEqual(clip.shown + 2);
      if (await next.getAttribute("aria-disabled") === "true") break;
      await next.click();
    }
    expect([...seen].sort()).toEqual(["Memnite", "Ornithopter", "Phyrexian Walker"]);

    await observer.setViewportSize({ width: 1280, height: 720 });
    const settings = observer.getByTestId("table-settings");
    await openGameMenu(observer);
    await settings.locator("summary").first().click();
    await settings.getByRole("slider", { name: "Hand panel height" }).fill("220");
    await settings.getByRole("slider", { name: "Hand width" }).fill("45");
    await observer.getByTestId("open-game-menu").click();

    const hand = observer.getByTestId("own-hand");
    const firstCard = hand.locator(".card-tile").first();
    const tableSize = observer.getByRole("slider", { name: "Table card size" });
    await tableSize.fill("0.65");
    const oldWidth = (await firstCard.boundingBox())!.width;
    await tableSize.fill("1.6");
    await expect.poll(async () => (await firstCard.boundingBox())!.width).toBeGreaterThan(oldWidth);

    await observer.setViewportSize({ width: 780, height: 650 });
    const nextHand = observer.getByRole("button", { name: "Next Hand page" });
    await expect(nextHand).toBeVisible();
    const firstRef = await firstCard.getAttribute("data-card-ref");
    await nextHand.click();
    await expect(firstCard).not.toHaveAttribute("data-card-ref", firstRef!);

    await openGameMenu(observer);
    if (await settings.getAttribute("open") === null) await settings.locator("summary").first().click();
    await observer.getByText("Card sizes by zone", { exact: true }).click();
    const resizeZone = observer.getByLabel("Zone to resize", { exact: true });
    for (const label of ["Hand", "Battlefield", "Command zone", "Graveyard", "Exile", "Library choices", "Stack"]) {
      await resizeZone.selectOption({ label });
      await expect(observer.getByRole("slider", { name: `${label} card size`, exact: true })).toBeVisible();
    }
    await resizeZone.selectOption({ label: "Hand" });
    await observer.getByRole("slider", { name: "Hand card size", exact: true }).fill("0.65");
    await expect(observer.locator(".game-shell")).toHaveAttribute("style", /--hand-card-scale: 1\.04/);
    await observer.getByTestId("open-game-menu").click();
    await views.getByRole("button", { name: "Seat A", exact: true }).click();
    expect(await viewRevision(observer)).toBe(revision);
    await observer.reload({ waitUntil: "domcontentloaded" });
    await expect(views.getByRole("button", { name: "Seat A", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(observer.getByRole("slider", { name: "Table card size" })).toHaveValue("1.6");
    await expect(visible).toHaveCount(2);
    await expect(observer.locator(".game-shell")).toHaveAttribute("style", /--hand-card-scale: 1\.04/);
    await observer.setViewportSize({ width: 620, height: 480 });
    await expect(views.getByRole("button", { name: "Your board", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(visible).toHaveCount(1);
    await views.getByRole("button", { name: "Seat A", exact: true }).click();
    await expect(opponent).toBeVisible();
    await expect(visible).toHaveCount(1);
    expect(await observer.evaluate(() => document.documentElement.scrollHeight)).toBe(480);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
test("@browser-lifecycle table card size changes the commander and normal clicks preserve hand height", async ({ browser }) => {
  const deck = { commander: "Yargle and Multani", cards: "99 Swamp" };
  const { contexts, pages: [host] } = await startPlaytestPod(
    browser, [deck, deck, deck, deck], 9183, { width: 1500, height: 940 },
  );
  try {
    await expect(host.locator(".boards-grid > .player-board")).toHaveCount(4);
    expect(await host.locator(".boards-grid > .player-board").evaluateAll(
      (boards) => boards.map((board) => board.getAttribute("data-seat")),
    )).toEqual(["B", "C", "D", "A"]);
    const commander = host.getByTestId("player-A").locator(".command-cards .card-tile");
    const slider = host.getByRole("slider", { name: "Table card size" }).first();
    await slider.press("Home");
    const small = (await commander.boundingBox())!.width;
    await slider.press("End");
    await expect.poll(async () => (await commander.boundingBox())!.width).toBeGreaterThan(small + 5);

    const hand = host.getByTestId("hand-panel");
    const before = (await hand.boundingBox())!.height;
    await hand.getByRole("heading", { name: "Your hand" }).click();
    await hand.getByRole("heading", { name: "Your hand" }).click();
    await expect.poll(async () => (await hand.boundingBox())!.height).toBe(before);
    const settings = host.getByTestId("table-settings");
    await host.getByTestId("open-game-menu").click();
    await settings.locator("summary").first().click();
    await settings.getByText("Table colors", { exact: true }).click();
    await settings.getByLabel("Accent").fill("#966cff");
    await settings.getByLabel("Background tint").fill("#19122b");
    const shell = host.locator(".game-shell");
    await expect(shell).toHaveCSS("--accent", "#966cff");
    await host.reload();
    await expect(shell).toHaveCSS("--accent", "#966cff");
    await expect(shell).toHaveCSS("--user-table-background", "#19122b");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
