import { expect, test } from "@playwright/test";
import { boardCard, castOffer, clickMove, driveTo, evidence, projectedState, startPlaytestPod } from "./support/playtest";

for (const payLife of [true, false]) {
  test(`@browser-rules Arid Mesa previews eligible lands and ${payLife ? "pays for untapped" : "accepts tapped"} entry`, async ({ browser }, info) => {
    const decks = [
      { commander: "The First Sliver", cards: "1 Arid Mesa\n1 Steam Vents\n1 Plains\n96 Forest" },
      { commander: "Yargle and Multani", cards: "99 Forest" },
    ];
    const { pages, contexts } = await startPlaytestPod(browser, decks, 1, { width: 1460, height: 900 });
    const [host] = pages;
    try {
      const play = host.getByTestId("own-hand").getByRole("button", { name: "Play Arid Mesa", exact: true });
      await driveTo(pages, play, info, "Offer Arid Mesa from the opening hand");
      if (payLife) {
        await play.hover();
        await host.keyboard.press("Alt");
        const pinned = host.getByRole("complementary", { name: /Pinned card viewer.*Arid Mesa/ });
        await expect(pinned).toBeVisible();
        await host.mouse.move(3, 3);
        await expect(pinned.locator(".inspector-text")).toHaveCSS("user-select", "text");
        await expect(pinned).toContainText("Arid Mesa");
        await host.keyboard.press("Alt");
        await expect(pinned).toHaveCount(0);
      }
      await clickMove(host, play);
      const mesa = boardCard(host, "Arid Mesa").first();
      await mesa.click();
      const review = host.getByTestId("submit-choice");
      if (await review.isVisible()) await clickMove(host, review);
      const selection = host.getByTestId("contextual-decision");
      await driveTo(pages, selection.getByRole("button", { name: "Steam Vents", exact: true }), info, "Resolve Arid Mesa");
      const plains = selection.getByRole("button", { name: "Plains", exact: true });
      const steamVents = selection.getByRole("button", { name: "Steam Vents", exact: true });
      await expect(selection.getByRole("button", { name: "Forest", exact: true })).toHaveCount(0);
      if (payLife) {
        const filter = selection.getByRole("searchbox", { name: "Filter cards by name" });
        await filter.fill("No eligible card with this name");
        await filter.press("Enter");
        await expect(selection).toContainText("No eligible cards match this search.");
        await filter.fill("steam");
        await expect(plains).toHaveCount(0);
        await steamVents.click();
        await selection.getByRole("button", { name: "Expand choice", exact: true }).click();
        const expanded = host.getByRole("dialog");
        await expect(expanded).toBeVisible();
        await expect(expanded.getByRole("button", { name: "Steam Vents", exact: true })).toHaveAttribute("aria-pressed", "true");
        await expanded.getByRole("button", { name: "Return to table", exact: true }).click();
        await expect(steamVents).toHaveAttribute("aria-pressed", "true");
      }
      await plains.click();
      await expect(selection.getByRole("button", { name: /pay 2 life/i })).toHaveCount(0);
      await steamVents.click();
      await expect(selection.getByTestId("card-inspector")).toContainText(/Island Mountain/);
      await selection.getByTestId("submit-choice").click();
      await expect(selection.getByRole("heading", { name: "How should Steam Vents enter?" })).toBeVisible();
      await selection.getByRole("button", { name: "Choose another land" }).click();
      await plains.click();
      await expect(selection.getByRole("button", { name: /pay 2 life/i })).toHaveCount(0);
      await steamVents.click();
      await selection.getByTestId("submit-choice").click();
      await clickMove(host, selection.getByRole("button", {
        name: payLife ? "Pay 2 life · Enter untapped" : "Enter tapped", exact: true,
      }));
      await expect.poll(async () => {
        const state = await projectedState(host);
        return { life: state.players.A.life, tapped: state.players.A.bf.find((card: { n: string }) => card.n === "Steam Vents")?.tap ?? 0 };
      }).toEqual(payLife ? { life: 37, tapped: 0 } : { life: 39, tapped: 1 });
      const activity = host.getByTestId("activity-panel");
      await expect(activity).toContainText("Arid Mesa");
      await activity.getByRole("button", { name: "Arid Mesa", exact: true }).first().hover();
      await expect(host.getByRole("region", { name: "Card viewer", exact: true })).toContainText("Arid Mesa");
    } finally {
      await Promise.all(contexts.map((context) => context.close()));
    }
  });
}

test("@browser-rules a recorded stack loop waits for an opponent and stops on their response", async ({ browser }, info) => {
  test.setTimeout(100_000);
  const decks = [
    { commander: "The First Sliver", cards: "1 Shuko\n1 Llanowar Elves\n97 Forest" },
    { commander: "Yargle and Multani", cards: "1 Nature's Claim\n98 Forest" },
  ];
  const { pages, contexts } = await startPlaytestPod(browser, decks, 2064, { width: 1460, height: 900 });
  const [host, opponent] = pages;
  for (const page of pages) page.setDefaultTimeout(12_000);
  try {
    const forest = (page: typeof host) => page.getByTestId("own-hand").getByRole("button", { name: "Play Forest", exact: true }).first();
    const elves = castOffer(host, "Llanowar Elves");
    await driveTo(pages, forest(host), info, "Play the first Forest");
    await clickMove(host, forest(host));
    await driveTo(pages, elves, info, "Cast Llanowar Elves");
    await clickMove(host, elves);
    await driveTo(pages, forest(opponent), info, "Give the opponent a Forest");
    await clickMove(opponent, forest(opponent));
    await driveTo(pages, forest(host), info, "Play the second Forest");
    await clickMove(host, forest(host));
    const shuko = castOffer(host, "Shuko");
    await driveTo(pages, shuko, info, "Cast Shuko");
    await clickMove(host, shuko);

    const equip = host.getByTestId("decision-panel").locator('[data-testid^="action-activate:"]').filter({ hasText: /equip/i }).first();
    const attachmentGroup = host.getByRole("group", { name: "Attached to Llanowar Elves", exact: true });
    const visibleAttachment = attachmentGroup.locator(".card-tile");
    async function useEquip() {
      await equip.click();
      await host.getByRole("button", { name: /Select Llanowar Elves/ }).first().click();
      await clickMove(host, host.getByTestId("submit-choice"));
    }

    await driveTo(pages, equip, info, "Offer Shuko equip");
    await useEquip();
    await driveTo(pages, equip, info, "Finish the initial equip");
    await expect(visibleAttachment).toBeVisible();
    await visibleAttachment.hover();
    await expect(host.getByRole("region", { name: "Card viewer", exact: true })).toContainText("Shuko");
    const parentRect = (await boardCard(host, "Llanowar Elves").first().boundingBox())!;
    const attachedRect = (await visibleAttachment.boundingBox())!;
    expect(attachedRect.x).toBeGreaterThanOrEqual(parentRect.x + parentRect.width);
    expect(attachedRect.y).toBeLessThan(parentRect.y + parentRect.height);
    await expect(host.locator(".has-attached-cards .attachment-connector")).toBeVisible();
    await visibleAttachment.focus();
    await expect(visibleAttachment).toBeFocused();
    await expect(host.getByRole("region", { name: "Card viewer", exact: true })).toContainText("Shuko");
    await evidence(host, info, "equipment-beside-creature");

    const loop = host.locator(".loop-controls");
    await loop.locator("summary").click();
    await loop.getByRole("button", { name: "Record loop" }).click();
    await useEquip();
    const ownPass = host.getByTestId("action-pass");
    await clickMove(host, ownPass);
    const response = castOffer(opponent, "Nature's Claim");
    await expect(response).toBeVisible();
    await clickMove(opponent, opponent.getByTestId("action-pass"));
    await driveTo(pages, loop.locator("summary").filter({ hasText: "Ready" }), info, "Return to the original priority window");
    await loop.locator("summary").click();
    await loop.getByRole("spinbutton").fill("2");
    await loop.getByRole("button", { name: "Repeat recorded loop" }).click();
    await expect(response).toBeVisible();
    await expect(loop.getByRole("status")).toContainText("Waiting for priority to return");
    await response.click();
    await opponent.getByRole("button", { name: /Select Shuko/ }).first().click();
    await clickMove(opponent, opponent.getByTestId("submit-choice"));
    await expect(loop.getByText("Loop stopped because another action or decision changed the sequence.")).toBeVisible();
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

test("@browser-rules trigger cards reorder by resolution and graveyard cards fill a second row", async ({ page }, info) => {
  await page.setViewportSize({ width: 1460, height: 900 });
  const mount = async (mode: string) => {
    await page.goto("/");
    await page.evaluate(async (mode) => {
      const path = "/tests/support/playtest-surface.tsx";
      (await import(path)).mountPlaytestSurface(mode);
    }, mode);
  };

  await mount("trigger");
  const rows = page.locator(".trigger-order-cards > li");
  await expect(rows.first()).toContainText("Resolves first");
  await expect(rows.first()).toContainText("Create a token");
  await expect(rows.last()).toContainText("Resolves last");
  await expect(rows.first().locator(".trigger-order-position")).toContainText("1");
  await expect(rows.first().locator("img")).toHaveAttribute("src", /\/cards\/second\/image\?size=small/);
  await rows.last().getByRole("button", { name: "Move trigger 2 earlier in resolution" }).click();
  await expect(rows.first()).toContainText("Draw a card");
  await page.getByTestId("submit-choice").click();
  await expect(page.locator("body")).toHaveAttribute("data-submitted-order", JSON.stringify(["S2", "S1"]));
  await evidence(page, info, "trigger-order");

  await mount("combat");
  await expect(page.getByRole("list", { name: "Combat steps" }).locator('[aria-current="step"]')).toHaveText("First-strike damage");

  await mount("graveyard");
  const graveyard = page.getByRole("group", { name: "Graveyard", exact: true });
  const tiles = graveyard.locator(".zone-card-grid > .card-tile");
  await expect.poll(() => tiles.evaluateAll((cards) => new Set(cards.map((card) => card.getBoundingClientRect().top)).size)).toBeGreaterThan(1);
  await expect(graveyard.getByRole("button", { name: "Next Graveyard page" })).toBeEnabled();
  await evidence(page, info, "graveyard-two-rows");

  await mount("board");
  const lands = page.getByRole("group", { name: "Lands", exact: true });
  await expect(lands.getByRole("button", { name: "Show all 2 Forest cards" })).toBeVisible();
  const sharedMana = lands.locator(".same-land-mana");
  await expect(sharedMana).toHaveCount(1);
  expect(await sharedMana.evaluate((node) => parseFloat(getComputedStyle(node).marginInlineStart))).toBeLessThan(0);

  await mount("bulk");
  await page.getByRole("button", { name: "Assign all available attackers to Seat A, 40 life" }).click();
  await expect(page.getByText("4 of 30004 attackers unassigned")).toBeVisible();
  await page.getByTestId("submit-choice").click();
  await expect(page.locator("body")).toHaveAttribute("data-assigned", "30000");

  await mount("priority");
  await page.getByText("Priority stops", { exact: true }).click();
  const skip = page.getByRole("checkbox", { name: /Skip unchanged this game.*Emry/ });
  const advance = page.getByRole("button", { name: "Next priority window" });
  const passes = page.getByLabel("Automatic passes");
  await expect(passes).toHaveText("0");
  await skip.check();
  await expect(passes).toHaveText("1");
  await advance.click();
  await expect(passes).toHaveText("2");
  await advance.click();
  await expect(skip).not.toBeChecked();
  await expect(passes).toHaveText("2");
});


test("@browser-rules entry designation starts with deck types and searches the full catalog", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const path = "/tests/support/playtest-surface.tsx";
    (await import(path)).mountPlaytestSurface("entry-type");
  });
  const choice = page.getByTestId("contextual-decision");
  await expect(choice.getByRole("radio", { name: "Elf", exact: true })).toBeVisible();
  await expect(choice.getByRole("radio", { name: "Brushwagg", exact: true })).toHaveCount(0);
  await expect(choice).not.toContainText("A87");
  await choice.getByRole("searchbox", { name: "Search all creature types" }).fill("brush");
  await choice.getByRole("radio", { name: "Brushwagg", exact: true }).check();
  await choice.getByTestId("submit-choice").click();
  await expect(page.locator("body")).toHaveAttribute("data-chosen-type", "A87:brushwagg");
});
