import { expect, test, type ElementHandle, type Page } from "@playwright/test";
import { driveUntil, viewRevision } from "./support/progress";
import { boardCard, castOffer, clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";
import { captureRealZoom, zoomContext } from "./support/browserZoom";

const mishra = "Mishra, Eminent One";
const yargle = "Yargle and Multani";
const plainDeck = { commander: yargle, cards: "99 Swamp" };

async function cast(pages: readonly Page[], page: Page, name: string, info: Parameters<typeof driveTo>[2]) {
  const action = castOffer(page, name);
  await driveTo(pages, action, info, `Cast ${name}`);
  const revision = await viewRevision(page);
  await action.click();
  const dialog = page.getByTestId("choice-dialog");
  await expect.poll(async () => (await viewRevision(page)) > revision || await dialog.isVisible()).toBe(true);
  if (await dialog.isVisible()) await clickMove(page, dialog.getByTestId("submit-choice"));
}

async function playLand(pages: readonly Page[], page: Page, name: string, info: Parameters<typeof driveTo>[2]) {
  const action = page.getByTestId("decision-panel").locator('[data-testid^="action-play-land:"]').filter({ hasText: name }).first();
  await driveTo(pages, action, info, `Play ${name}`);
  await clickMove(page, action);
}

test("@browser-rules @mana-action contextual Solemn decision and repeated-name search", async ({ browser }, info) => {
  test.setTimeout(300_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: mishra, cards: [
      "1 Sol Ring", "1 Dark Ritual", "1 Solemn Simulacrum",
      "4 Swamp (brc) 33", "4 Swamp (m21) 268", "2 Swamp (brc) 33",
      "18 Relentless Rats", "5 Mountain (brc) 35", "11 Relentless Rats",
      "3 Mountain (brc) 36", "49 Relentless Rats",
    ].join("\n") },
    plainDeck, plainDeck, plainDeck,
  ], 6710);
  const [host, ...opponents] = pages;
  try {
    await playLand(pages, host, "Swamp", info);
    await cast(pages, host, "Dark Ritual", info);
    await cast(pages, host, "Sol Ring", info);
    await cast(pages, host, "Solemn Simulacrum", info);
    const optional = host.getByRole("form", { name: "Search for a basic land?" });
    const search = optional.getByRole("button", { name: "Search", exact: true });
    await driveTo(pages, search, info, "Resolve Solemn’s optional trigger");
    await expect(optional).toContainText("Solemn Simulacrum");
    await expect(optional).toContainText("enters the battlefield tapped");
    await expect(optional.getByRole("button", { name: "Decline" })).toBeVisible();
    await evidence(host, info, "solemn-optional-search");
    const opposingImageRequests: string[] = [];
    for (const page of opponents) page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/image")) opposingImageRequests.push(request.url());
    });
    await clickMove(host, search);

    const choices = host.getByRole("form", { name: "Choose a basic land" });
    await expect(choices.getByRole("button", { name: "Find no card" })).toBeVisible();
    const collection = choices.locator(".card-choice-options");
    const previous = choices.getByRole("button", { name: "Previous Search results page" });
    const next = choices.getByRole("button", { name: "Next Search results page" });
    async function pageBack() {
      while (await previous.isVisible() && await previous.getAttribute("aria-disabled") !== "true") await previous.click();
    }
    async function searchOnPages(name: RegExp) {
      await pageBack();
      const tile = choices.getByRole("button", { name }).first();
      while (!await tile.isVisible() && await next.isVisible() && await next.getAttribute("aria-disabled") !== "true") {
        await next.click();
      }
      await expect(tile).toBeVisible();
      return tile;
    }
    const names = new Set<string | null>();
    for (let page = 0; page < 14; page++) {
      for (const title of await collection.getByRole("button").evaluateAll((buttons) =>
        buttons.map((button) => button.getAttribute("aria-label")))) names.add(title);
      if (!await next.isVisible() || await next.getAttribute("aria-disabled") === "true") break;
      await next.click();
    }
    expect([...names].filter((name) => name?.startsWith("Swamp, Copy"))).toHaveLength(8);
    expect([...names].filter((name) => name?.startsWith("Mountain, Copy"))).toHaveLength(6);
    const selected = await searchOnPages(/^Swamp, Copy 2 of 8/);
    const identity = await selected.getAttribute("data-testid");
    expect(identity).toBeTruthy();
    for (const page of opponents) await expect(page.getByTestId(identity!)).toHaveCount(0);
    await selected.hover();
    await expect(choices.locator(".card-choice-preview")).toContainText("Swamp");
    const printingImages: (string | null)[] = [];
    let previousArt: ElementHandle | null = null;
    for (const edition of [/BRC #33/, /M21 #268/]) {
      const print = await searchOnPages(new RegExp(`^Swamp.*${edition.source}`));
      await print.hover();
      await expect(choices.locator(".card-choice-preview")).toContainText(edition);
      if (process.env.QUORUNE_TEST_LIVE_ART === "1") {
        const image = choices.locator(".card-choice-preview .inspector-art img");
        await expect.poll(() => image.evaluate((item: HTMLImageElement) => item.naturalWidth)).toBeGreaterThan(0);
        if (previousArt) expect(await previousArt.evaluate((item) => item.isConnected)).toBe(false);
        previousArt = await image.elementHandle();
        printingImages.push(await image.getAttribute("src"));
      }
    }
    if (printingImages.length) expect(printingImages[0]).not.toEqual(printingImages[1]);
    expect(opposingImageRequests.some((url) => {
      const params = new URL(url).searchParams;
      return params.get("edition") === "brc" && ["35", "36"].includes(params.get("collector") ?? "");
    })).toBe(false);
    await searchOnPages(/^Swamp, Copy 2 of 8/);
    await selected.click();
    await expect(selected).toHaveAttribute("aria-pressed", "true");
    await expect(choices).toContainText("1 selected");
    const confirm = choices.getByRole("button", { name: "Put onto battlefield" });
    await expect(confirm).toBeInViewport({ ratio: 1 });
    await evidence(host, info, "solemn-visual-search-repeated-basics");
    await clickMove(host, confirm);
    const swamp = boardCard(host, "Swamp");
    await expect(swamp).toBeVisible();
    await expect(swamp).toHaveAttribute("data-tapped", "true");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-rules @mana-action declining Solemn's search still leaves a distinct optional death draw", async ({ browser }, info) => {
  test.setTimeout(360_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: mishra, cards: "1 Sol Ring\n1 Dark Ritual\n1 Solemn Simulacrum\n96 Swamp" },
    { commander: yargle, cards: "20 Swamp\n1 Murder\n32 Swamp\n1 Dark Ritual\n45 Swamp" },
    plainDeck, plainDeck,
  ], 6710);
  const [host, opponent] = pages;
  try {
    await playLand(pages, host, "Swamp", info);
    await cast(pages, host, "Dark Ritual", info);
    await cast(pages, host, "Sol Ring", info);
    await cast(pages, host, "Solemn Simulacrum", info);
    const offer = host.getByRole("form", { name: "Search for a basic land?" });
    const decline = offer.getByRole("button", { name: "Decline", exact: true });
    await driveTo(pages, decline, info, "Solemn’s optional entry search");
    await clickMove(host, decline);
    await expect(host.getByRole("form", { name: "Choose a basic land" })).toHaveCount(0);
    await expect(boardCard(host, "Swamp")).toHaveCount(1);
    await playLand(pages, opponent, "Swamp", info);
    await cast(pages, opponent, "Dark Ritual", info);
    const murder = castOffer(opponent, "Murder");
    await driveTo(pages, murder, info, "Offer Murder to the active opponent");
    const solemn = opponent.getByTestId("player-A").locator(".battlefield .card-tile").filter({ hasText: "Solemn Simulacrum" });
    const ref = await solemn.getAttribute("data-card-ref");
    await murder.click();
    const draft = opponent.getByTestId("public-action-draft");
    if (await draft.isVisible()) await solemn.click();
    else await opponent.getByTestId(`choice-target-${ref}`).check();
    await clickMove(opponent, opponent.getByTestId("submit-choice"));
    const draw = host.getByRole("form", { name: "Draw a card?" });
    const accept = draw.getByRole("button", { name: "Draw", exact: true });
    await driveTo(pages, accept, info, "Resolve Solemn’s optional death draw");
    await expect(draw).toContainText("Solemn Simulacrum");
    await expect(draw.getByRole("button", { name: "Decline", exact: true })).toBeVisible();
    const handBefore = await host.getByTestId("own-hand").locator(".card-tile").count();
    await evidence(host, info, "solemn-optional-death-draw");
    await clickMove(host, accept);
    await expect(host.getByTestId("own-hand").locator(".card-tile")).toHaveCount(handBefore + 1);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-rules @combat two distinct blockers group under the same attacker", async ({ browser }, info) => {
  test.setTimeout(360_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: yargle, cards: "1 Ornithopter\n1 Memnite\n97 Swamp" },
    { commander: mishra, cards: "1 Raging Goblin\n98 Mountain" },
    plainDeck, plainDeck,
  ], 2064, undefined, await zoomContext(browser, info));
  const [host, attacker] = pages;
  try {
    await cast(pages, host, "Ornithopter", info);
    await cast(pages, host, "Memnite", info);
    await expect(boardCard(host, "Memnite")).toBeVisible();
    const layout = await host.evaluate(() => ({
      width: innerWidth, height: innerHeight,
      scrollHeight: document.documentElement.scrollHeight,
      seats: [...document.querySelectorAll(".player-board")].map((row) => row.getBoundingClientRect().toJSON()),
    }));
    expect([layout.width, layout.height]).toEqual([1366, 650]);
    expect(layout.scrollHeight).toBe(layout.height);
    expect(layout.seats).toHaveLength(4);
    expect(layout.seats[3].width).toBeGreaterThan(layout.seats[0].width * 2);
    expect(layout.seats[0].y).toBe(layout.seats[1].y);
    expect(layout.seats[1].y).toBe(layout.seats[2].y);
    await expect(boardCard(host, "Ornithopter").getByTestId("card-current-stats")).toHaveText("0/2");
    await boardCard(host, "Ornithopter").hover();
    await expect(host.getByTestId("hover-card-preview")).toContainText("Current 0/2");
    await evidence(host, info, "populated-table-and-hover-preview");
    await captureRealZoom(host, info);
    await host.getByRole("button", { name: "Arrange", exact: true }).click();
    let layoutCommands = 0;
    const countCommands = (request: { method(): string; url(): string }) => {
      if (request.method() === "POST" && new URL(request.url()).pathname.endsWith("/commands")) layoutCommands++;
    };
    host.on("request", countCommands);
    await host.getByRole("button", { name: "Move Memnite earlier" }).click();
    await expect(host.getByTestId("own-battlefield").locator(".card-tile").first()).toContainText("Memnite");
    await host.getByRole("button", { name: "Reset layout" }).click();
    await host.getByRole("button", { name: "Done arranging" }).click();
    host.off("request", countCommands);
    expect(layoutCommands).toBe(0);
    await playLand(pages, attacker, "Mountain", info);
    await cast(pages, attacker, "Raging Goblin", info);
    const attack = attacker.getByRole("form", { name: "Declare attackers" });
    const goblin = attack.getByRole("button", { name: "Select Raging Goblin" });
    await driveTo(pages, goblin, info, "Reach the attacking seat's declaration");
    await goblin.click();
    await attack.getByRole("button", { name: "Assign to Seat A, 40 life" }).click();
    await expect(attack.locator(".combat-connections")).toContainText("Raging Goblin");
    await evidence(attacker, info, "attack-staged-to-seat-a");
    await clickMove(attacker, attack.getByRole("button", { name: "Confirm attackers" }));

    const block = host.getByRole("form", { name: "Select blockers" });
    const ornithopter = block.getByRole("button", { name: "Select Ornithopter" });
    const memnite = block.getByRole("button", { name: "Select Memnite" });
    const incoming = block.getByRole("button", { name: "Assign to Raging Goblin" });
    const group = block.locator(".combat-connections");
    await driveTo(pages, ornithopter, info, "Reach the defending seat's declaration");
    await expect(block).toContainText("Defending seat A");
    await expect(block.getByRole("button", { name: "Confirm no blockers", exact: true })).toBeVisible();
    await expect(block.locator("[data-blocked=false]")).toContainText("Unblocked");
    await expect(host.getByTestId("target-arrow-layer")).toHaveAttribute("data-attack-arrow-count", "1");
    await expect(attacker.getByTestId("target-arrow-layer")).toHaveAttribute("data-attack-arrow-count", "1");
    await ornithopter.click();
    await incoming.click();
    await block.getByRole("button", { name: "Expand choice", exact: true }).click();
    await expect(group).toContainText("Ornithopter");
    const bank = block.getByRole("group", { name: "Eligible blockers", exact: true });
    async function revealBlocker(button: typeof memnite) {
      if (!(await button.isVisible())) {
        const next = bank.getByRole("button", { name: "Next Eligible blockers page" });
        if (await next.isVisible() && await next.isEnabled()) await next.click();
        else await bank.getByRole("button", { name: "Previous Eligible blockers page" }).click();
      }
      await expect(button).toBeVisible();
    }
    await revealBlocker(memnite);
    await memnite.dragTo(incoming, { timeout: 15_000 });
    await expect(group.getByRole("button")).toHaveCount(2);
    await expect(block.locator("[data-blocked=true]")).toContainText("Blocked");
    await group.getByRole("button", { name: /Remove Memnite/ }).click();
    await expect(group.getByRole("button")).toHaveCount(1);
    const revision = await viewRevision(host);
    await block.getByRole("button", { name: "Clear assignments" }).click();
    await expect(group).toHaveCount(0);
    expect(await viewRevision(host)).toBe(revision);
    await revealBlocker(ornithopter);
    await ornithopter.click();
    await incoming.click();
    await revealBlocker(memnite);
    await memnite.click();
    await incoming.click();
    await expect(group.getByRole("button")).toHaveCount(2);
    await evidence(host, info, "two-blockers-one-attacker");
    await clickMove(host, block.getByRole("button", { name: "Confirm blockers" }));
    const flyer = boardCard(host, "Ornithopter");
    const marked = flyer.getByTestId("card-marked-damage");
    let damageReadoutSeen = false;
    await driveUntil(pages, () => marked.isVisible(), info, {
      label: "Resolve the assigned combat damage",
      advance: async () => {
        const choice = attacker.locator('[data-testid="decision-panel"][data-decision-kind="combat.damage"]')
          .getByTestId("contextual-decision");
        const commit = choice.getByTestId("submit-choice");
        if (!(await commit.isVisible()) || !(await commit.isEnabled({ timeout: 250 }).catch(() => false))) return false;
        const source = choice.locator(".damage-source").filter({ hasText: "Raging Goblin" });
        const assigned = source.getByRole("status");
        const ornithopterDamage = source.getByRole("spinbutton", { name: "Ornithopter" });
        const memniteDamage = source.getByRole("spinbutton", { name: "Memnite" });
        await ornithopterDamage.fill("0");
        await memniteDamage.fill("0");
        await expect(assigned).toHaveText("0 assigned · 1 remaining");
        await ornithopterDamage.fill("1");
        await memniteDamage.fill("1");
        await expect(assigned).toHaveText("2 assigned · 1 over the limit");
        await memniteDamage.fill("0");
        await expect(assigned).toHaveText("1 assigned · 0 remaining");
        await evidence(attacker, info, "combat-damage-assigned-and-remaining");
        damageReadoutSeen = true;
        await clickMove(attacker, commit);
        return true;
      },
    });
    expect(damageReadoutSeen).toBe(true);
    await expect(marked).toHaveText("1 dmg");
    await expect(flyer.getByTestId("card-current-stats")).toHaveText("0/2");
    await flyer.hover();
    await expect(host.getByTestId("hover-card-preview")).toContainText("Current 0/2");
    await expect(host.getByTestId("hover-card-preview")).toContainText("1 damage marked");
    await evidence(host, info, "marked-damage-with-current-stats");
    await driveUntil(pages, async () => !(await marked.isVisible()), info, { label: "Reach cleanup" });
    await expect(flyer.getByTestId("card-current-stats")).toHaveText("0/2");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
