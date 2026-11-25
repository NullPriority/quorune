import assert from "node:assert/strict";
import test from "node:test";

import { activeCombatPriorityStop, findSafeAutoPass, turnYieldPause } from "../src/tableAutomation.ts";

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

test("a current-turn yield stops at its boundaries and requires a new arm for the next turn", () => {
  const armed = { turn: 4, phase: "precombat_main", stack: ["S1"] };
  assert.equal(turnYieldPause(armed, armed, "priority", true), undefined);
  assert.equal(turnYieldPause(armed, { ...armed, turn: 5 }, "priority", true), "Turn ended");
  assert.equal(turnYieldPause(armed, { ...armed, stack: ["S2", "S1"] }, "priority", true), "New object on the stack");
  const combat = { ...armed, phase: "combat" };
  assert.equal(turnYieldPause(armed, combat, "priority", true), undefined);
  assert.equal(turnYieldPause(armed, combat, "priority", false), "A response is available");
  assert.equal(turnYieldPause(armed, combat, "combat.attackers", false), "A decision is required");
  assert.equal(turnYieldPause(armed, armed, "combat.blockers", false), "A decision is required");
  assert.equal(turnYieldPause(armed, armed, "priority", false), "A response is available");
});

test("an ignored activation stops again when its offered targets change", () => {
  const pass = { action: "pass" };

test("combat stops hold only the selected timing window", () => {
  assert.equal(activeCombatPriorityStop("precombat_main", "main", false, ["combat"]), undefined);
  assert.equal(activeCombatPriorityStop("combat", "beginning_combat", false, ["declare_attackers"]), undefined);
  assert.equal(activeCombatPriorityStop("combat", "declare_attackers", false, ["declare_attackers"]), "After attackers are declared");
  assert.equal(activeCombatPriorityStop("combat", "combat_damage", true, ["combat_damage"]), undefined);
  assert.equal(activeCombatPriorityStop("combat", "combat_damage", true, ["first_strike_damage"]), "After first-strike damage");
  assert.equal(activeCombatPriorityStop("combat", "end_combat", false, ["combat"]), "End of combat");
});
  const offered = { action: "activate", id: "activate:1", form: { legal_refs: ["B"] } };
  const window = { ignoredActivations: new Map([[offered.id, JSON.stringify(offered)]]) };
  assert.equal(findSafeAutoPass([pass, offered], window), pass);
  const changed = { ...offered, form: { legal_refs: ["B", "C"] } };
  assert.equal(findSafeAutoPass([pass, changed], window), null);
  assert.equal(findSafeAutoPass([pass, offered]), null);
});
