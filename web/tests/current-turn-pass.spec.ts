import { expect, test } from "@playwright/test";
import { castOffer, clickMove, driveTo, startPlaytestPod } from "./support/playtest";
import { openGameMenu } from "./support/startup";

test("@browser-rules @playtest-yield Pass turn records its scope and only skips an explicitly ignored activation stop", async ({ browser }, info) => {
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
    const activationId = (await activation.getAttribute("data-testid"))!.replace("action-", "");
    await openGameMenu(host);
    await host.getByText("Priority stops for this turn", { exact: true }).click();
    await host.getByRole("checkbox", { name: /Ignore stop for Viscera Seer/ }).check();
    await host.getByTestId("open-game-menu").click();
    await expect(activation).toBeVisible();

    const receipt = host.waitForResponse((response) => response.request().method() === "POST"
      && response.url().endsWith("/commands") && response.request().postDataJSON().action_id === "pass");
    await host.getByRole("button", { name: "Pass turn", exact: true }).click();
    const response = await receipt;
    expect((await response.json()).receipt.code).toBe("accepted");
    expect(response.request().postDataJSON().choices).toEqual({
      pass_turn_sequence: 1, ignored_activation_ids: [activationId],
    });
    await expect(host.getByText("Combat started", { exact: true })).toBeVisible({ timeout: 30_000 });
    await host.getByRole("button", { name: "Cancel pass turn" }).click();
    await host.getByTestId("auto-pass-toggle").click();
    await expect(host.getByTestId("auto-pass-toggle")).toContainText("Full control");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
