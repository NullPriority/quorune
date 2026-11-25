import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { castOffer, clickMove, evidence, startPlaytestPod, submitSingleCleanupDiscard } from "./support/playtest";
import { driveUntil } from "./support/progress";
import { completeCastGesture } from "./support/startup";

const opponentDeck = { commander: "Yargle and Multani", cards: "99 Swamp" };

async function ready(pages: Page[], target: Locator, testInfo: TestInfo, label: string) {
  await driveUntil(pages, async () => await target.isVisible() && await target.isEnabled(), testInfo, {
    label,
    advance: () => submitSingleCleanupDiscard(pages),
  });
}

async function projectedState(page: Page) {
  const game = await page.locator(".game-shell").getAttribute("data-game-id");
  return page.evaluate(async (id) => {
    const tab = sessionStorage.getItem("commander-browser-tab");
    if (!tab) throw new Error("Missing a browser tab identity");
    const response = await fetch(`/api/v1/games/${id}/state?full=true`, {
      headers: { "X-Commander-Tab": tab }, credentials: "same-origin",
    });
    if (!response.ok) throw new Error(`Game state request returned ${response.status}`);
    return (await response.json()).packet.state;
  }, game);
}

async function expectTableAspect(page: Page) {
  const layout = await page.locator(".game-shell").evaluate((table) => {
    const { width, height, left, top } = table.getBoundingClientRect();
    return {
      width, height, left, top,
      availableWidth: window.innerWidth,
      availableHeight: window.innerHeight,
      documentWidth: document.documentElement.scrollWidth,
      documentHeight: document.documentElement.scrollHeight,
    };
  });
  const expectedWidth = Math.min(layout.availableWidth, layout.availableHeight * 16 / 9);
  expect(Math.abs(layout.width - expectedWidth)).toBeLessThan(1);
  expect(Math.abs(layout.height - expectedWidth * 9 / 16)).toBeLessThan(1);
  expect(Math.abs(layout.left - (layout.availableWidth - layout.width) / 2)).toBeLessThan(1);
  expect(Math.abs(layout.top - (layout.availableHeight - layout.height) / 2)).toBeLessThan(1);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.availableWidth);
  expect(layout.documentHeight).toBeLessThanOrEqual(layout.availableHeight);
}

async function expectLandPrinting(card: Locator, tapped: boolean, cid: string) {
  await expect(card).toHaveAttribute("data-tapped", String(tapped));
  await expect(card).toBeInViewport({ ratio: .95 });
  const face = card.locator(".battlefield-printed-face");
  const image = face.locator("img");
  const requireArt = process.env.QUORUNE_TEST_ZONE_ART === "1";
  if (requireArt) {
    await expect(image).toHaveAttribute("src", new RegExp(`/${cid}/image\\?`));
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
  }
  const layout = await face.evaluate((element, requireArt) => {
    const frame = element.parentElement!.getBoundingClientRect();
    const lane = element.closest(".battlefield-row")!.getBoundingClientRect();
    const artwork = requireArt ? element.querySelector("img")! : element;
    const card = element.closest(".battlefield-card")!;
    return {
      frame: frame.toJSON(),
      artwork: artwork.getBoundingClientRect().toJSON(),
      lane: lane.toJSON(),
      rotation: getComputedStyle(element).rotate,
      cardTransform: getComputedStyle(card).transform,
    };
  }, requireArt);
  expect(layout.rotation).toBe(tapped ? "90deg" : "none");
  expect(layout.cardTransform).toBe("none");
  expect(layout.frame.width / layout.frame.height).toBeCloseTo(tapped ? 88 / 63 : 63 / 88, 2);
  expect(Math.min(layout.frame.width, layout.frame.height)).toBeGreaterThanOrEqual(24);
  for (const edge of ["left", "top", "right", "bottom"]) {
    expect(Math.abs(layout.artwork[edge] - layout.frame[edge])).toBeLessThan(1);
  }
  expect(layout.frame.top).toBeGreaterThanOrEqual(layout.lane.top);
  expect(layout.frame.left).toBeGreaterThanOrEqual(layout.lane.left);
  expect(layout.frame.bottom).toBeLessThanOrEqual(layout.lane.bottom);
  expect(layout.frame.right).toBeLessThanOrEqual(layout.lane.right);
  if (tapped) {
    await expect(card).toHaveAccessibleDescription(/Tapped/);
    await expect(card.getByText("Tapped", { exact: true })).toBeHidden();
  }
}

test("@browser-rules visible zone piles distinguish a face-down exile and a private library look", async ({ browser }, info) => {
  test.setTimeout(320_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    {
      commander: "Mishra, Eminent One",
      cards: [
        "1 Sol Ring", "1 Saw It Coming", "1 The Reality Chip", "1 Tormod's Crypt",
        "1 Generic Library Reveal Permission", "1 Generic Library Action Permission", "93 Island",
      ].join("\n"),
    },
    opponentDeck,
  ], 97249);
  const [host, opponent] = pages;
  const images: string[] = [];
  opponent.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/image")) images.push(path);
  });
  try {
    const board = host.getByTestId("player-A");
    const library = host.getByTestId("zone-A-library");
    const opponentLibrary = opponent.getByTestId("zone-A-library");
    const exile = host.getByTestId("zone-A-exile");
    const opposingExile = opponent.getByTestId("zone-A-exile");
    await expect(library).toHaveText(/^Library \d+$/);
    await expect(board.getByTestId("zone-A-graveyard")).toBeVisible();
    await expect(exile).toBeVisible();
    await board.getByRole("button", { name: "Collapse zones" }).click();
    await expect(library.locator(".zone-pile-thumbnail")).toHaveCount(0);
    await expect(library).toHaveText(/^Library \d+$/);
    await board.getByRole("button", { name: "Expand zones" }).click();
    await expect(library.locator(".zone-pile-thumbnail")).toBeVisible();
    await expect(library).toHaveText(/^Library \d+$/);
    await library.hover();
    await expect(host.getByTestId("zone-peek")).toHaveCount(0);
    await library.click();
    const drawer = host.getByRole("dialog", { name: /^Library/ });
    await expect(drawer.getByRole("heading", { name: /^Library · \d+$/ })).toBeVisible();
    await expect(drawer.locator(".zone-card-grid, .zone-empty")).toHaveCount(0);
    await expect(drawer.locator(".card-tile img")).toHaveCount(0);
    await drawer.getByRole("button", { name: "Close" }).click();

    const land = host.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
    await ready(pages, land, info, "Play the first Island");
    await clickMove(host, land);
    for (const name of ["Sol Ring", "Tormod's Crypt"]) {
      const cast = castOffer(host, name);
      await ready(pages, cast, info, `Cast ${name}`);
      await completeCastGesture(host, () => cast.click());
    }

    const foretell = host.getByTestId("decision-panel").getByRole("button", { name: /Foretell.*Saw It Coming/i });
    await ready(pages, foretell, info, "Foretell Saw It Coming");
    await completeCastGesture(host, () => foretell.click());
    await expect(exile).toContainText("Saw It Coming · Face down");
    await expect(opposingExile).toContainText("Face-down card");
    await expect(opposingExile.locator("img")).toHaveCount(0);
    const hostCard = (await projectedState(host)).players.A.ex.find((card: { n: string }) => card.n === "Saw It Coming");
    const otherCard = (await projectedState(opponent)).players.A.ex.find((card: { fd?: number }) => card.fd);
    expect(hostCard).toMatchObject({ n: "Saw It Coming", fd: 1 });
    expect(otherCard).toMatchObject({ n: "?", fd: 1 });
    expect(otherCard).not.toHaveProperty("cid");

    const crypt = host.getByTestId("own-battlefield").locator(".card-tile").filter({ hasText: "Tormod's Crypt" });
    const ref = await crypt.getAttribute("data-card-ref");
    const activate = host.getByTestId("decision-panel").locator(`[data-testid^="action-activate:${ref}:"]`).first();
    await ready(pages, activate, info, "Offer Tormod's Crypt");
    for (const page of pages) {
      const toggle = page.getByTestId("auto-pass-toggle");
      if (await toggle.getAttribute("aria-pressed") === "true") await toggle.click();
    }
    await activate.click();
    const draft = host.getByTestId("public-action-draft");
    await host.getByRole("button", { name: /^Select Seat A,/ }).click();
    await clickMove(host, draft.getByTestId("submit-choice"));
    const graveyard = host.getByTestId("zone-A-graveyard");
    await expect(graveyard).toContainText("Tormod's Crypt");
    await graveyard.hover();
    await expect(host.getByTestId("zone-peek")).toContainText("Tormod's Crypt");
    await graveyard.click();
    await expect(host.getByRole("dialog", { name: /^Graveyard/ }).locator(".zone-card-grid")).toContainText("Tormod's Crypt");
    await host.getByRole("dialog", { name: /^Graveyard/ }).getByRole("button", { name: "Close" }).click();

    const chip = castOffer(host, "The Reality Chip");
    await ready(pages, land, info, "Play the next Island after Crypt resolves");
    await clickMove(host, land);
    await ready(pages, chip, info, "Cast The Reality Chip");
    await completeCastGesture(host, () => chip.click());
    const chipOnBoard = host.getByTestId("own-battlefield").getByText("The Reality Chip", { exact: true });
    await ready(pages, chipOnBoard, info, "Resolve The Reality Chip");
    const top = (await projectedState(host)).players.A.known_top[0];
    expect(top.n).toBeTruthy();
    await expect(library).toContainText(top.n);
    await expect(opponentLibrary).toHaveText(/^Library \d+$/);
    await expect(opponentLibrary.locator("img")).toHaveCount(0);
    expect((await projectedState(opponent)).players.A).not.toHaveProperty("known_top");
    if (process.env.QUORUNE_TEST_ZONE_ART === "1") {
      const art = library.locator(".zone-pile-thumbnail img");
      await expect.poll(() => art.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
      await expect(art).toHaveAttribute("src", new RegExp(`/${top.cid}/image\\?`));
      await expect(opponentLibrary.locator("img")).toHaveCount(0);
    }

    await expect(opposingExile).toContainText("Tormod's Crypt");
    await opposingExile.hover();
    const peek = opponent.getByTestId("zone-peek");
    await expect(peek).toContainText("Tormod's Crypt");
    await expect(peek).toContainText("Face-down card");
    await opposingExile.click();
    const opposingCards = opponent.getByRole("dialog", { name: /^Exile/ }).locator(".zone-card-grid");
    await expect(opposingCards).toContainText("Tormod's Crypt");
    const concealed = opposingCards.locator('[data-concealed="true"]');
    await expect(concealed).toContainText("Face-down card");
    await expect(concealed.locator("img")).toHaveCount(0);
    await expect(opposingCards).not.toContainText("Saw It Coming");
    expect(images.some((path) => path.includes(`/${hostCard.cid}/`))).toBe(false);
    await opponent.getByRole("dialog", { name: /^Exile/ }).getByRole("button", { name: "Close" }).click();
    await evidence(host, info, "private-library-and-mixed-exile-piles");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-rules a public library reveal shows the same projected face to both seats", async ({ browser }, info) => {
  test.setTimeout(230_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Mishra, Eminent One", cards: "1 Sol Ring\n1 Lantern of Insight\n97 Island" },
    opponentDeck,
  ], 97249, { width: 1600, height: 900 });
  const [host, opponent] = pages;
  try {
    const landOnBoard = (page: Page) => page.getByTestId("player-A").locator(".battlefield-lands .battlefield-card");
    const land = host.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
    await ready(pages, land, info, "Play the first Island");
    await clickMove(host, land);
    const printedIsland = (await projectedState(host)).players.A.bf[0];
    expect(printedIsland.n).toBe("Island");
    await expectTableAspect(opponent);
    await expectLandPrinting(landOnBoard(opponent), false, printedIsland.cid);
    for (const name of ["Sol Ring", "Lantern of Insight"]) {
      const cast = castOffer(host, name);
      await ready(pages, cast, info, `Cast ${name}`);
      await completeCastGesture(host, () => cast.click());
    }
    const permanent = host.getByTestId("own-battlefield").getByText("Lantern of Insight", { exact: true });
    await ready(pages, permanent, info, "Resolve the reveal permission");
    await expectLandPrinting(landOnBoard(host), true, printedIsland.cid);
    await expectLandPrinting(landOnBoard(opponent), true, printedIsland.cid);
    const top = (await projectedState(host)).players.A.known_top[0];
    const other = (await projectedState(opponent)).players.A.known_top[0];
    expect(other).toMatchObject({ n: top.n, cid: top.cid });
    expect(other.n).not.toBe("?");
    const visible = opponent.getByTestId("zone-A-library");
    await expect(visible).toContainText(top.n);
    if (process.env.QUORUNE_TEST_ZONE_ART === "1") {
      const art = visible.locator(".zone-pile-thumbnail img");
      await expect.poll(() => art.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
      await expect(art).toHaveAttribute("src", new RegExp(`/${top.cid}/image\\?`));
    }
    await evidence(opponent, info, "opponent-battlefield-and-visible-zone-piles");
    await visible.hover();
    await expect(opponent.getByTestId("zone-peek")).toContainText(top.n);
    await visible.click();
    const drawer = opponent.getByRole("dialog", { name: /^Library/ });
    await expect(drawer.locator(".zone-card-grid")).toContainText(top.n);
    await expect(drawer.locator(".zone-card-grid .card-tile")).toHaveCount(1);
    await drawer.getByRole("button", { name: "Close" }).click();
    await opponent.mouse.move(1, 1);
    await expect(opponent.getByTestId("zone-peek")).toHaveCount(0);

    await opponent.setViewportSize({ width: 1366, height: 650 });
    await expectTableAspect(opponent);
    await expectLandPrinting(landOnBoard(opponent), true, printedIsland.cid);
    await evidence(opponent, info, "opponent-battlefield-in-wide-short-window");

    await opponent.setViewportSize({ width: 1280, height: 800 });
    await expectTableAspect(opponent);
    await expectLandPrinting(landOnBoard(opponent), true, printedIsland.cid);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
