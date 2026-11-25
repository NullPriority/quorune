import { expect, test } from "@playwright/test";
import { castOffer, clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";
import { completeCastGesture, openGameMenu } from "./support/startup";
import { viewRevision } from "./support/progress";

test("@browser-lifecycle opponents can be focused and crowded card groups page without scrolling", async ({ browser }, info) => {
  test.setTimeout(300_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Mishra, Eminent One", cards: "1 Ornithopter\n1 Memnite\n1 Phyrexian Walker\n1 Sol Ring\n95 Mountain" },
    { commander: "Zimone and Dina", cards: "99 Island" },
    { commander: "Adeline, Resplendent Cathar", cards: "99 Plains" },
    { commander: "Krenko, Tin Street Kingpin", cards: "99 Mountain" },
  ], 97249, { width: 1280, height: 720 });
  const [host, observer] = pages;
  try {
    const land = host.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
    await driveTo(pages, land, info, "Play the first Mountain");
    await clickMove(host, land);
    for (const name of ["Sol Ring", "Ornithopter", "Memnite", "Phyrexian Walker"]) {
      const offer = castOffer(host, name);
      await driveTo(pages, offer, info, `Offer ${name}`);
      await completeCastGesture(host, () => offer.click());
    }
    await driveTo(pages, host.getByTestId("own-battlefield").getByText("Phyrexian Walker", { exact: true }),
      info, "Resolve the final artifact creature");

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

    const hand = observer.getByTestId("own-hand");
    const firstCard = hand.locator(".card-tile").first();
    const oldWidth = (await firstCard.boundingBox())!.width;
    await observer.getByRole("slider", { name: "Table card size" }).fill("1.6");
    await expect.poll(async () => (await firstCard.boundingBox())!.width).toBeGreaterThan(oldWidth);
    const nextHand = observer.getByRole("button", { name: "Next Hand page" });
    await expect(nextHand).toBeVisible();
    const firstRef = await firstCard.getAttribute("data-card-ref");
    await nextHand.click();
    await expect(firstCard).not.toHaveAttribute("data-card-ref", firstRef!);

    await openGameMenu(observer);
    await observer.getByTestId("table-settings").locator("summary").first().click();
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
    await observer.reload();
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
