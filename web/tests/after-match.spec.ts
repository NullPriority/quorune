import { expect, test } from "@playwright/test";
import { clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";
import { openGameMenu, startRoomGame } from "./support/startup";

test("@browser-lifecycle visually editing a saved deck keeps its tags and chosen printing", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("display-name").fill("Deck editor playtester");
  await page.getByTestId("create-guest").click();
  await page.getByTestId("create-room").click();
  await page.getByTestId("deck-name").fill("Original editor list");
  await page.getByTestId("commander-name").fill("Krenko, Tin Street Kingpin");
  await page.getByTestId("deck-list").fill("Commander:\n1 Krenko, Tin Street Kingpin\nMainboard:\n99 Mountain");
  await page.getByTestId("submit-deck").click();
  await page.getByTestId("unready-deck").click();
  await page.getByRole("button", { name: "Edit visually", exact: true }).click();
  const editor = page.getByRole("region", { name: "Visual deck editor" });
  await editor.getByRole("button", { name: /99 × Mountain/ }).click();
  await editor.getByRole("textbox", { name: "Tags", exact: true }).fill("Mana, Terrain");
  await editor.getByLabel("Order cards").selectOption("tag");
  await editor.getByLabel("Filter cards and tags").fill("Terrain");
  await expect(editor.getByRole("button", { name: /99 × Mountain/ })).toHaveCount(1);
  const printing = { set_code: "dmu", collector_number: "272", language: "en", scryfall_id: "00000000-0000-4891-8060-00000000da01" };
  await page.route("**/api/v1/me/deck-editor/printings?*", (route) => route.fulfill({ json: {
    name: "Mountain", cid: "ac0628b9", has_more: false,
    printings: [{ printing, set_name: "Dominaria United", finishes: ["foil"] }],
  } }));
  await editor.getByRole("button", { name: "Find printings", exact: true }).click();
  await editor.getByLabel("Choose printing").selectOption(printing.scryfall_id);
  await expect(editor).toContainText("DMU #272");
  await page.getByTestId("deck-name").fill("Edited visual list");
  const updated = page.waitForResponse((response) => response.url().endsWith("/deck") && response.request().method() === "PUT");
  await page.getByTestId("submit-deck").click();
  const result = await updated;
  expect(result.ok()).toBe(true);
  const saved = (await result.json()).deck;
  const edited = await page.evaluate(async (id) => {
    const response = await fetch(`/api/v1/me/decks/${id}`, {
      headers: { "X-Commander-Tab": sessionStorage.getItem("commander-browser-tab") ?? "" },
    });
    if (!response.ok) throw new Error(`Could not read the saved deck: ${response.status}`);
    return (await response.json()).deck;
  }, saved.deck_id);
  expect(edited.entries.find((entry: { name: string }) => entry.name === "Mountain")).toMatchObject({
    tags: ["Mana", "Terrain"], printing: { ...printing, finish: "foil" },
  });
});

test("@browser-lifecycle @playtest-rematch finish a game, find saved decks, and play a fresh match", async ({ browser }, info) => {
  test.setTimeout(240_000);
  const deck = { commander: "Yargle and Multani", cards: "99 Swamp" };
  const { contexts, pages } = await startPlaytestPod(browser, [deck, deck], 19191);
  const [host, opponent] = pages;
  try {
    await driveTo(pages, host.getByTestId("action-pass"), info, "Hold a priority window");
    const game = host.locator(".game-shell");
    const previousId = await game.getAttribute("data-game-id");
    expect(previousId).toBeTruthy();
    await openGameMenu(host);
    await host.getByTestId("action-concede").click();
    const confirmation = host.getByTestId("choice-dialog");
    await expect(confirmation).toContainText("Concede game");
    await host.keyboard.press("Escape");
    await expect(confirmation).toHaveCount(0);
    await expect(host.getByTestId("game-over-banner")).toHaveCount(0);
    await openGameMenu(host);
    await host.getByTestId("action-concede").click();
    await clickMove(host, confirmation.getByTestId("submit-choice"));

    await expect(opponent.getByTestId("game-over-banner")).toContainText("Seat B wins");
    await evidence(host, info, "finished-game-actions");
    await host.getByTestId("game-over-banner").getByRole("button", { name: "Decks and games" }).click();
    const saved = host.getByRole("region", { name: "Saved decks" });
    await expect(saved).toContainText("Playtest deck A");
    await expect(saved).not.toContainText("Playtest deck B");
    const recent = host.getByRole("region", { name: "Recent games" });
    await expect(recent).toContainText("Complete");
    await evidence(host, info, "saved-decks-and-game-history");
    await saved.getByRole("button", { name: "Edit Playtest deck A" }).click();
    await expect(host.getByTestId("deck-list")).toHaveValue(/99 Swamp/);
    await host.getByTestId("room-library").click();
    await recent.getByRole("button", { name: "View game", exact: true }).click();
    await expect(game).toHaveAttribute("data-game-id", previousId!);

    await host.getByRole("button", { name: "Play again with these decks" }).click();
    await opponent.getByRole("button", { name: "Join the next table" }).click();
    await expect(host.getByTestId("deck-ready-summary")).toContainText("Playtest deck A");
    await expect(opponent.getByTestId("deck-ready-summary")).toContainText("Playtest deck B");
    await host.getByTestId("unready-deck").click();
    await expect(host.getByTestId("deck-list")).toHaveValue(/99 Swamp/);
    await host.getByTestId("deck-name").fill("Rematch deck");
    await host.getByTestId("submit-deck").click();
    await expect(host.getByTestId("deck-ready-summary")).toContainText("Rematch deck");
    const deckChecks = host.getByRole("region", { name: "Your deck checks" });
    await expect(deckChecks).toContainText("Commander legality");
    await expect(deckChecks).toContainText("Passed current checks");
    await expect(deckChecks).toContainText("Match readiness");
    await startRoomGame(host);
    await expect(game).not.toHaveAttribute("data-game-id", previousId!);
    await expect(host.getByTestId("action-keep")).toBeVisible();
    await clickMove(host, host.getByTestId("action-keep"));
    await expect(opponent.getByTestId("action-keep")).toBeVisible();
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
