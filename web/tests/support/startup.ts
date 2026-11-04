import { expect, test, type Page } from "@playwright/test";

export async function startRoomGame(page: Page): Promise<void> {
  const receipt = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && /^\/api\/v1\/rooms\/[^/]+\/start$/.test(new URL(response.url()).pathname),
    { timeout: test.info().timeout },
  );
  await page.getByTestId("start-game").click();
  const response = await receipt;
  expect(response.status(), "Game startup must receive an accepted HTTP receipt").toBe(200);
  const result = await response.json();
  expect(typeof result.game_id).toBe("string");
  expect(result.game_id.length).toBeGreaterThan(0);
}
