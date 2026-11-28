import assert from "node:assert/strict";
import test from "node:test";

import { activationStopSignature, findSafeAutoPass, priorityActivityHold, selectedPriorityStop } from "../src/tableAutomation.ts";
import { loopFrame, opponentResponded, repeatableOffer } from "../src/loopShortcuts.ts";
import type { ProjectedView } from "../src/protocol.ts";

test("loop playback recognizes its own new stack occurrences and observes public opponent responses", () => {
  const ability = { id: "S31", kind: "activated_ability", ctl: "A", label: "Equip", targets: ["A7"] };
  const view = {
    principal: "pilot:A", decision: { kind: "priority", actor: "A" },
    state: { stack: [ability], turn: { seq: 3 }, players: {} },
    events: [
      { id: 3, c: "stack.cast", a: "C", pub: true },
      { id: 4, c: "priority.pass", a: "B", pub: true },
      { id: 5, c: "stack.cast", a: "A", pub: true },
      { id: 6, c: "stack.cast", a: "B", pub: false },
      { id: 7, c: "stack.activate", a: "B", pub: true },
    ],
  } as unknown as ProjectedView;
  const frame = loopFrame(view);
  const withAbility = (changes: object) => ({ ...view, state: { ...view.state, stack: [{ ...ability, ...changes }] } });
  assert.equal(loopFrame(withAbility({ id: "S32" })), frame);
  assert.notEqual(loopFrame(withAbility({ id: "S32", targets: ["A8"] })), frame);
  assert.notEqual(loopFrame(withAbility({ ctl: "B" })), frame);
  assert.equal(opponentResponded(view, 6), true);
  assert.equal(opponentResponded({ ...view, events: view.events.slice(0, 4) }, 3), false);
});

test("loop observation tolerates only renewed capabilities and the pilot's accumulating mana", () => {
  const action = { id: "activate:A12:ab1", action: "activate", expiry_revision: 20, proposal_fingerprint: "old" };
  const view = {
    principal: "pilot:A", decision: { kind: "priority", actor: "A" },
    state: { turn: { seq: 6, phase: "precombat_main", step: "main", active: "A" },
      players: { A: { life: 40, mana: { G: 1 }, bf: [{ id: "A12", tap: 0 }] },
        B: { life: 40, mana: { U: 1 }, bf: [] } }, stack: [] },
  } as unknown as ProjectedView;
  const initial = loopFrame(view);
  assert.ok(initial);
  const players = { A: { life: 40, mana: { G: 4 }, bf: [{ id: "A12", tap: 0 }] },
    B: { life: 40, mana: { U: 1 }, bf: [] } };
  assert.equal(loopFrame({ ...view, state: { ...view.state, players } }), initial);
  players.B.mana.U = 0;
  assert.notEqual(loopFrame({ ...view, state: { ...view.state, players } }), initial);
  assert.equal(loopFrame({ ...view, decision: { ...view.decision!, kind: "semantic.search" } }), null);
  assert.equal(repeatableOffer({ ...action, expiry_revision: 21, proposal_fingerprint: "renewed" }), repeatableOffer(action));
  assert.notEqual(repeatableOffer({ ...action, id: "activate:A14:ab1" }), repeatableOffer(action));
});

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
  assert.equal(selectedPriorityStop("ending", "end_step", false, 0, ["before_my_turn"], true), "End step before your turn");
  assert.equal(selectedPriorityStop("ending", "end_step", false, 0, ["before_my_turn"], false), undefined);
  assert.equal(selectedPriorityStop("ending", "cleanup", false, 0, ["before_my_turn"], true), undefined);
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
