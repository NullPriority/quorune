import { expect, test } from "@playwright/test";
import { castOffer, clickMove, driveTo, startPlaytestPod } from "./support/playtest";

test("@browser-rules @playtest-yield Pass turn skips Lantern while another pilot holds an empty step", async ({ browser }, info) => {
  test.setTimeout(240_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Yargle and Multani", cards: "1 Soul-Guide Lantern\n98 Swamp" },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
  ], 323);
  const [host, opponent] = pages;
  const tray = host.getByTestId("decision-panel");
  try {
    const land = tray.locator('[data-testid^="action-play-land:"]').first();
    await driveTo(pages, land, info, "Play Lantern’s mana source");
    await clickMove(host, land);
    const stops = opponent.getByText("Priority stops", { exact: true });
    await stops.click();
    const stackHold = opponent.getByRole("checkbox", { name: "Hold for new spells and abilities" });
    await stackHold.check();
    await expect(opponent.getByText("Syncing priority stops…", { exact: true })).toHaveCount(0);
    await stops.click();
    const cast = castOffer(host, "Soul-Guide Lantern");
    await driveTo(pages, cast, info, "Offer the Lantern spell");
    await clickMove(host, cast);

    await expect(opponent.getByText("Priority held · New object on the stack", { exact: true })).toBeVisible();
    await clickMove(opponent, opponent.getByTestId("action-pass"));
    await stops.click();
    await stackHold.uncheck();
    await expect(opponent.getByText("Syncing priority stops…", { exact: true })).toHaveCount(0);
    await stops.click();

    const activation = tray.locator('[data-testid^="action-activate:"]').filter({ hasText: "Soul-Guide Lantern" }).first();
    await driveTo(pages, activation, info, "Wait for the activation stop");
    const railCombat = opponent.getByTestId("phase-rail").getByRole("button", { name: /^Combat\b/ });
    await railCombat.click();
    const endOfCombat = opponent.getByRole("group", { name: "Priority steps" })
      .getByRole("button", { name: "End of combat", exact: true });
    await endOfCombat.click();
    await expect(endOfCombat).toHaveAttribute("aria-pressed", "true");
    await expect(opponent.getByText("Syncing priority stops…", { exact: true })).toHaveCount(0);
    await railCombat.click();
    await expect(activation).toBeVisible();
    const activationId = (await activation.getAttribute("data-testid"))?.replace("action-", "");
    expect(activationId).toBeTruthy();

    const receipt = host.waitForResponse((response) => response.request().method() === "POST"
      && response.url().endsWith("/commands") && response.request().postDataJSON().action_id === "pass");
    await host.getByTestId("table-bottom-dock").getByRole("button", { name: "Pass turn", exact: true }).click();
    const response = await receipt;
    expect((await response.json()).receipt.code).toBe("accepted");
    expect(response.request().postDataJSON().choices.pass_turn_sequence).toBe(1);
    expect(response.request().postDataJSON().choices.ignored_activation_ids).toContain(activationId);
    await expect(opponent.getByText("Priority held · End of combat", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(opponent.getByTestId("exact-step-label")).toHaveText(/end of combat/i);
    await expect(opponent.getByTestId("decision-panel").locator('[data-testid^="action-activate:"]')).toHaveCount(0);
    const hostDock = host.getByTestId("table-bottom-dock");
    await hostDock.getByRole("button", { name: "Cancel pass turn", exact: true }).click();
    await expect(host.getByText("Syncing priority stops…", { exact: true })).toHaveCount(0);
    await clickMove(opponent, opponent.getByTestId("action-pass"));
    await expect(hostDock.getByText("Priority held · Pass turn canceled", { exact: true })).toBeVisible();
    await hostDock.getByRole("button", { name: "Pass turn", exact: true }).click();
    await expect(host.getByTestId("active-turn-label")).toContainText("Seat B", { timeout: 60_000 });
    await expect(hostDock.getByRole("button", { name: "Pass turn", exact: true })).toBeVisible();

    await driveTo(pages, activation, info, "Offer Lantern on another pilot’s turn");
    await hostDock.getByText("Priority stops", { exact: true }).click();
    const autoPass = host.waitForResponse((reply) => reply.request().method() === "POST"
      && reply.url().endsWith("/commands") && reply.request().postDataJSON().choices?.pass_turn_sequence === 2);
    await hostDock.getByRole("checkbox", { name: "Auto-pass through optional activations" }).check();
    const automaticPass = await autoPass;
    expect((await automaticPass.json()).receipt.code).toBe("accepted");
    expect(automaticPass.request().postDataJSON().choices.ignored_activation_ids).toContain(activationId);
    await host.getByTestId("auto-pass-toggle").click();
    await expect(host.getByTestId("auto-pass-toggle")).toContainText("Full control");
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
