import { expect, test } from "@playwright/test";

test("@browser-lifecycle startup can recover from an unavailable server and submits the guest once", async ({ page }) => {
  await page.route("**/api/v1/system", (route) => route.fulfill({
    status: 503, json: { detail: "Service unavailable in the startup test" },
  }), { times: 1 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Couldn’t open Quorune" })).toBeVisible();
  await expect(page.getByText("Service unavailable in the startup test")).toBeHidden();
  await page.getByText("Connection details", { exact: true }).click();
  await expect(page.getByText("Service unavailable in the startup test")).toBeVisible();
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(page.getByRole("heading", { name: "Join as a guest" })).toBeVisible();
  await page.getByTestId("display-name").fill("Returning guest");
  let requests = 0;
  await page.route("**/api/v1/guests", async (route) => {
    requests += 1;
    await route.continue();
  });
  await page.getByTestId("create-guest").evaluate((button) => {
    const form = (button as HTMLButtonElement).form!;
    form.requestSubmit();
    form.requestSubmit();
  });
  await expect(page.getByRole("heading", { name: "Find your table" })).toBeVisible();
  expect(requests).toBe(1);
  await expect(page.getByText("Playing as Returning guest", { exact: false })).toBeVisible();
});
