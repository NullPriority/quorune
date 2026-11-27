import { expect, test } from "@playwright/test";
import { castOffer, clickMove, driveTo, startPlaytestPod } from "./support/playtest";
import { openGameMenu } from "./support/startup";

test("@browser-rules @playtest-yield Pass turn yields voluntary responses and honors a selected step hold", async ({ browser }, info) => {
  test.setTimeout(240_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Yargle and Multani", cards: "1 Viscera Seer\n98 Swamp" },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
  ], 323);
  const [host] = pages;
  const tray = host.getByTestId("decision-panel");
  try {
    const land = tray.locator('[data-testid^="action-play-land:"]').first();
    await driveTo(pages, land, info, "Play the Seer’s mana source");
    await clickMove(host, land);
    const cast = castOffer(host, "Viscera Seer");
    await driveTo(pages, cast, info, "Offer the Seer spell");
    await clickMove(host, cast);
    const activation = tray.locator('[data-testid^="action-activate:"]').filter({ hasText: "Viscera Seer" });
    await driveTo(pages, activation, info, "Wait for the activation stop");
    await openGameMenu(host);
    await host.getByText("Hold priority at specific steps", { exact: true }).click();
    const endOfCombat = host.getByRole("group", { name: "Combat priority stops" })
      .getByRole("button", { name: "End of combat", exact: true });
    await endOfCombat.click();
    await expect(endOfCombat).toHaveAttribute("aria-pressed", "true");
    await host.getByTestId("open-game-menu").click();
    await expect(activation).toBeVisible();

    const receipt = host.waitForResponse((response) => response.request().method() === "POST"
      && response.url().endsWith("/commands") && response.request().postDataJSON().action_id === "pass");
    await host.getByTestId("table-bottom-dock").getByRole("button", { name: "Pass turn", exact: true }).click();
    const response = await receipt;
    expect((await response.json()).receipt.code).toBe("accepted");
    expect(response.request().postDataJSON().choices).toEqual({ pass_turn_sequence: 1 });
    await expect(host.getByText("Priority held · End of combat", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(host.getByTestId("exact-step-label")).toHaveText(/end of combat/i);
    await expect(activation).toBeVisible();
    await host.getByRole("button", { name: "Cancel pass turn" }).click();
    await host.getByTestId("auto-pass-toggle").click();
    await expect(host.getByTestId("auto-pass-toggle")).toContainText("Full control");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
