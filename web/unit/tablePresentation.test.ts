import assert from "node:assert/strict";
import test from "node:test";
import type { LegalAction } from "../src/generated/protocol.ts";

import { actionTitle, activationCostLabel, cardActionCost, decisionDetail, isMandatorySelection, needsActivationConfirmation, phyrexianCastCostLabels, presentTurn, publicEventSummary, visibleActionLabel } from "../src/tablePresentation.ts";

const pass = { id: "pass", action: "pass", label: "Pass priority" };

test("public events use names and counts in plain language", () => {
  assert.equal(publicEventSummary({ code: "stack.cast", summary: "B cast S5 Negate." }), "Cast Negate.");
  assert.equal(publicEventSummary({ code: "land.play", summary: "B played B19 Island." }), "Played Island.");
  assert.equal(publicEventSummary({ code: "stack.storm.copy", summary: "A created 1 storm copy/copies." }),
    "Created 1 copy with storm.");
  assert.equal(publicEventSummary({ code: "stack.gravestorm.copy", summary: "B created 3 gravestorm copy/copies." }),
    "Created 3 copies with gravestorm.");
  assert.equal(publicEventSummary({ code: "priority.pass", summary: "A passed priority." }), "A passed priority.");
  assert.equal(publicEventSummary({ code: "priority.pass", summary: "" }), "Game event recorded.");
  const island = (ref: string) => ref === "B19" ? "Island" : undefined;
  assert.equal(publicEventSummary({ code: "mana.ability", summary: "B activated B19 intrinsic_island for mana." }, island),
    "Activated Island for mana.");
  assert.equal(publicEventSummary({ code: "mana.produce", summary: "B tapped B19 for {'U': 1}." }, island),
    "Tapped Island for 1 blue mana.");
  assert.equal(publicEventSummary({ code: "mana.produce", summary: "A tapped A19 for {'B': 1, 'R': 2}." }, island),
    "Tapped a mana source for 1 black and 2 red mana.");
  assert.equal(publicEventSummary({ code: "mana.undo", summary: "B undid B19's mana activation." }, island),
    "Undid mana from Island.");
});

test("players see a readable action or choice when a label is missing", () => {
  assert.equal(actionTitle({ id: "activate:A18:ab1", action: "activate", label: "activate:A18:ab1" }), "Activate ability");
  assert.equal(actionTitle({ id: "unexpected_internal_action", action: "unexpected_internal_action" }), "Make a choice");
  assert.equal(decisionDetail({ kind: "cleanup.discard", ctx: { count: 2 } }), "Discard 2 cards to reach your maximum hand size.");
});

test("mandatory selection metadata covers the named decision types", () => {
  for (const kind of ["mulligan.bottom", "cleanup.discard", "state.legend", "trigger.order", "combat.damage"]) {
    assert.equal(isMandatorySelection(kind), true, kind);
  }
  assert.equal(isMandatorySelection("priority"), false);
  assert.equal(decisionDetail({ kind: "trigger.order", ctx: { triggers: ["first", "second"] } }),
    "2 triggered abilities. Choose the order in which the abilities go on the stack.");
  assert.equal(decisionDetail({ kind: "trigger.order", ctx: { triggers: ["first", "second"], instruction: "Order bottom-to-top on the stack." } }),
    "2 triggered abilities. Order bottom-to-top on the stack.");
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

test("printed Phyrexian choices label the mana and life from the offer", () => {
  const action: LegalAction = {
    id: "cast:A15",
    action: "cast",
    cost_options: [
      { id: "printed-phyrexian-1", requirements: { R: 1 } },
      { id: "printed-phyrexian-2", requirements: {}, additional_life: 2 },
      { id: "printed-phyrexian-3", requirements: { GENERIC: 1, B: 1 }, additional_life: 2 },
      { id: "flashback", requirements: { U: 1 } },
    ],
  };
  assert.deepEqual(phyrexianCastCostLabels(action), {
    "printed-phyrexian-1": "Pay {R}",
    "printed-phyrexian-2": "Pay 2 life",
    "printed-phyrexian-3": "Pay {1}{B} and 2 life",
  });
});

test("fixed material activation costs are reviewed before commitment", () => {
  const action = { id: "activate:1", action: "activate", cost_summary: { tap: true, life: 1, sac_self: true } };
  assert.equal(needsActivationConfirmation(action), true);
  assert.equal(activationCostLabel(action), "Tap this card · Sacrifice this card · Pay 1 life");
  assert.equal(needsActivationConfirmation({ ...action, cost_summary: { tap: true } }), false);
  assert.equal(needsActivationConfirmation({ ...action, cost_summary: { m: { GENERIC: 1 } } }), false);
  assert.equal(activationCostLabel({ ...action, cost_summary: { random_discard: 2 } }), "Discard 2 cards at random");
  assert.equal(activationCostLabel({ ...action, cost_summary: { loyalty: 2 } }), "+2 loyalty");
  assert.equal(activationCostLabel({ ...action, cost_summary: { loyalty: -1 } }), "−1 loyalty");
  assert.equal(activationCostLabel({ ...action, cost_summary: { loyalty: 0 } }), "0 loyalty");
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
