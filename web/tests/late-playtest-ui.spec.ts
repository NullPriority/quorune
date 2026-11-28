import { expect, test, type Locator } from "@playwright/test";
import { boardCard, castOffer, clickMove, driveTo, startPlaytestPod } from "./support/playtest";
import { openGameMenu } from "./support/startup";

function manaCostLayout(cost: Locator) {
  return cost.evaluate((node) => {
    const bounds = node.getBoundingClientRect();
    const card = node.closest(".card-tile")!.getBoundingClientRect();
    const symbols = [...node.querySelectorAll(".ms")].map((pip) => pip.getBoundingClientRect());
    return {
      symbols: symbols.length,
      rows: new Set(symbols.map((pip) => Math.round(pip.top))).size,
      fits: symbols.every((pip) => pip.left >= bounds.left && pip.right <= bounds.right
        && pip.top >= bounds.top && pip.bottom <= bounds.bottom + 1
        && pip.left >= card.left && pip.right <= card.right && pip.bottom <= card.bottom),
    };
  });
}

test("@browser-lifecycle table layout, companion windows and explicit first-main yield", async ({ browser }, info) => {
  test.setTimeout(150_000);
  const context = await browser.newContext({ viewport: { width: 1720, height: 820 } });
  await context.addInitScript(() => {
    if (location.protocol !== "http:" && location.protocol !== "https:") return;
    const key = "commander-table-preferences:v2";
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ autoPass: false }));
  });
  const decks = [
    { commander: "Yargle and Multani", cards: "99 Swamp" },
    { commander: "Zimone and Dina", cards: "99 Island" },
    { commander: "Adeline, Resplendent Cathar", cards: "99 Plains" },
    { commander: "Krenko, Tin Street Kingpin", cards: "99 Mountain" },
  ];
  const { contexts, pages: [host] } = await startPlaytestPod(browser, decks, 9183, { width: 1720, height: 820 }, context);
  try {
    const hand = host.getByTestId("hand-panel");
    const battlefield = host.getByRole("region", { name: "Commander battlefield" });
    const sidebar = host.locator(".main-table-workspace > .table-sidebar");
    const stackToggle = host.getByTestId("stack-toggle");
    const passTurn = host.getByRole("button", { name: "Pass turn", exact: true });
    async function measureLayout(name: string) {
      const result = await host.evaluate(() => {
        const bounds = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
        const board = bounds(".main-table-workspace > .boards-grid");
        const header = bounds(".game-topbar");
        const dock = bounds(".table-bottom-dock");
        const command = bounds(".own-board .command-zone");
        return {
          viewport: [innerWidth, innerHeight],
          headerHeight: Math.round(header.height),
          battlefieldTop: Math.round(board.top),
          battlefieldHeight: Math.round(board.height),
          battlefieldWidth: Math.round(board.width),
          commandZoneWidth: Math.round(command.width),
          dockHeight: Math.round(dock.height),
          dockBottom: Math.round(dock.bottom),
        };
      });
      await info.attach(name, { body: JSON.stringify(result), contentType: "application/json" });
      return result;
    }
    const cards = hand.locator(".card-tile");
    await expect(cards).toHaveCount(7);
    await expect(cards.first().locator(".card-copy strong")).toBeVisible();
    if (await cards.first().locator("img").count()) {
      await expect(cards.first().locator("img")).toHaveCSS("object-fit", "contain");
    }
    await expect(host.getByTestId("exact-step-label")).toHaveText("Upkeep");
    const boxes = await Promise.all([cards.nth(0).boundingBox(), cards.nth(1).boundingBox()]);
    expect(boxes[0]!.x + boxes[0]!.width).toBeLessThanOrEqual(boxes[1]!.x + 1);
    await cards.nth(0).focus();
    await expect(host.locator(".floating-card-preview")).toContainText("Swamp");
    await host.keyboard.press("Tab");
    await expect(cards.nth(1)).toBeFocused();
    await cards.nth(2).click();
    await expect(host.getByTestId("selected-card-actions")).toContainText("Swamp");
    await host.getByTestId("selected-card-actions").getByRole("button", { name: "Close" }).click();

    const main = host.getByTestId("phase-rail").getByRole("button", { name: "Main 1" });
    await main.click();
    await host.getByRole("button", { name: "Advance to first main" }).click();
    await expect(host.getByText("Priority held · First main phase")).toBeVisible({ timeout: 45_000 });
    await expect(host.getByTestId("exact-step-label")).toHaveText("Main Phase 1");
    const handBox = (await hand.boundingBox())!;
    const actionsBox = (await host.getByTestId("decision-panel").boundingBox())!;
    const zonesBox = (await host.getByRole("group", { name: "Seat A card zones", exact: true }).boundingBox())!;
    expect(actionsBox.x).toBeGreaterThan(handBox.x + handBox.width);
    expect(actionsBox.y).toBeGreaterThanOrEqual(zonesBox.y + zonesBox.height);
    expect(handBox.width / 1720).toBeCloseTo(.66, 1);
    expect((await host.locator(".game-topbar").boundingBox())!.y).toBeLessThanOrEqual(2);
    expect((await stackToggle.boundingBox())!.y).toBeLessThan((await battlefield.boundingBox())!.y);
    expect((await passTurn.boundingBox())!.x).toBeGreaterThan(handBox.x + handBox.width);
    expect((await passTurn.boundingBox())!.y).toBeLessThan(handBox.y + handBox.height);
    const desktop = await measureLayout("desktop-layout");
    expect(desktop.headerHeight).toBeLessThanOrEqual(45);
    expect(desktop.battlefieldTop).toBeLessThanOrEqual(78);
    expect(desktop.dockHeight).toBeLessThanOrEqual(215);
    expect(820 - desktop.dockBottom).toBeLessThanOrEqual(8);
    await host.screenshot({ path: info.outputPath("desktop.png"), animations: "disabled" });

    const settings = host.getByTestId("table-settings");
    const opponent = host.getByTestId("player-C");
    const initialWidth = (await opponent.boundingBox())!.width;
    const initialRailWidth = (await sidebar.boundingBox())!.width;
    await openGameMenu(host);
    await settings.locator("summary").first().click();
    await settings.getByLabel("Art-focused hand").check();
    await settings.getByRole("slider", { name: "Hand width" }).fill("55");
    await settings.getByRole("slider", { name: "Hand panel height" }).fill("220");
    await settings.getByRole("slider", { name: "Right rail width" }).fill("340");
    await settings.getByText("Opponent board widths", { exact: true }).click();
    await settings.getByRole("slider", { name: "Seat C board width" }).fill("175");
    await settings.getByText("Table colors", { exact: true }).click();
    await settings.getByRole("button", { name: "Midnight", exact: true }).click();
    await host.getByTestId("open-game-menu").click();
    await expect.poll(async () => (await opponent.boundingBox())!.width).toBeGreaterThan(initialWidth);
    await expect.poll(async () => (await sidebar.boundingBox())!.width).toBeGreaterThan(initialRailWidth + 50);
    await expect.poll(async () => (await hand.boundingBox())!.width).toBeLessThan(handBox.width);
    await expect.poll(async () => (await hand.boundingBox())!.height).toBeGreaterThan(handBox.height);
    await expect(hand.locator(".hand-scroll")).toHaveClass(/art-focused/);

    const activity = host.getByTestId("activity-panel");
    await expect(activity.getByRole("list", { name: "Recent activity" })).toHaveCSS("overflow-y", "auto");
    const boardWithRail = (await battlefield.boundingBox())!.width;
    const viewerWindow = host.waitForEvent("popup");
    await host.getByRole("button", { name: "Pop out viewer" }).click();
    const viewer = await viewerWindow;
    await expect(viewer.getByTestId("card-inspector-expanded")).toBeVisible();
    await expect(activity.getByRole("list")).toHaveCount(0);
    await expect.poll(async () => (await battlefield.boundingBox())!.width).toBeGreaterThan(boardWithRail + 275);
    await measureLayout("rail-collapsed-layout");
    await host.screenshot({ path: info.outputPath("rail-collapsed.png"), animations: "disabled" });
    await activity.getByRole("button", { name: "Show recent activity" }).click();
    await expect(activity.getByRole("list")).toBeVisible();
    await expect.poll(async () => (await battlefield.boundingBox())!.width).toBeLessThan(boardWithRail + 3);
    await viewer.close();

    await host.locator(".board-detach summary").click();
    const detach = host.locator(".board-detach-options");
    await detach.getByLabel("Seat C").check();
    await detach.getByLabel("Seat D").check();
    const boardWindow = host.waitForEvent("popup");
    await detach.getByRole("button", { name: "Open detached boards" }).click();
    const boards = await boardWindow;
    const detachedC = boards.getByTestId("player-C");
    const detachedD = boards.getByTestId("player-D");
    await expect(detachedC).toBeVisible();
    await expect(detachedD).toBeVisible();
    await expect(boards.getByTestId("stack-panel")).toBeVisible();
    await expect(host.getByTestId("player-C")).toBeHidden();
    await expect(boards.getByTestId("player-B")).toHaveCount(0);
    await expect(boards.getByText("Mountain", { exact: true })).toHaveCount(0);
    await expect(boards.getByText("Plains", { exact: true })).toHaveCount(0);
    await expect(detachedC).toContainText("7 in hand");
    await detachedC.getByRole("button", { name: "Inspect Adeline, Resplendent Cathar" }).focus();
    await expect(boards.getByTestId("card-inspector").last()).toContainText("Adeline, Resplendent Cathar");
    const upper = (await detachedC.boundingBox())!;
    const lower = (await detachedD.boundingBox())!;
    expect(upper.width).toBe(lower.width);
    expect(upper.y + upper.height).toBeLessThanOrEqual(lower.y);
    await boards.close();
    await expect(host.getByTestId("player-C")).toBeVisible();

    const stops = host.locator(".priority-controls > summary");
    await stops.click();
    await host.getByLabel("Priority stop turn").selectOption("D");
    const endStep = host.getByRole("button", { name: "End step", exact: true });
    await endStep.click();
    await expect(endStep).toHaveAttribute("aria-pressed", "true");
    await host.reload();
    await expect(hand.locator(".hand-scroll")).toHaveClass(/art-focused/);
    await expect(host.locator(".game-shell")).toHaveCSS("--accent", "#a9c6ff");
    await stops.click();
    await host.getByLabel("Priority stop turn").selectOption("D");
    await expect(endStep).toHaveAttribute("aria-pressed", "true");
    await stops.click();

    await host.setViewportSize({ width: 650, height: 560 });
    await expect(hand).toBeVisible();
    await expect(host.getByTestId("decision-panel")).toBeVisible();
    expect(await host.evaluate(() => ({
      height: document.documentElement.scrollHeight, width: document.documentElement.scrollWidth,
    }))).toEqual({ height: 560, width: 650 });
    await expect(sidebar).toBeHidden();
    expect((await stackToggle.boundingBox())!.y).toBeLessThan((await battlefield.boundingBox())!.y);
    await expect(passTurn).toBeInViewport({ ratio: 1 });
    const narrow = await measureLayout("constrained-layout");
    expect(narrow.headerHeight).toBeLessThanOrEqual(90);
    expect(narrow.battlefieldTop).toBeLessThanOrEqual(125);
    expect(narrow.battlefieldHeight).toBeGreaterThanOrEqual(205);
    const commander = host.getByTestId("player-A").locator(".command-cards .card-tile");
    const baseCost = commander.locator(".command-card-cost");
    await expect.poll(() => manaCostLayout(baseCost)).toEqual({ symbols: 4, rows: 2, fits: true });
    await expect(commander.locator(".commander-tax")).toHaveCount(0);
    await expect(commander).toHaveAccessibleDescription("Base cost 3 generic mana black mana black mana green mana");
    await commander.screenshot({ path: info.outputPath("wrapped-commander-cost.png"), animations: "disabled" });
    await host.screenshot({ path: info.outputPath("constrained.png"), animations: "disabled" });
    await stackToggle.click();
    await expect(sidebar.getByTestId("stack-panel")).toBeVisible();
    await stackToggle.click();
    await expect(sidebar).toBeHidden();

    await host.setViewportSize({ width: 390, height: 680 });
    await expect(host.getByTestId("open-game-menu")).toBeInViewport({ ratio: 1 });
    await expect(passTurn).toBeInViewport({ ratio: 1 });
    expect(await host.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    const phone = await measureLayout("phone-layout");
    expect(phone.headerHeight).toBeLessThanOrEqual(105);
    expect(phone.battlefieldHeight).toBeGreaterThanOrEqual(260);
    expect(phone.dockHeight).toBeLessThanOrEqual(240);
    await host.screenshot({ path: info.outputPath("phone.png"), animations: "disabled" });
  } finally {
    await Promise.all(contexts.map((browserContext) => browserContext.close()));
  }
});


test("@browser-lifecycle a narrow card wraps the whole casting cost and leaves tax on its own line", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 495 });
  await page.goto("/");
  await page.evaluate(async () => {
    const path = "/tests/support/playtest-surface.tsx";
    (await import(path)).mountPlaytestSurface("costs");
  });
  const commander = page.locator(".command-cards .card-tile");
  const baseCost = commander.locator(".command-card-cost");
  await expect.poll(() => manaCostLayout(baseCost)).toMatchObject({ symbols: 10, fits: true });
  expect((await manaCostLayout(baseCost)).rows).toBeGreaterThan(1);
  const tax = commander.locator(".commander-tax");
  await expect(tax).toHaveText("Tax +4");
  await expect(commander).toHaveAccessibleDescription(/Commander tax 4/);
  expect((await tax.boundingBox())!.y).toBeGreaterThanOrEqual((await baseCost.boundingBox())!.y + (await baseCost.boundingBox())!.height);
  const stats = (await commander.getByTestId("card-current-stats").boundingBox())!;
  expect(stats.y + stats.height).toBeLessThan((await baseCost.boundingBox())!.y);

  const handPrice = page.getByTestId("card-offer-cost");
  await expect.poll(() => manaCostLayout(handPrice)).toMatchObject({ symbols: 10, fits: true });
  expect((await manaCostLayout(handPrice)).rows).toBeGreaterThan(1);
  await commander.screenshot({ path: info.outputPath("long-wrapped-commander-cost.png"), animations: "disabled" });
});

test("@browser-rules pending cascade, controller targets, commander return and printing choices", async ({ page }, info) => {
  const mount = async (mode: string) => {
    await page.goto("/");
    await page.evaluate(async (selection) => {
      const path = "/tests/support/late-playtest-choices.tsx";
      (await import(path)).mountLatePlaytestChoices(selection);
    }, mode);
  };
  await page.setViewportSize({ width: 800, height: 625 });
  await mount("cascade");
  await page.getByRole("button", { name: "Yes, cast", exact: true }).click();
  await expect(page.locator("body")).toHaveAttribute("data-answer", JSON.stringify({
    action: "cast", choices: { cost_option: "without_mana_cost" },
  }));
  await expect(page.getByTestId("submit-choice")).toHaveCount(0);
  await page.getByRole("button", { name: "No, decline", exact: true }).click();
  await expect(page.locator("body")).toHaveAttribute("data-answer", JSON.stringify({ action: "decline", choices: {} }));

  await mount("targeted-cascade");
  await page.getByRole("button", { name: "Yes, choose how to cast", exact: true }).click();
  await expect(page.locator("body")).not.toHaveAttribute("data-answer", /./);
  await expect(page.getByRole("group", { name: "Seat C targets" })).toBeVisible();

  await mount("target");
  const targetsB = page.getByRole("group", { name: "Seat B targets" });
  const targetsC = page.getByRole("group", { name: "Seat C targets" });
  await expect(targetsB).toHaveAttribute("data-seat", "B");
  await expect(targetsC).toHaveAttribute("data-seat", "C");
  await expect(targetsB.getByRole("checkbox")).toHaveCount(1);
  await expect(targetsC.getByRole("checkbox")).toHaveCount(2);
  await targetsC.getByRole("checkbox", { name: "Arcane Signet" }).focus();
  await page.keyboard.press("Space");
  await page.getByTestId("submit-choice").click();
  await expect(page.locator("body")).toHaveAttribute("data-answer", JSON.stringify({
    action: "choose", choices: { targets: { permanent: ["C1"] } },
  }));
  await page.screenshot({ path: info.outputPath("targets-by-seat.png") });

  await mount("commander");
  const destinations = page.getByTestId("choice-replacement");
  await expect(destinations.locator("option")).toHaveText(["Move to command zone", "Return to hand"]);
  await destinations.selectOption({ label: "Return to hand" });
  await page.getByTestId("submit-choice").click();
  await expect(page.locator("body")).toHaveAttribute("data-answer", JSON.stringify({
    action: "choose", choices: { replacement: "decline" },
  }));

  // A synthetic long printing label exercises wrapping and the same URL
  // resolution that the imported collector number uses in the viewer.
  await page.route("**/api/v1/cards/**", (route) => route.fulfill({
    contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="63" height="88"/>',
  }));
  await mount("printing");
  const result = page.getByRole("button", { name: /Temple Garden, J22 #835/ });
  await result.focus();
  await expect(result.locator("small")).toHaveText("J22 #835 · Traditional Chinese · Etched foil");
  await expect(result.locator("img")).toHaveAttribute("src", /edition=j22&collector=835&language=zht/);
  const inspector = page.getByTestId("card-inspector");
  await expect(inspector).toContainText("J22 #835 · Traditional Chinese · Etched foil");
  await expect(inspector.locator("img")).toHaveAttribute("src", /edition=j22&collector=835&language=zht/);
  const label = result.locator("small");
  expect((await label.boundingBox())!.y + (await label.boundingBox())!.height)
    .toBeLessThanOrEqual((await result.boundingBox())!.y + (await result.boundingBox())!.height);
  await result.press("Space");
  await expect(result).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: info.outputPath("printing-picker.png") });

  await page.goto("/");
  await page.evaluate(async () => {
    const path = "/tests/support/playtest-surface.tsx";
    (await import(path)).mountPlaytestSurface("board");
  });
  const lands = page.getByRole("group", { name: "Lands", exact: true });
  const staggered = lands.locator(".same-land-mana");
  await expect(staggered).toContainText("Snow-Covered Forest");
  expect(await staggered.evaluate((node) => parseFloat(getComputedStyle(node).marginInlineStart))).toBeLessThan(0);
  await lands.getByRole("button", { name: "Show all 2 Forest cards" }).click();
  const forests = lands.getByRole("button", { name: "Inspect Forest", exact: true });
  await expect(forests).toHaveCount(2);
  const preview = page.locator(".floating-card-preview");
  expect(await forests.first().getAttribute("data-card-ref")).not.toEqual(await forests.last().getAttribute("data-card-ref"));
  await forests.first().hover({ timeout: 10_000 });
  await expect(preview).toHaveCount(1);
  await forests.last().focus();
  await expect(preview).toHaveCount(1);
  await expect(preview).toContainText("Forest");
  await expect(page.getByTestId("plus-counters")).toHaveText("3 × +1/+1");
  await expect(page.getByTestId("card-current-stats")).toHaveText("5/6");
  await page.screenshot({ path: info.outputPath("battlefield-groups.png") });
});


test("@browser-rules an up-to-X cast sends creatures selected on the table and in the target list", async ({ browser }, info) => {
  test.setTimeout(150_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Zimone and Dina", cards: "1 Ornithopter\n1 Memnite\n1 Echo Ledger\n1 Veil Current\n95 Island" },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
  ], 97249);
  const [host] = pages;
  try {
    const land = host.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').first();
    await driveTo(pages, land, info, "Find the blue mana source");
    await clickMove(host, land);
    for (const name of ["Ornithopter", "Memnite"]) {
      const offer = castOffer(host, name);
      await driveTo(pages, offer, info, `Offer ${name}`);
      await clickMove(host, offer);
      await driveTo(pages, boardCard(host, name), info, `Resolve ${name}`);
    }

    const offer = castOffer(host, "Veil Current");
    await driveTo(pages, offer, info, "Offer the up-to-X phasing spell");
    await offer.click();
    const draft = host.getByTestId("public-action-draft");
    const first = boardCard(host, "Ornithopter");
    const second = boardCard(host, "Memnite");
    const submit = draft.getByTestId("submit-choice");
    const x = draft.getByTestId("choice-x");
    const targetStatus = draft.getByTestId("x-target-status");
    const chooseFromList = draft.getByRole("button", { name: "Choose from list" });
    await expect(x).toHaveValue("0");
    await expect(draft).toContainText("Set X above 0 to select targets");
    await expect(first).not.toHaveAttribute("aria-label", /^Select /);
    await expect(submit).toHaveText("Cast with no targets");
    await draft.getByRole("checkbox", { name: "Echo Ledger", exact: true }).check();
    await expect(x).toHaveAttribute("max", "2");
    await x.fill("2");
    await expect(targetStatus).toContainText("Choose up to 2 targets");
    await expect(targetStatus).toContainText("0 selected");
    await expect(submit).toHaveText("Cast with no targets");
    await x.press("Enter");
    await expect(chooseFromList).toBeFocused();
    await first.focus();
    await host.keyboard.press("Space");
    await expect(targetStatus).toContainText("1 selected");

    await host.setViewportSize({ width: 650, height: 560 });
    await host.getByRole("group", { name: "Battlefield views" }).getByRole("button", { name: "Seat B", exact: true }).click();
    await expect(first).toBeHidden();
    await expect(targetStatus).toBeInViewport({ ratio: 1 });
    await chooseFromList.click();
    const picker = host.getByRole("dialog", { name: "Choose targets" });
    const seatA = picker.getByRole("group", { name: "Seat A targets" });
    await expect(seatA.getByRole("checkbox", { name: "Ornithopter" })).toBeChecked();
    const memnite = seatA.getByRole("checkbox", { name: "Memnite" });
    await memnite.focus();
    await host.keyboard.press("Space");
    await expect(memnite).toBeChecked();
    await expect(picker.getByRole("status")).toHaveText("2 of 2 selected");
    await host.screenshot({ path: info.outputPath("up-to-x-target-picker.png") });
    await host.keyboard.press("Escape");
    await expect(picker).toBeHidden();
    await expect(chooseFromList).toBeFocused();
    await expect(targetStatus).toContainText("2 selected");
    await expect(targetStatus).toBeInViewport({ ratio: 1 });
    await expect(submit).toHaveText("Cast spell");
    await host.getByRole("button", { name: "Your board", exact: true }).click();
    await expect(second).toHaveAttribute("aria-pressed", "true");
    await expect.poll(async () => (await first.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(24);
    await expect(first).toBeInViewport({ ratio: .9 });
    await x.fill("0");
    await expect(first).toHaveAttribute("aria-pressed", "true");
    await first.click();
    await second.click();
    await expect(submit).toHaveText("Cast with no targets");
    await x.fill("2");
    await first.click();
    await second.click();
    await expect(targetStatus).toContainText("2 selected");
    await submit.focus();
    await expect(targetStatus).toBeInViewport({ ratio: 1 });
    await host.screenshot({ path: info.outputPath("up-to-x-targets.png") });
    const group = await Promise.all([first, second].map((card) => card.getAttribute("data-card-ref")));
    expect(group.every(Boolean)).toBeTruthy();

    const cast = host.waitForResponse((response) => response.request().method() === "POST"
      && response.url().endsWith("/commands") && response.request().postDataJSON().action_id.startsWith("cast:"));
    await submit.click();
    const response = await cast;
    expect(response.request().postDataJSON().choices).toMatchObject({
      x: 2, targets: { target_0: group },
    });
    expect((await response.json()).receipt.code).toBe("accepted");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
