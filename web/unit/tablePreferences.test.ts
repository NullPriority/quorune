import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_TABLE_PREFERENCES,
  effectiveCardScale,
  parseTablePreferences,
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
  assert.equal(value.cardScale, 1.2);
  assert.equal(value.rightRailWidth, 410);
  assert.deepEqual(value.rightRailOrder, ["activity", "stack", "card"]);
  assert.equal(value.inspectorCollapsed, true);
  assert.equal(value.boardDensity, "compact");
  assert.equal(value.compactPhaseRail, true);
  assert.equal(value.activityVisible, false);
});
