import { expect, test } from "@playwright/test";

test("deck review shows every rulings mismatch and can start with reviewed data", async ({ page, browser, baseURL }, info) => {
  test.skip(process.env.QUORUNE_TEST_RULINGS_MISMATCH !== "1", "Select the local snapshot that reproduces the mismatch");
  await page.goto("/");
  await page.getByTestId("display-name").fill("Deck review tester");
  await page.getByTestId("create-guest").click();
  await page.getByTestId("room-size").selectOption("2");
  await page.getByTestId("create-room").click();
  const list = "1 Deflecting Swat\n1 Deadly Rollick\n49 Mountain\n48 Swamp";
  await page.getByTestId("deck-name").fill("Rulings check witness");
  await page.getByTestId("commander-name").fill("Mishra, Eminent One");
  await page.getByTestId("deck-list").fill(list);
  await page.getByTestId("submit-deck").click();
  const summary = page.getByTestId("deck-review-summary");
  await expect(summary).toContainText("Commander legalityLegal");
  await expect(summary).toContainText("2 cards need a card data review");
  await expect(summary.getByText("Deflecting Swat", { exact: true })).toBeVisible();
  await expect(summary.getByText("Deadly Rollick", { exact: true })).toBeVisible();
  await expect(summary).toContainText("Draft saved for review");
  await expect(page.getByTestId("deck-list")).toHaveValue(list);
  await expect(page.getByTestId("start-game")).toBeDisabled();
  await page.reload();
  const swat = summary.locator("details.deck-data-mismatch").filter({ hasText: "Deflecting Swat" });
  await swat.locator("summary").first().click();
  await expect(page.locator(".start-bar")).toHaveCSS("position", "static");
  if (process.env.QUORUNE_TEST_REVIEWED_SNAPSHOT === "1") {
    await expect(swat.getByText("Card rulings · 2 added", { exact: true })).toBeVisible();
    await expect(swat.locator("blockquote")).toHaveCount(2);
    await expect(summary.getByText("Card rulings · Formatting only", { exact: true })).toBeVisible();
  }
  await expect(swat.getByText("Diagnostic ID", { exact: true })).toBeHidden();
  await info.attach("editable-rulings-draft", { body: await summary.screenshot(), contentType: "image/png" });
  await swat.getByText("Technical details", { exact: true }).click();
  await expect(swat.getByText("Diagnostic ID", { exact: true })).toBeVisible();
  await expect(swat.getByText(/^Reviewed program/)).toBeVisible();
  await summary.getByRole("button", { name: "Edit saved draft" }).click();
  await expect(page.getByTestId("deck-list")).toHaveValue(/1 Deflecting Swat/);

  if (process.env.QUORUNE_TEST_REVIEWED_SNAPSHOT !== "1") return;
  const invite = await page.getByTestId("room-invite").textContent();
  expect(invite).toBeTruthy();
  const context = await browser.newContext({ baseURL });
  try {
    const opponent = await context.newPage();
    await opponent.goto("/");
    await opponent.getByTestId("display-name").fill("Other seat");
    await opponent.getByTestId("create-guest").click();
    await opponent.getByTestId("invite-code").fill(invite!);
    await opponent.getByTestId("seat-select").selectOption("B");
    await opponent.getByTestId("join-room").click();
    await expect(opponent.getByTestId("seat-A")).toContainText("Deck needs review");
    await expect(opponent.getByText("Deflecting Swat", { exact: true })).toHaveCount(0);
    await expect(opponent.getByText("Use earlier card data", { exact: true })).toHaveCount(0);
    await opponent.getByTestId("deck-name").fill("Ready witness");
    await opponent.getByTestId("commander-name").fill("Yargle and Multani");
    await opponent.getByTestId("deck-list").fill("99 Swamp");
    await opponent.getByTestId("submit-deck").click();
    await expect(opponent.getByTestId("deck-ready-summary")).toBeVisible();
    await expect(page.getByTestId("start-game")).toBeDisabled();
    await page.getByText("Use earlier card data", { exact: true }).click();
    const start = page.getByRole("button", { name: "Start with earlier card data" });
    await expect(start).toBeVisible();
    const [started] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith("/start") && response.request().method() === "POST"),
      start.click(),
    ]);
    expect(started.ok(), await started.text()).toBeTruthy();
    for (const pilot of [page, opponent]) {
      await expect(pilot.getByTestId("own-hand").locator(".hand-card")).toHaveCount(7);
      await pilot.getByRole("button", { name: "Keep this hand", exact: true }).click();
    }
    await expect(page.getByTestId("active-turn-label")).toContainText("1st turn");
    await opponent.reload();
    await expect(opponent.getByTestId("active-turn-label")).toContainText("1st turn");
  } finally {
    await context.close();
  }
});
