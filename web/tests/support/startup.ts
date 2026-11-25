import { expect, test, type Page } from "@playwright/test";
import { viewRevision } from "./progress";

export async function openGameMenu(page: Page): Promise<void> {
  const menu = page.locator("details.game-menu");
  if (await menu.getAttribute("open") === null) await page.getByTestId("open-game-menu").click();
}

export async function completeCastGesture(page: Page, gesture: () => Promise<void>): Promise<void> {
  const revision = await viewRevision(page);
  const dialog = page.getByTestId("choice-dialog");
  await gesture();
  await expect.poll(async () => await dialog.isVisible() || await viewRevision(page) > revision).toBe(true);
  if (await dialog.isVisible()) await dialog.getByTestId("submit-choice").click();
  await expect.poll(() => viewRevision(page)).toBeGreaterThan(revision);
}

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
