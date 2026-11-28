import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_TABLE_PREFERENCES,
  effectiveCardScale,
  parseTablePreferences,
  priorityStopsForTurn,
  tablePreferenceStyle,
} from "../src/tablePreferences.ts";

test("table preferences default to automatic mana and automatic empty passes", () => {
  assert.deepEqual(parseTablePreferences(null), DEFAULT_TABLE_PREFERENCES);
  assert.deepEqual(parseTablePreferences("not json"), DEFAULT_TABLE_PREFERENCES);
});

test("opponent focus and per-zone multipliers survive saved settings", () => {
  const layout = parseTablePreferences(JSON.stringify({
    opponentView: "C", cardScale: 1.2,
    zoneCardScale: { hand: 0.75, battlefield: 3, command: "invalid", forbidden: 2 },
  }));
  assert.equal(layout.opponentView, "C");
  assert.deepEqual(layout.zoneCardScale, { hand: 0.75, battlefield: 1.6 });
  assert.ok(Math.abs(effectiveCardScale(layout, "hand") - 0.9) < 0.00001);
  assert.equal(effectiveCardScale(layout, "exile"), 1.2);
  assert.equal(parseTablePreferences('{"opponentView":"E"}').opponentView, "all");
});

test("table preferences preserve valid toggles and repair invalid fields", () => {
  const migrated = parseTablePreferences('{"autoMana":false,"autoPass":false}');
  assert.equal(migrated.autoMana, false);
  assert.equal(migrated.autoPass, false);
  assert.equal(migrated.handPanelHeight, DEFAULT_TABLE_PREFERENCES.handPanelHeight);
  const repaired = parseTablePreferences('{"autoMana":"yes","autoPass":false,"handPanelHeight":5000,"rightRailOrder":["card","card"]}');
  assert.equal(repaired.autoMana, true);
  assert.equal(repaired.autoPass, false);
  assert.equal(repaired.handPanelHeight, 650);
  assert.deepEqual(repaired.rightRailOrder, DEFAULT_TABLE_PREFERENCES.rightRailOrder);
});

test("bounded version-two layout preferences survive parsing", () => {
  const value = parseTablePreferences(JSON.stringify({
    handPanelHeight: 420,
    handWidth: 62, handArtFocused: true,
    opponentWidths: { A: 115, B: 450, E: 70 },
    seatPriorityStops: { D: ["end_step", "before_my_turn", "unknown"], E: ["end_step"] },
    handAutoCollapse: false,
    cardScale: 1.2,
    rightRailWidth: 410,
    rightRailOrder: ["activity", "stack", "card"],
    inspectorCollapsed: true,
    boardDensity: "compact",
    compactPhaseRail: true,
    activityVisible: false,
  }));
  assert.equal(value.handPanelHeight, 420);
  assert.equal(value.handWidth, 62);
  assert.equal(value.handArtFocused, true);
  assert.deepEqual(value.opponentWidths, { A: 115, B: 200 });
  assert.deepEqual(value.seatPriorityStops, { D: ["end_step"] });
  assert.deepEqual(priorityStopsForTurn(value, "D"), ["end_step"]);
  assert.deepEqual(priorityStopsForTurn(value, "B"), []);
  assert.equal(value.cardScale, 1.2);
  assert.equal(value.rightRailWidth, 410);
  assert.deepEqual(value.rightRailOrder, ["activity", "stack", "card"]);
  assert.equal(value.inspectorCollapsed, true);
  assert.equal(value.boardDensity, "compact");
  assert.equal(value.compactPhaseRail, true);
  assert.equal(value.activityVisible, false);
});

test("battlefield lanes default to equal multipliers and preserve saved sizes", () => {
  const variables = (raw: string | null) => tablePreferenceStyle(parseTablePreferences(raw)) as Record<string, string | number>;
  assert.deepEqual(parseTablePreferences(null).battlefieldLaneScale, {});
  assert.equal(variables(null)["--creatures-lane-scale"], 1);
  assert.equal(variables(null)["--lands-lane-scale"], 1);
  assert.equal(variables(null)["--support-lane-scale"], 1);

  const saved = parseTablePreferences(JSON.stringify({
    cardScale: 1.2,
    zoneCardScale: { battlefield: 0.8 },
    battlefieldLaneScale: { creatures: 1.25, lands: 0, support: 3, other: 1.2 },
  }));
  assert.deepEqual(saved.battlefieldLaneScale, { creatures: 1.25, lands: 0.6, support: 1.6 });
  const serialized = JSON.stringify(saved);
  assert.deepEqual(parseTablePreferences(serialized).battlefieldLaneScale, saved.battlefieldLaneScale);
  assert.equal(variables(serialized)["--creatures-lane-scale"], 1.25);
  assert.equal(variables(serialized)["--battlefield-card-scale"], 0.96);
  assert.deepEqual(parseTablePreferences('{"battlefieldLaneScale":{"lands":"1.5"}}').battlefieldLaneScale, {});
});
