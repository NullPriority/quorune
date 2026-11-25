import assert from "node:assert/strict";
import test from "node:test";

import { actionTitle, cardActionCost, decisionDetail, presentTurn, visibleActionLabel } from "../src/tablePresentation.ts";

const pass = { id: "pass", action: "pass", label: "Pass priority" };

test("players see a readable action or choice when a label is missing", () => {
  assert.equal(actionTitle({ id: "activate:A18:ab1", action: "activate", label: "activate:A18:ab1" }), "Activate ability");
  assert.equal(actionTitle({ id: "unexpected_internal_action", action: "unexpected_internal_action" }), "Make a choice");
  assert.equal(decisionDetail({ kind: "cleanup.discard", ctx: { count: 2 } }), "Discard 2 cards to reach your maximum hand size.");
});

test("the hover cost uses the offered alternate price", () => {
  const offer = {
    id: "cast:A15", action: "cast", label: "Cast Faithless Looting — {R}", cost: "{R}",
    cost_options: [{ id: "flashback", requirements: { GENERIC: 2, R: 1 } }],
  };
  assert.equal(actionTitle(offer), "Cast Faithless Looting");
  assert.equal(cardActionCost([offer]), "{2}{R}");
  assert.equal(cardActionCost([{ ...offer, cost_options: [{ ...offer.cost_options[0], target_costs: { prices: [] } }] }]),
    "Cost depends on targets");
});

test("active empty main phases label their intentional turn advance", () => {
  assert.equal(visibleActionLabel(pass, {
    activeSeat: "A", ownSeat: "A", phase: "precombat_main", stackDepth: 0,
  }), "Continue to combat");
  assert.equal(visibleActionLabel(pass, {
    activeSeat: "A", ownSeat: "A", phase: "postcombat_main", stackDepth: 0,
  }), "End turn");
});

test("pass intent identifies when the stack can advance", () => {
  assert.equal(visibleActionLabel(pass, {
    activeSeat: "A", ownSeat: "B", phase: "precombat_main", stackDepth: 0,
  }), "Pass priority");
  assert.equal(visibleActionLabel(pass, {
    activeSeat: "A", ownSeat: "A", phase: "precombat_main", stackDepth: 1,
  }), "Resolve");
});

test("turn presentation separates active player, priority, and exact step", () => {
  const value = presentTurn({
    turnSequence: 1,
    activeSeat: "A",
    prioritySeat: "B",
    phase: "beginning",
    step: "upkeep",
    combatDamageStep: 0,
    firstStrikeStep: false,
    lifecycleStatus: "active",
  });
  assert.equal(value.headline, "Seat A's Turn · Turn 1");
  assert.equal(value.priority, "Priority: Seat B");
  assert.equal(value.exactStep, "Upkeep");
  assert.equal(value.macroPhase, "upkeep");
  assert.equal(value.rail.find((entry) => entry.id === "upkeep")?.state, "current");
  assert.equal(value.rail.find((entry) => entry.id === "untap")?.state, "past");
});

test("turn presentation normalizes both main phases and combat damage", () => {
  const base = {
    turnSequence: 4,
    activeSeat: "B",
    prioritySeat: "B",
    combatDamageStep: 0,
    firstStrikeStep: false,
    lifecycleStatus: "active",
  };
  assert.equal(presentTurn({ ...base, phase: "precombat_main", step: "main" }).exactStep, "Main Phase 1");
  assert.equal(presentTurn({ ...base, phase: "postcombat_main", step: "main" }).exactStep, "Main Phase 2");
  assert.equal(presentTurn({ ...base, phase: "combat", step: "combat_damage", firstStrikeStep: true }).exactStep, "First-Strike Damage");
  assert.equal(presentTurn({ ...base, phase: "combat", step: "combat_damage", combatDamageStep: 2 }).exactStep, "Combat Damage");
});

test("completed games replace live turn and priority presentation", () => {
  const value = presentTurn({
    turnSequence: 12,
    activeSeat: "A",
    prioritySeat: "B",
    phase: "ending",
    step: "cleanup",
    combatDamageStep: 0,
    firstStrikeStep: false,
    lifecycleStatus: "complete",
  });
  assert.equal(value.terminal, true);
  assert.equal(value.headline, "Game complete");
  assert.equal(value.priority, "No live priority");
});

test("the opening table describes mulligans before there is an active seat", () => {
  const value = presentTurn({
    turnSequence: 0, activeSeat: "", prioritySeat: "", phase: "setup", step: "",
    combatDamageStep: 0, firstStrikeStep: false, lifecycleStatus: "active",
  });
  assert.equal(value.headline, "Choosing opening hands");
  assert.equal(value.priority, "Before the first turn");
  assert.ok(value.rail.every((phase) => phase.state === "future"));
});
