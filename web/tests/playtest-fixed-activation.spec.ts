import { expect, test } from "@playwright/test";
import { boardCard, clickMove, driveTo, evidence, startPlaytestPod } from "./support/playtest";
import { viewRevision } from "./support/progress";

test("@browser-rules a single land play is direct and its fixed activation requires review", async ({ browser }, info) => {
  test.setTimeout(150_000);
  const { contexts, pages } = await startPlaytestPod(browser, [
    { commander: "Yargle and Multani", cards: "1 Prismatic Vista\n98 Forest" },
    { commander: "Yargle and Multani", cards: "99 Swamp" },
  ], 1);
  const [host] = pages;
  const vista = host.getByTestId("own-hand").getByRole("button", { name: "Play Prismatic Vista", exact: true });
  const battlefieldVista = boardCard(host, "Prismatic Vista");
  const life = host.locator('.life[data-player-ref="A"]');
  const draft = host.getByTestId("choice-dialog");
  try {
    await driveTo(pages, vista, info, "Pass to the legal Vista land drop");
    await clickMove(host, vista);
    await expect(battlefieldVista).toBeVisible();
    await expect(host.getByTestId("selected-card-actions")).toHaveCount(0);

    const before = await viewRevision(host);
    await battlefieldVista.click();
    await expect(draft).toContainText("Pay 1 life");
    await expect(draft).toContainText("Sacrifice this card");
    await expect(draft).toContainText("Tap this card");
    await expect(life).toHaveAttribute("aria-label", "40 life");
    expect(await viewRevision(host)).toBe(before);
    await evidence(host, info, "fixed-activation-before-payment");

    await draft.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(draft).toHaveCount(0);
    await expect(battlefieldVista).toBeVisible();
    await expect(life).toHaveAttribute("aria-label", "40 life");
    expect(await viewRevision(host)).toBe(before);

    await battlefieldVista.click();
    await expect(draft).toBeVisible();
    await host.keyboard.press("Escape");
    await expect(draft).toHaveCount(0);
    await expect(battlefieldVista).toBeVisible();
    await expect(life).toHaveAttribute("aria-label", "40 life");
    expect(await viewRevision(host)).toBe(before);

    await battlefieldVista.click();
    await clickMove(host, draft.getByRole("button", { name: "Pay cost and activate", exact: true }));
    await expect(life).toHaveAttribute("aria-label", "39 life");
    await expect(battlefieldVista).toHaveCount(0);
  } finally {
    await Promise.all(contexts.map(context => context.close()));
  }
});
