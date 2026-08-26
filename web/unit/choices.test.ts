import assert from "node:assert/strict";
import test from "node:test";

import {
  activeFields,
  choicesWithDefaults,
  executableChoices,
  initialChoices,
  updateModalTargetSelection,
  validateChoices,
} from "../src/choices.ts";
import type { ChoiceForm } from "../src/generated/protocol.ts";

function form(fields: ChoiceForm["fields"]): ChoiceForm {
  return { v: 1, fields, submit_label: "Submit" };
}

test("private mulligan-bottom refs require exactly the server count", () => {
  const bottom = form([
    {
      name: "cards",
      label: "Cards",
      control: "refs",
      required: true,
      minimum: 1,
      maximum: 1,
      options: [
        { value: "A01", label: "Forest" },
        { value: "A02", label: "Island" },
      ],
    },
  ]);
  const choices = initialChoices(bottom);
  assert.deepEqual(choices, { cards: [] });
  assert.match(validateChoices(bottom, choices)[0], /at least 1/);
  choices.cards = ["A02"];
  assert.deepEqual(validateChoices(bottom, choices), []);
  assert.deepEqual(executableChoices(bottom, choices), { cards: ["A02"] });
});

test("cost variants activate only their server-issued fields", () => {
  const cast: ChoiceForm = {
    v: 1,
    fields: [],
    submit_label: "Cast",
    variants: {
      field: "cost_option",
      default: "normal",
      options: [
        {
          value: "normal",
          label: "Normal",
          fields: [
            { name: "x", label: "X", control: "integer", minimum: 0, maximum: 4 },
          ],
        },
        {
          value: "pitch",
          label: "Pitch",
          fields: [
            {
              name: "exile_card",
              label: "Exile card",
              control: "ref",
              required: true,
              options: [{ value: "A03", label: "Blue card" }],
            },
          ],
        },
      ],
    },
  };
  const choices = initialChoices(cast);
  assert.deepEqual(choices, { cost_option: "normal", x: 0 });
  const pitch = choicesWithDefaults(cast, { ...choices, cost_option: "pitch" });
  assert.deepEqual(
    activeFields(cast, pitch).map((field) => field.name),
    ["exile_card"],
  );
  assert.equal(pitch.exile_card, "A03");
  assert.deepEqual(executableChoices(cast, pitch), {
    cost_option: "pitch",
    exile_card: "A03",
  });
});

test("dependent top ordering follows only object-map choices sent to top", () => {
  const ordered = form([
    {
      name: "decisions",
      label: "Decisions",
      control: "object_map",
      minimum: 2,
      keys: ["A01", "A02"],
      options: [
        { value: "pay_life", label: "Pay life" },
        { value: "top", label: "Put on top" },
      ],
    },
    {
      name: "top_order",
      label: "Top order",
      control: "refs",
      ordered: true,
      options_from_map: "decisions",
      required_value: "top",
      minimum: 0,
      maximum: 2,
      options: [
        { value: "A01", label: "First" },
        { value: "A02", label: "Second" },
      ],
    },
  ]);
  const choices = initialChoices(ordered);
  choices.decisions = { A01: "pay_life", A02: "top" };
  const topOrder = activeFields(ordered, choices)[1];
  assert.deepEqual(topOrder.legal_refs, ["A02"]);
  assert.deepEqual(topOrder.options, [
    { value: "A02", label: "Second", available: true },
  ]);
  assert.match(validateChoices(ordered, choices)[0], /Top order requires/);
  choices.top_order = ["A02"];
  assert.deepEqual(validateChoices(ordered, choices), []);
});

test("modal target forms validate modes and group cardinality", () => {
  const target = form([
    {
      name: "targets",
      label: "Targets and modes",
      control: "targets",
      required: true,
      schema: {
        legal_modes: ["destroy", "draw"],
        min_modes: 1,
        max_modes: 1,
        mode_schemas: {
          destroy: {
            groups: [
              { id: "permanent", label: "Permanent", min: 1, max: 1, legal_refs: ["B01"] },
            ],
          },
          draw: { groups: [] },
        },
      },
    },
  ]);
  const choices = initialChoices(target);
  assert.match(validateChoices(target, choices)[0], /Choose at least 1 mode/);
  choices.modes = ["destroy"];
  assert.match(validateChoices(target, choices)[0], /Permanent requires/);
  choices.targets = { permanent: ["B01"] };
  assert.deepEqual(validateChoices(target, choices), []);
  assert.deepEqual(executableChoices(target, choices), {
    targets: { permanent: ["B01"] },
    modes: ["destroy"],
  });
});

test("modal clicks use printed order and retain unaffected target groups", () => {
  const schema = {
    legal_modes: ["mode_1", "mode_2", "mode_3"],
    min_modes: 2,
    max_modes: 2,
    mode_schemas: {
      mode_1: {
        groups: [{ id: "target_1", max: 1, legal_refs: ["A", "B"] }],
      },
      mode_2: { groups: [] },
      mode_3: {
        groups: [{ id: "target_3", max: 1, legal_refs: ["C", "D"] }],
      },
    },
  };
  const third = updateModalTargetSelection(schema, [], {}, "mode_3", true);
  const first = updateModalTargetSelection(
    schema,
    third.modes,
    { target_3: ["D"] },
    "mode_1",
    true,
  );

  assert.deepEqual(first, {
    modes: ["mode_1", "mode_3"],
    targets: { target_3: ["D"] },
  });
  assert.deepEqual(
    updateModalTargetSelection(
      schema,
      first.modes,
      { target_1: ["A"], target_3: ["D"] },
      "mode_1",
      false,
    ),
    { modes: ["mode_3"], targets: { target_3: ["D"] } },
  );
});

test("fully ordered server choices initialize in projected order", () => {
  const triggers = form([
    {
      name: "triggers",
      label: "Triggers",
      control: "refs",
      ordered: true,
      minimum: 2,
      maximum: 2,
      options: [
        { value: "S2", label: "Second" },
        { value: "S1", label: "First" },
      ],
    },
  ]);
  const choices = initialChoices(triggers);
  assert.deepEqual(choices.triggers, ["S2", "S1"]);
  assert.deepEqual(validateChoices(triggers, choices), []);
});

test("ordered library partitions require every legal card exactly once", () => {
  const scry = form([
    {
      name: "cards",
      label: "Cards",
      control: "ordered_partition",
      required: true,
      options: [
        { value: "A01", label: "First" },
        { value: "A02", label: "Second" },
        { value: "A03", label: "Third" },
      ],
    },
  ]);
  const choices = initialChoices(scry);
  assert.deepEqual(choices.cards, {
    top: ["A01", "A02", "A03"],
    bottom: [],
  });
  assert.deepEqual(validateChoices(scry, choices), []);

  choices.cards = { top: ["A03", "A01"], bottom: ["A02"] };
  assert.deepEqual(validateChoices(scry, choices), []);
  assert.deepEqual(executableChoices(scry, choices), {
    cards: { top: ["A03", "A01"], bottom: ["A02"] },
  });

  choices.cards = { top: ["A01"], bottom: ["A01"] };
  assert.match(validateChoices(scry, choices).join(" "), /every card|same card/);
});

test("ordered library partitions honor server-issued Surveil destinations and ordering", () => {
  const surveil = form([
    {
      name: "cards",
      label: "Cards",
      control: "ordered_partition",
      required: true,
      options: [
        { value: "A01", label: "First" },
        { value: "A02", label: "Second" },
      ],
      partitions: {
        graveyard: {
          label: "Graveyard",
          order: "graveyard_top_to_bottom",
        },
        top: { label: "Top of library", order: "top_to_bottom" },
      },
    },
  ]);
  const choices = initialChoices(surveil);
  assert.deepEqual(choices.cards, { top: ["A01", "A02"], graveyard: [] });
  choices.cards = { top: ["A02"], graveyard: ["A01"] };
  assert.deepEqual(validateChoices(surveil, choices), []);
  assert.deepEqual(executableChoices(surveil, choices), {
    cards: { top: ["A02"], graveyard: ["A01"] },
  });
});

test("mana modes preserve an exact server-issued bundle", () => {
  const mana = form([
    {
      name: "mana_output",
      label: "Mana to add",
      control: "mana_modes",
      required: true,
      options: [
        { value: { U: 1 }, label: "Add {U}" },
        { value: { B: 1 }, label: "Add {B}" },
      ],
    },
  ]);
  const choices = initialChoices(mana);
  assert.deepEqual(choices, { mana_output: { U: 1 } });
  assert.deepEqual(validateChoices(mana, choices), []);
  choices.mana_output = { G: 1 };
  assert.match(validateChoices(mana, choices)[0], /legal mana choice/);
});

test("destructive boolean choices require the exact server-issued confirmation", () => {
  const concede = form([
    {
      name: "confirm_concede",
      label: "Concede game",
      control: "boolean",
      required: true,
      legal_values: [true],
      default: true,
    },
  ]);
  const choices = initialChoices(concede);
  assert.deepEqual(choices, { confirm_concede: true });
  assert.deepEqual(validateChoices(concede, choices), []);
  choices.confirm_concede = false;
  assert.match(validateChoices(concede, choices)[0], /explicit confirmation/);
  choices.confirm_concede = "true";
  assert.match(validateChoices(concede, choices)[0], /explicit confirmation/);
});

test("assignment maps enforce server-issued conditional group minimums", () => {
  const blockers = form([
    {
      name: "blocks",
      label: "Blockers and attackers",
      control: "assignment_map",
      minimum_group_sizes: { A17: 2 },
      rows: [],
    },
  ]);
  assert.deepEqual(validateChoices(blockers, { blocks: {} }), []);
  assert.match(
    validateChoices(blockers, { blocks: { B10: "A17" } })[0],
    /at least 2 assignments/,
  );
  assert.deepEqual(
    validateChoices(blockers, {
      blocks: { B10: "A17", B11: "A17" },
    }),
    [],
  );
});

test("copy targets preserve defaults and validate each copy", () => {
  const copies = form([
    {
      name: "copy_targets",
      label: "Copy targets",
      control: "copy_targets",
      copy_count: 2,
      copies: [
        {
          default_targets: ["B01"],
          target_schema: {
            groups: [{ id: "target", min: 1, max: 1, legal_refs: ["B01", "C01"] }],
          },
        },
        {
          default_targets: ["B01"],
          target_schema: {
            groups: [{ id: "target", min: 1, max: 1, legal_refs: ["B01", "C01"] }],
          },
        },
      ],
    },
  ]);
  const choices = initialChoices(copies);
  assert.deepEqual(choices.copy_targets, [["B01"], ["B01"]]);
  assert.deepEqual(validateChoices(copies, choices), []);
  choices.copy_targets = [{ target: ["C01"] }, { target: [] }];
  assert.match(validateChoices(copies, choices)[0], /Copy 2/);
});
