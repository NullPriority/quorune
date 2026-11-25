import { expect, test, type Locator, type Page } from "@playwright/test";
import { startRoomGame } from "./support/startup";
import { submitSingleCleanupDiscard } from "./support/playtest";
import { driveUntil, viewRevision } from "./support/progress";
import { list, record } from "../src/choices";
import type { DecisionPacket, JsonValue } from "../src/generated/protocol";

const commander = "Yargle and Multani";
const deck = (mainboard: string) => `Commander:\n1 ${commander}\n\nMainboard:\n${mainboard}\n`;
const readiness = /Deck (validated|accepted)/;

async function enter(page: Page, seat: string) {
  await page.goto("/");
  await page.getByTestId("display-name").fill(`Playtester ${seat}`);
  await page.getByTestId("create-guest").click();
  await expect(page.getByRole("heading", { name: "Find your table" })).toBeVisible();
}

async function registerDeck(page: Page, seat: string) {
  await page.getByTestId("deck-name").fill(`Playtest deck ${seat}`);
  await page.getByTestId("commander-name").fill(commander);
  await page.getByTestId("deck-list").fill(deck(seat === "A"
    ? "1 Viscera Seer\n1 Plagued Rusalka\n1 Ornithopter\n1 Dark Ritual\n95 Swamp"
    : "99 Swamp"));
  await page.getByTestId("submit-deck").click();
  await expect(page.locator(".success-banner, .warning-banner").filter({ hasText: readiness })).toBeVisible({ timeout: 60_000 });
}

async function submit(page: Page, button: Locator, openCastForm = false) {
  const revision = await viewRevision(page);
  await button.click();
  if (openCastForm && await page.getByTestId("choice-dialog").isVisible()) {
    await page.getByTestId("submit-choice").click();
  }
  await expect.poll(() => viewRevision(page)).toBeGreaterThan(revision);
}

function commandReceipt(page: Page) {
  return page.waitForResponse((response) =>
    response.request().method() === "POST" && /\/games\/[^/]+\/commands$/.test(new URL(response.url()).pathname));
}

test("@browser-rules @mana-action @playtest-activation selected creature costs work through authenticated play", async ({ browser }, testInfo) => {
  test.setTimeout(300_000);
  const contexts = await Promise.all(Array.from({ length: 4 }, () => browser.newContext({
    viewport: { width: 1366, height: 650 },
  })));
  const pages = await Promise.all(contexts.map((context) => context.newPage()));
  const [host, ...opponents] = pages;
  let packet: DecisionPacket | undefined;
  host.on("websocket", (socket) => socket.on("framereceived", ({ payload }) => {
    if (typeof payload !== "string") return;
    const message = JSON.parse(payload) as { type: string; packet?: DecisionPacket };
    if (message.type === "projection") packet = message.packet;
  }));
  const seats = ["A", "B", "C", "D"];
  const ownBoard = host.locator("[data-own-battlefield='true']");
  const tray = host.getByTestId("decision-panel");
  const dock = host.getByTestId("public-action-draft");
  const stack = host.locator(".stack-items");
  const onBoard = (name: string) => ownBoard.locator(".card-tile").filter({ has: host.getByText(name, { exact: true }) });
  const keepOrAdvance = async (action: Locator) => {
    await driveUntil(pages, async () => await action.isVisible() && await action.isEnabled(), testInfo, {
      label: "Reach the offered activation playtest action",
      overallMs: 120_000,
      noProgressMs: 25_000,
      advance: async () => {
        if (await submitSingleCleanupDiscard(pages)) return true;
        for (const page of pages) {
          const emptyAttack = page.getByRole("button", { name: "Confirm attackers" });
          if (await emptyAttack.isVisible() && await emptyAttack.isEnabled()) {
            await submit(page, emptyAttack);
            return true;
          }
        }
        return false;
      },
    });
  };

  async function playSwamp() {
    const land = tray.locator('[data-testid^="action-play-land:"]').first();
    await keepOrAdvance(land);
    await submit(host, land);
  }

  async function cast(name: string) {
    const spell = tray.locator('[data-testid^="action-cast:"]').filter({ hasText: name }).first();
    await keepOrAdvance(spell);
    await submit(host, spell, true);
    await keepOrAdvance(onBoard(name));
  }

  async function capture(name: string) {
    const path = testInfo.outputPath(`${name}-1366x650.png`);
    await host.screenshot({ path });
    await testInfo.attach(name, { path, contentType: "image/png" });
  }

  try {
    await host.route(/\/api\/v1\/rooms$/, async (route) => {
      const request = route.request();
      await route.continue({
        postData: JSON.stringify({ ...request.postDataJSON(), seed: 97249 }),
        headers: { ...request.headers(), "content-type": "application/json" },
      });
    });
    await Promise.all(pages.map((page, index) => enter(page, seats[index])));
    await host.getByTestId("room-size").selectOption("4");
    await host.getByTestId("create-room").click();
    const invite = await host.getByTestId("room-invite").textContent();
    expect(invite).toBeTruthy();
    await Promise.all(opponents.map(async (page, index) => {
      await page.getByTestId("invite-code").fill(invite!);
      await page.getByTestId("seat-select").selectOption(seats[index + 1]);
      await page.getByTestId("join-room").click();
    }));
    await Promise.all(pages.map((page, index) => registerDeck(page, seats[index])));
    await startRoomGame(host);
    for (const page of pages) await submit(page, page.getByTestId("action-keep"));

    await cast("Ornithopter");
    await playSwamp();
    const ritual = tray.locator('[data-testid^="action-cast:"]').filter({ hasText: "Dark Ritual" }).first();
    await keepOrAdvance(ritual);
    await submit(host, ritual, true);
    await cast("Viscera Seer");
    await cast("Plagued Rusalka");
    await keepOrAdvance(tray.locator('[data-testid^="action-activate:"]').filter({ hasText: "Plagued Rusalka" }));
    await expect(host.locator(".card-action-hint").filter({ hasText: /^Info$/i })).toHaveCount(0);

    const passTurn = host.getByRole("button", { name: "Pass turn", exact: true });
    await passTurn.click();
    await expect(host.getByText("A response is available", { exact: true })).toBeVisible();
    await host.getByRole("button", { name: "Cancel pass turn" }).click();
    await host.getByTestId("auto-pass-toggle").click();
    await expect(host.getByTestId("auto-pass-toggle")).toContainText("Full control");
    await expect(passTurn).toBeDisabled();
    await host.getByTestId("auto-pass-toggle").click();
    const chooseRusalkaCost = async (captureAim = false) => {
      await onBoard("Plagued Rusalka").click();
      await expect(dock).toContainText("{B}");
      await expect(dock).toContainText("Sacrifice");
      await expect(onBoard("Ornithopter")).toHaveAttribute("data-choice-candidate", "true");
      await onBoard("Ornithopter").hover();
      const arrow = host.getByTestId("target-arrow-layer");
      await expect(arrow).toHaveAttribute("data-arrow-count", "1");
      expect(await arrow.evaluate((element) => getComputedStyle(element).pointerEvents)).toBe("none");
      if (captureAim) await capture("rusalka-aiming-arrow");
      await onBoard("Ornithopter").click();
      await expect(arrow).toHaveAttribute("data-arrow-count", "1");
      await expect(onBoard("Plagued Rusalka")).toHaveAttribute("data-choice-candidate", "true");
      await onBoard("Plagued Rusalka").click();
      await expect(dock).toContainText("1 / 1 · Ornithopter");
      await expect(dock).toContainText("1 / 1 · Plagued Rusalka");
    };
    const uncommittedRevision = await viewRevision(host);
    await chooseRusalkaCost();
    await dock.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(onBoard("Plagued Rusalka")).toBeVisible();
    expect(await viewRevision(host)).toBe(uncommittedRevision);
    await chooseRusalkaCost(true);
    await capture("rusalka-target-mana-and-sacrifice");
    const activation = commandReceipt(host);
    await submit(host, dock.getByTestId("submit-choice"));
    expect((await (await activation).json()).receipt.code).toBe("accepted");
    await expect(onBoard("Plagued Rusalka")).toHaveCount(0);
    await expect(stack).toContainText("Plagued Rusalka");
    await stack.locator(".stack-row").filter({ hasText: "Plagued Rusalka" }).hover();
    await expect(onBoard("Ornithopter")).toHaveClass(/target-highlight/);
    await expect(host.getByTestId("target-arrow-layer")).toHaveAttribute("data-arrow-count", "1");

    const seerOffer = packet?.decision?.legal_actions.find((action) => action.action === "activate"
      && String(action.label ?? "").includes("Viscera Seer"));
    const typedCost = record(list(record(seerOffer?.cost_summary as JsonValue | undefined).choose_cost)[0]);
    const costField = seerOffer?.form?.fields.find((field) => field.name === "cost_cards");
    expect(typedCost.n).toBe(1);
    expect(costField).toMatchObject({ control: "refs", required: true, minimum: 1, maximum: 1 });
    expect(list(costField?.options).map((option) => record(option).value)).toEqual(typedCost.legal_refs);
    const beforeSacrifice = await viewRevision(host);
    await onBoard("Viscera Seer").click();
    await expect(onBoard("Viscera Seer")).toHaveAttribute("data-choice-candidate", "true");
    const wrongCost = onBoard("Swamp").first();
    await expect(wrongCost).not.toHaveAttribute("data-choice-candidate", "true");
    await wrongCost.click();
    await expect(host.getByRole("dialog", { name: /Card viewer/ })).toHaveCount(0);
    await expect(dock.getByTestId("submit-choice")).toBeDisabled();
    expect(await viewRevision(host)).toBe(beforeSacrifice);
    await wrongCost.press("i");
    await expect(host.getByRole("dialog", { name: "Card viewer: Swamp" })).toBeVisible();
    await host.keyboard.press("Escape");
    await expect(host.getByRole("dialog", { name: /Card viewer/ })).toHaveCount(0);
    await expect(dock.getByTestId("submit-choice")).toBeDisabled();
    await onBoard("Ornithopter").focus();
    await onBoard("Ornithopter").press("Enter");
    await expect(dock).toContainText("1 / 1 · Ornithopter");
    await capture("seer-sacrifice-selection");
    const clickAt = performance.now();
    const scryActivation = commandReceipt(host).then(async (response) => ({
      receivedAt: performance.now(),
      command: response.request().postDataJSON(),
      receipt: (await response.json()).receipt,
    }));
    await submit(host, dock.getByTestId("submit-choice"));
    const accepted = await scryActivation;
    const projectionAt = performance.now();
    expect(accepted.receipt.code).toBe("accepted");
    expect(Object.keys(accepted.command.choices)).toEqual(["cost_cards"]);
    await expect(onBoard("Ornithopter")).toHaveCount(0);
    await expect(stack).toContainText("Viscera Seer");
    await expect(stack).toContainText("Plagued Rusalka");
    await expect(tray.getByRole("form", { name: "Scry" })).toHaveCount(0);
    await capture("two-activated-abilities-on-stack");

    const scry = tray.getByRole("form", { name: "Scry" });
    const bottom = scry.getByRole("button", { name: "Bottom", exact: true });
    await keepOrAdvance(bottom);
    for (const page of opponents) await expect(page.getByRole("form", { name: "Scry" })).toHaveCount(0);
    await expect(scry).toContainText("Swamp");
    await expect(scry.getByRole("button", { name: "Top", exact: true })).toBeVisible();
    await expect(bottom).toBeVisible();
    await capture("private-scry-resolution");
    await submit(host, bottom);
    await testInfo.attach("seer-interaction-evidence", {
      contentType: "application/json",
      body: JSON.stringify({
        viewport: await host.evaluate(() => ({ width: innerWidth, height: innerHeight, devicePixelRatio })),
        expected: "Sacrifice one creature you control, then scry one on resolution",
        issued: { kind: typedCost.k, count: typedCost.n, eligible: typedCost.legal_refs },
        control: { name: costField?.name, required: costField?.required, min: costField?.minimum, max: costField?.maximum },
        visibleRoute: ["Select Viscera Seer", "Try ineligible Swamp", "Select Ornithopter with Enter", "Pay and activate", "Put on bottom"],
        nearestIllegalCost: { card: "Swamp", changedRevision: false, submitEnabled: false },
        receipt: { interactionId: accepted.command.command_id, code: accepted.receipt.code, revision: accepted.receipt.state_revision },
        projection: { creatureLeftBattlefield: "Ornithopter", abilityOnStack: "Viscera Seer", scryCompleted: true },
        timingMs: { toReceipt: accepted.receivedAt - clickAt, toObservedRevision: projectionAt - clickAt },
        interventions: [],
      }, null, 2),
    });
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});
