import assert from "node:assert/strict";
import test from "node:test";

import { activationStopSignature, findSafeAutoPass, priorityActivityHold, selectedPriorityStop } from "../src/tableAutomation.ts";

test("automatic passing accepts only pass, concede, and bare mana actions", () => {
  const pass = { action: "pass", id: "pass:1" };
  assert.equal(findSafeAutoPass([pass]), pass);
  assert.equal(
    findSafeAutoPass([pass, { action: "activate", mana_ability: true }]),
    pass,
  );
  assert.equal(findSafeAutoPass([pass, { action: "concede" }]), pass);
});

test("automatic passing stops for every meaningful action category", () => {
  for (const action of ["play_land", "cast", "activate", "undo_mana", "choose", "attack", "block"]) {
    assert.equal(
      findSafeAutoPass([{ action: "pass" }, { action }]),
      null,
      action,
    );
  }
  assert.equal(findSafeAutoPass([{ action: "concede" }]), null);
});

test("automatic passing never advances an active player's empty-stack main phase", () => {
  const pass = { action: "pass", id: "pass:main" };
  for (const phase of ["precombat_main", "postcombat_main"]) {
    assert.equal(
      findSafeAutoPass([pass], {
        activePlayer: "A",
        phase,
        principalSeat: "A",
        stackDepth: 0,
      }),
      null,
      phase,
    );
  }
});

test("automatic passing still accelerates nonactive and stack response windows", () => {
  const pass = { action: "pass", id: "pass:response" };
  assert.equal(
    findSafeAutoPass([pass], {
      activePlayer: "A",
      phase: "precombat_main",
      principalSeat: "B",
      stackDepth: 0,
    }),
    pass,
  );
  assert.equal(
    findSafeAutoPass([pass], {
      activePlayer: "A",
      phase: "precombat_main",
      principalSeat: "A",
      stackDepth: 1,
    }),
    pass,
  );
});

test("a current-turn yield can leave a routine main phase and preserves complex mana choices", () => {
  const pass = { action: "pass", id: "pass:main" };
  const window = { activePlayer: "A", phase: "precombat_main", principalSeat: "A", allowActiveMain: true };
  assert.equal(findSafeAutoPass([pass], window), pass);
  assert.equal(findSafeAutoPass([pass, {
    action: "activate", id: "mana", mana_ability: true, cost_summary: { sac_self: 1 },
  }], window), null);
});

test("activity holds distinguish a new stack item, a graveyard change, and a graveyard effect on the stack", () => {
  const acknowledged = { stack: ["S1"], graveyard: "A1", graveyardStack: [] };
  const stack = { ...acknowledged, stack: ["S2", "S1"] };
  const graveyardSpell = { ...stack, graveyardStack: ["S2"] };
  const both = { stack: true, graveyard: true };
  assert.equal(priorityActivityHold(acknowledged, acknowledged, both), undefined);
  assert.equal(priorityActivityHold(acknowledged, stack, both), "New object on the stack");
  assert.equal(priorityActivityHold(acknowledged, { ...acknowledged, stack: [] }, both), undefined);
  assert.equal(priorityActivityHold(acknowledged, { ...acknowledged, graveyard: "A2" }, both), "Graveyard activity");
  assert.equal(priorityActivityHold(acknowledged, graveyardSpell, both), "Graveyard activity");
  assert.equal(priorityActivityHold(graveyardSpell, graveyardSpell, both), undefined);
  assert.equal(priorityActivityHold(acknowledged, graveyardSpell, { stack: false, graveyard: false }), undefined);
});

test("priority stops hold only the selected timing window, including the distinct damage steps", () => {
  assert.equal(selectedPriorityStop("precombat_main", "main", false, 0, ["combat"]), undefined);
  assert.equal(selectedPriorityStop("precombat_main", "main", false, 0, ["precombat_main"]), "First main phase");
  assert.equal(selectedPriorityStop("combat", "beginning_combat", false, 0, ["declare_attackers"]), undefined);
  assert.equal(selectedPriorityStop("combat", "declare_attackers", false, 0, ["declare_attackers"]), "After attackers are declared");
  assert.equal(selectedPriorityStop("combat", "combat_damage", true, 1, ["combat_damage"]), undefined);
  assert.equal(selectedPriorityStop("combat", "combat_damage", true, 1, ["first_strike_damage"]), "After first-strike damage");
  assert.equal(selectedPriorityStop("combat", "combat_damage", true, 2, ["first_strike_damage"]), undefined);
  assert.equal(selectedPriorityStop("combat", "combat_damage", true, 2, ["combat_damage"]), "After combat damage");
  assert.equal(selectedPriorityStop("combat", "end_combat", false, 0, ["combat"]), "End of combat");
});

test("optional-activation auto-pass leaves spell, land, and manual mana responses available", () => {
  const pass = { action: "pass" };
  const activation = { action: "activate", id: "activate:lantern", cost_summary: { sacrifice: true } };
  const window = { passWithOptionalActivations: true };
  assert.equal(findSafeAutoPass([pass, activation], window), pass);
  const ownMain = { ...window, activePlayer: "A", phase: "precombat_main", principalSeat: "A", stackDepth: 0 };
  assert.equal(findSafeAutoPass([pass], ownMain), null);
  assert.equal(findSafeAutoPass([pass, activation], ownMain), pass);
  for (const action of ["cast", "play_land", "undo_mana", "decline"]) {
    assert.equal(findSafeAutoPass([pass, activation, { action }], ownMain), null, action);
  }
});

test("an ignored activation survives renewal and stops again when its choices change", () => {
  const pass = { action: "pass" };
  const offered = {
    action: "activate", id: "activate:1", expiry_revision: 10, proposal_fingerprint: "first",
    cost_summary: { life: 1 }, form: { legal_refs: ["B"] },
  };
  const window = { ignoredActivations: new Map([[offered.id, activationStopSignature(offered)]]) };
  assert.equal(findSafeAutoPass([pass, offered], window), pass);
  const renewed = { ...offered, expiry_revision: 12, proposal_fingerprint: "renewed" };
  assert.equal(findSafeAutoPass([pass, renewed], window), pass);
  assert.equal(findSafeAutoPass([pass, { ...renewed, form: { legal_refs: ["B", "C"] } }], window), null);
  assert.equal(findSafeAutoPass([pass, { ...renewed, cost_summary: { life: 2 } }], window), null);
  assert.equal(findSafeAutoPass([pass, offered]), null);
});
