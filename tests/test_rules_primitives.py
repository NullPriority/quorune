from __future__ import annotations

import json
from pathlib import Path
import random
import unittest

from quorune.continuous_effects import (
    CharacteristicState,
    ContinuousEffect,
    ContinuousOperation,
    Layer,
    evaluate_continuous_effects,
    order_continuous_effects,
)
from quorune.object_predicate import ObjectQuerySpec
from quorune.replacement_effects import (
    ReplaceableEvent,
    ReplacementClass,
    ReplacementEffect,
    ReplacementEffectError,
    apply_replacement,
    replacement_choice,
    resolve_replacements,
)


def effect(
    effect_id,
    layer,
    sublayer,
    timestamp,
    op,
    value,
    *,
    depends_on=(),
    source_present=True,
):
    return ContinuousEffect(
        effect_id=effect_id,
        source_id=f"source:{effect_id}",
        layer=layer,
        sublayer=sublayer,
        timestamp=timestamp,
        operations=(ContinuousOperation(op, value),),
        depends_on=depends_on,
        source_present=source_present,
    )


class ContinuousLayerTests(unittest.TestCase):
    def test_layer_order_dominates_timestamp(self):
        values = [
            effect(
                "ability",
                Layer.ABILITY,
                "6",
                1,
                "add_ability",
                "Flying",
            ),
            effect(
                "copy",
                Layer.COPY,
                "1a",
                99,
                "copy_values",
                {"name": "Copied", "abilities": []},
            ),
            effect(
                "type",
                Layer.TYPE,
                "4",
                50,
                "add_types",
                ["Artifact"],
            ),
        ]
        result = evaluate_continuous_effects(
            CharacteristicState(
                name="Original",
                card_types={"Creature"},
            ),
            reversed(values),
        )
        self.assertEqual(
            ("copy", "type", "ability"),
            result.applied_effects,
        )
        self.assertEqual("Copied", result.characteristics["name"])
        self.assertEqual(
            ["Artifact", "Creature"],
            result.characteristics["card_types"],
        )
        self.assertEqual(
            ["Flying"], result.characteristics["abilities"]
        )

    def test_power_toughness_sublayers_set_modify_then_switch(self):
        result = evaluate_continuous_effects(
            CharacteristicState(
                name="Creature",
                card_types={"Creature"},
                power=1,
                toughness=4,
            ),
            [
                effect(
                    "switch",
                    Layer.POWER_TOUGHNESS,
                    "7d",
                    1,
                    "switch_power_toughness",
                    None,
                ),
                effect(
                    "modify",
                    Layer.POWER_TOUGHNESS,
                    "7c",
                    3,
                    "modify_power_toughness",
                    [1, -1],
                ),
                effect(
                    "set",
                    Layer.POWER_TOUGHNESS,
                    "7b",
                    8,
                    "set_power_toughness",
                    [2, 5],
                ),
            ],
        )
        self.assertEqual(4, result.characteristics["power"])
        self.assertEqual(3, result.characteristics["toughness"])

    def test_dependency_overrides_timestamp_and_cycles_are_audited(self):
        dependent = effect(
            "dependent",
            Layer.COLOR,
            "5",
            1,
            "set_colors",
            ["U"],
            depends_on=("base",),
        )
        base = effect(
            "base",
            Layer.COLOR,
            "5",
            20,
            "set_colors",
            ["G"],
        )
        ordered, cycles = order_continuous_effects(
            [dependent, base]
        )
        self.assertEqual(["base", "dependent"], [
            value.effect_id for value in ordered
        ])
        self.assertFalse(cycles)

        left = effect(
            "left",
            Layer.COLOR,
            "5",
            2,
            "set_colors",
            ["W"],
            depends_on=("right",),
        )
        right = effect(
            "right",
            Layer.COLOR,
            "5",
            1,
            "set_colors",
            ["B"],
            depends_on=("left",),
        )
        ordered, cycles = order_continuous_effects([left, right])
        self.assertEqual(["right", "left"], [
            value.effect_id for value in ordered
        ])
        self.assertEqual([("right", "left")], cycles)

    def test_cycle_fallback_preserves_dependencies_from_outside_the_cycle(self):
        left = effect("left", Layer.COLOR, "5", 3, "set_colors", ["R"], depends_on=("right",))
        right = effect("right", Layer.COLOR, "5", 2, "set_colors", ["G"], depends_on=("left",))
        dependent = effect("dependent", Layer.COLOR, "5", 1, "set_colors", ["U"], depends_on=("left",))
        for values in ((left, right, dependent), (dependent, right, left)):
            with self.subTest(order=[value.effect_id for value in values]):
                result = evaluate_continuous_effects(CharacteristicState(name="Synthetic"), values)
                self.assertEqual(("right", "left", "dependent"), result.applied_effects)
                self.assertEqual((("right", "left"),), result.dependency_cycles)
                self.assertEqual(["U"], result.characteristics["colors"])
        legacy = evaluate_continuous_effects(
            CharacteristicState(name="Synthetic"), (left, right, dependent),
            context={"continuous_dependency_order_version": None},
        )
        self.assertEqual(("dependent", "right", "left"), legacy.applied_effects)
        self.assertEqual((("dependent", "right", "left"),), legacy.dependency_cycles)
        self.assertEqual(["R"], legacy.characteristics["colors"])

    def test_absent_source_and_predicate_are_inapplicable(self):
        result = evaluate_continuous_effects(
            CharacteristicState(
                name="Object",
                card_types={"Artifact"},
            ),
            [
                effect(
                    "gone",
                    Layer.ABILITY,
                    "6",
                    1,
                    "add_ability",
                    "Hexproof",
                    source_present=False,
                ),
                ContinuousEffect(
                    effect_id="creatures",
                    source_id="source:creatures",
                    layer=Layer.ABILITY,
                    sublayer="6",
                    timestamp=2,
                    operations=(
                        ContinuousOperation(
                            "add_ability", "Vigilance"
                        ),
                    ),
                    applies=ObjectQuerySpec(types_all=("creature",)),
                ),
            ],
        )
        self.assertEqual(
            ("gone", "creatures"), result.inapplicable_effects
        )
        self.assertFalse(result.characteristics["abilities"])

    def test_effect_order_is_stable_under_input_mutation(self):
        values = [
            effect(
                f"e{index}",
                Layer.ABILITY,
                "6",
                index,
                "add_ability",
                f"Ability {index}",
            )
            for index in range(30)
        ]
        expected = [value.effect_id for value in values]
        randomizer = random.Random(613)
        for _ in range(50):
            randomizer.shuffle(values)
            ordered, cycles = order_continuous_effects(values)
            self.assertEqual(
                expected, [value.effect_id for value in ordered]
            )
            self.assertFalse(cycles)


class ReplacementOrderingTests(unittest.TestCase):
    def setUp(self):
        self.event = ReplaceableEvent(
            event_id="event:1",
            kind="zone.change",
            affected_player="A",
            payload={
                "card": "A01",
                "from": "battlefield",
                "destination": "graveyard",
            },
        )

    @staticmethod
    def replacement(
        effect_id,
        replacement_class,
        destination,
        *,
        optional=False,
        conditions=None,
    ):
        return ReplacementEffect(
            effect_id=effect_id,
            source_id=f"source:{effect_id}",
            event_kind="zone.change",
            replacement_class=replacement_class,
            conditions=conditions or {},
            operations=(
                {
                    "op": "set",
                    "field": "destination",
                    "value": destination,
                },
            ),
            optional=optional,
        )

    def test_contract_traces_every_cr_616_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "replacement-ordering.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "616",
                "616.1",
                "616.1a",
                "616.1b",
                "616.1c",
                "616.1d",
                "616.1e",
                "616.1f",
                "616.1g",
                "616.2",
            },
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("616")
            },
        )

    def test_contract_traces_every_cr_614_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "unconditional-enters-tapped.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "614",
                "614.1",
                "614.1a",
                "614.1b",
                "614.1c",
                "614.1d",
                "614.1e",
                "614.2",
                "614.3",
                "614.4",
                "614.5",
                "614.6",
                "614.7",
                "614.7a",
                "614.8",
                "614.9",
                "614.10",
                "614.10a",
                "614.10b",
                "614.11",
                "614.11a",
                "614.11b",
                "614.12",
                "614.12a",
                "614.12b",
                "614.12c",
                "614.13",
                "614.13a",
                "614.13b",
                "614.13c",
                "614.14",
                "614.15",
                "614.16",
                "614.17",
                "614.17a",
                "614.17b",
                "614.17c",
                "614.17d",
            },
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("614")
            },
        )

    def test_uncompiled_replacement_families_fail_closed(self):
        for operation in (
            "skip",
            "regenerate",
            "redirect",
            "prohibit",
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                    ReplacementEffectError,
                    "Unsupported replacement operation",
                ):
                    ReplacementEffect(
                        effect_id=operation,
                        source_id=f"source:{operation}",
                        event_kind="zone.change",
                        replacement_class=ReplacementClass.OTHER,
                        operations=({"op": operation},),
                    )

    def test_affected_player_chooses_from_current_priority_class(self):
        effects = [
            self.replacement(
                "z-option", ReplacementClass.OTHER, "exile"
            ),
            self.replacement(
                "a-option", ReplacementClass.OTHER, "library"
            ),
            self.replacement(
                "not-applicable",
                ReplacementClass.OTHER,
                "hand",
                conditions={"from": "graveyard"},
            ),
        ]

        choice = replacement_choice(self.event, effects)

        self.assertEqual("A", choice.chooser)
        self.assertEqual(("a-option", "z-option"), choice.options)
        changed = apply_replacement(choice, effects, "z-option")
        self.assertEqual("exile", changed.payload["destination"])

    def test_self_replacement_must_be_chosen_before_other_effects(self):
        effects = [
            self.replacement(
                "other", ReplacementClass.OTHER, "exile"
            ),
            self.replacement(
                "self",
                ReplacementClass.SELF_REPLACEMENT,
                "library",
            ),
        ]
        choice = replacement_choice(self.event, effects)
        self.assertEqual("A", choice.chooser)
        self.assertEqual(("self",), choice.options)
        changed = apply_replacement(choice, effects, "self")
        next_choice = replacement_choice(changed, effects)
        self.assertEqual(("other",), next_choice.options)

    def test_enters_control_precedes_copy_back_face_and_other(self):
        effects = [
            self.replacement(
                "other", ReplacementClass.OTHER, "other"
            ),
            self.replacement(
                "back-face",
                ReplacementClass.ENTERS_BACK_FACE,
                "back-face",
            ),
            self.replacement(
                "copy", ReplacementClass.ENTERS_COPY, "copy"
            ),
            self.replacement(
                "control",
                ReplacementClass.ENTERS_CONTROL,
                "control",
            ),
        ]

        choice = replacement_choice(self.event, effects)

        self.assertEqual(
            ReplacementClass.ENTERS_CONTROL,
            choice.replacement_class,
        )
        self.assertEqual(("control",), choice.options)

    def test_enters_copy_precedes_back_face_and_other(self):
        effects = [
            self.replacement(
                "other", ReplacementClass.OTHER, "other"
            ),
            self.replacement(
                "back-face",
                ReplacementClass.ENTERS_BACK_FACE,
                "back-face",
            ),
            self.replacement(
                "copy", ReplacementClass.ENTERS_COPY, "copy"
            ),
        ]

        choice = replacement_choice(self.event, effects)

        self.assertEqual(ReplacementClass.ENTERS_COPY, choice.replacement_class)
        self.assertEqual(("copy",), choice.options)

    def test_enters_back_face_precedes_other(self):
        effects = [
            self.replacement(
                "other", ReplacementClass.OTHER, "other"
            ),
            self.replacement(
                "back-face",
                ReplacementClass.ENTERS_BACK_FACE,
                "back-face",
            ),
        ]

        choice = replacement_choice(self.event, effects)

        self.assertEqual(
            ReplacementClass.ENTERS_BACK_FACE,
            choice.replacement_class,
        )
        self.assertEqual(("back-face",), choice.options)

    def test_any_effect_in_the_current_class_may_be_chosen(self):
        effects = [
            self.replacement(
                "exile", ReplacementClass.OTHER, "exile"
            ),
            self.replacement(
                "library", ReplacementClass.OTHER, "library"
            ),
        ]

        choice = replacement_choice(self.event, effects)
        changed = apply_replacement(choice, effects, "library")

        self.assertEqual(("exile", "library"), choice.options)
        self.assertEqual("library", changed.payload["destination"])

    def test_each_effect_applies_at_most_once_to_one_event(self):
        effects = [
            self.replacement(
                "first", ReplacementClass.OTHER, "exile"
            ),
            self.replacement(
                "second", ReplacementClass.OTHER, "library"
            ),
        ]
        changed = resolve_replacements(
            self.event,
            effects,
            selections=["first", "second"],
        )
        self.assertEqual("library", changed.payload["destination"])
        self.assertEqual(
            ("first", "second"), changed.applied_effects
        )
        self.assertIsNone(replacement_choice(changed, effects))

    def test_applicability_is_recomputed_after_each_replacement(self):
        effects = [
            self.replacement(
                "to-exile", ReplacementClass.OTHER, "exile"
            ),
            self.replacement(
                "exile-to-library",
                ReplacementClass.OTHER,
                "library",
                conditions={"destination": "exile"},
            ),
            self.replacement(
                "graveyard-only",
                ReplacementClass.OTHER,
                "hand",
                conditions={"destination": "graveyard"},
            ),
        ]

        first = replacement_choice(self.event, effects)
        self.assertEqual(
            ("graveyard-only", "to-exile"), first.options
        )
        changed = apply_replacement(first, effects, "to-exile")
        second = replacement_choice(changed, effects)

        self.assertEqual(("exile-to-library",), second.options)
        final = apply_replacement(
            second, effects, "exile-to-library"
        )
        self.assertEqual("library", final.payload["destination"])
        self.assertIsNone(replacement_choice(final, effects))

    def test_malformed_nested_event_operation_fails_closed(self):
        nested = ReplacementEffect(
            effect_id="nested",
            source_id="source:nested",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            operations=(
                {
                    "op": "nested_event",
                    "event": {
                        "kind": "damage",
                        "amount": 1,
                    },
                },
            ),
        )

        choice = replacement_choice(self.event, [nested])

        with self.assertRaisesRegex(
            ReplacementEffectError,
            "unknown field",
        ):
            apply_replacement(choice, [nested], "nested")

    def test_optional_decline_is_recorded_and_not_offered_again(self):
        optional = self.replacement(
            "optional",
            ReplacementClass.OTHER,
            "exile",
            optional=True,
        )
        changed = resolve_replacements(
            self.event,
            [optional],
            selections=[None],
        )
        self.assertEqual(
            "graveyard", changed.payload["destination"]
        )
        self.assertEqual(("optional",), changed.applied_effects)

    def test_mandatory_replacement_cannot_be_declined(self):
        mandatory = self.replacement(
            "mandatory",
            ReplacementClass.OTHER,
            "exile",
        )
        choice = replacement_choice(self.event, [mandatory])
        with self.assertRaises(ReplacementEffectError):
            apply_replacement(choice, [mandatory], None)

    def test_prevention_never_prevents_more_than_remaining_damage(self):
        event = ReplaceableEvent(
            event_id="damage:1",
            kind="damage",
            affected_player="B",
            payload={"amount": 2},
        )
        prevention = ReplacementEffect(
            effect_id="prevent",
            source_id="shield",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            operations=({"op": "prevent", "amount": 5},),
        )
        changed = resolve_replacements(
            event, [prevention], selections=["prevent"]
        )
        self.assertEqual(0, changed.payload["amount"])
        self.assertEqual(2, changed.payload["prevented"])


class PreventionEffectTests(unittest.TestCase):
    @staticmethod
    def event(
        event_id="damage:1",
        *,
        amount=3,
        unpreventable=False,
        source_color="red",
    ):
        return ReplaceableEvent(
            event_id=event_id,
            kind="damage",
            affected_player="B",
            payload={
                "amount": amount,
                "unpreventable": unpreventable,
                "source_color": source_color,
            },
        )

    @staticmethod
    def prevention(
        effect_id="prevent",
        *,
        amount=1,
        conditions=None,
        operations=None,
    ):
        return ReplacementEffect(
            effect_id=effect_id,
            source_id=f"source:{effect_id}",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            conditions=conditions or {},
            operations=operations
            or ({"op": "prevent", "amount": amount},),
        )

    def test_contract_traces_every_cr_615_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "prevention-effects.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "615",
                "615.1",
                "615.1a",
                "615.2",
                "615.3",
                "615.4",
                "615.5",
                "615.6",
                "615.7",
                "615.8",
                "615.9",
                "615.10",
                "615.11",
                "615.12",
                "615.12a",
                "615.13",
            },
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("615")
            },
        )

    def test_prevention_applies_only_to_matching_damage_events(self):
        prevention = self.prevention(
            conditions={"source_color": "red"}
        )
        nondamage = ReplaceableEvent(
            event_id="draw:1",
            kind="draw",
            affected_player="B",
            payload={"amount": 1, "source_color": "red"},
        )

        self.assertIsNone(replacement_choice(nondamage, [prevention]))
        self.assertIsNone(
            replacement_choice(
                self.event(source_color="blue"),
                [prevention],
            )
        )
        self.assertEqual(
            ("prevent",),
            replacement_choice(
                self.event(source_color="red"),
                [prevention],
            ).options,
        )

    def test_prevention_produces_a_modified_damage_event(self):
        prevention = self.prevention(amount=2)

        changed = resolve_replacements(
            self.event(amount=3),
            [prevention],
            selections=["prevent"],
        )

        self.assertEqual(1, changed.payload["amount"])
        self.assertEqual(2, changed.payload["prevented"])
        self.assertEqual(("prevent",), changed.applied_effects)

    def test_static_prevention_applies_separately_to_each_event(self):
        prevention = self.prevention(amount=1)

        first = resolve_replacements(
            self.event("damage:1", amount=3),
            [prevention],
            selections=["prevent"],
        )
        second = resolve_replacements(
            self.event("damage:2", amount=2),
            [prevention],
            selections=["prevent"],
        )

        self.assertEqual(2, first.payload["amount"])
        self.assertEqual(1, second.payload["amount"])
        self.assertEqual(("prevent",), first.applied_effects)
        self.assertEqual(("prevent",), second.applied_effects)

    def test_unpreventable_damage_applies_effect_once_without_preventing(self):
        prevention = self.prevention(
            operations=(
                {"op": "prevent", "amount": 2},
                {
                    "op": "set",
                    "field": "prevented_by",
                    "value": "applied",
                },
            )
        )

        changed = resolve_replacements(
            self.event(amount=3, unpreventable=True),
            [prevention],
            selections=["prevent"],
        )

        self.assertEqual(3, changed.payload["amount"])
        self.assertEqual(0, changed.payload["prevented"])
        self.assertEqual(
            "applied", changed.payload["prevented_by"]
        )
        self.assertEqual(("prevent",), changed.applied_effects)
        self.assertIsNone(replacement_choice(changed, [prevention]))

    def test_negative_prevention_amount_fails_closed(self):
        with self.assertRaisesRegex(
            ReplacementEffectError,
            "at least 0",
        ):
            self.prevention(amount=-1)

    def test_negative_damage_amount_fails_closed_in_prevention(self):
        prevention = self.prevention(amount=1)
        choice = replacement_choice(
            self.event(amount=-1), [prevention]
        )

        with self.assertRaisesRegex(
            ReplacementEffectError,
            "cannot be negative",
        ):
            apply_replacement(choice, [prevention], "prevent")


class EffectRuleTests(unittest.TestCase):
    @staticmethod
    def source_shield():
        return ReplacementEffect(
            effect_id="red-source-shield",
            source_id="shield",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            conditions={"source_color": "red"},
            operations=({"op": "prevent", "amount": 3},),
        )

    @staticmethod
    def damage_event(event_id, *, source_color):
        return ReplaceableEvent(
            event_id=event_id,
            kind="damage",
            affected_player="A",
            payload={
                "amount": 3,
                "source_color": source_color,
            },
        )

    def test_contract_traces_every_cr_609_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "effects.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "609",
                "609.1",
                "609.2",
                "609.3",
                "609.4",
                "609.4a",
                "609.4b",
                "609.5",
                "609.6",
                "609.7",
                "609.7a",
                "609.7b",
                "609.7c",
            },
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("609")
            },
        )

    def test_source_property_mismatch_does_not_consume_shield(self):
        shield = self.source_shield()
        mismatching = self.damage_event(
            "damage:blue",
            source_color="blue",
        )
        matching = self.damage_event(
            "damage:red",
            source_color="red",
        )

        self.assertIsNone(replacement_choice(mismatching, [shield]))
        choice = replacement_choice(matching, [shield])
        self.assertEqual(("red-source-shield",), choice.options)

        changed = apply_replacement(
            choice,
            [shield],
            "red-source-shield",
        )
        self.assertEqual(0, changed.payload["amount"])
        self.assertEqual(
            ("red-source-shield",),
            changed.applied_effects,
        )

    def test_any_of_source_property_matches_one_member_and_rejects_others(self):
        event = ReplaceableEvent(
            event_id="damage:black",
            kind="damage",
            affected_player="A",
            payload={"amount": 3, "source_colors": ["B"]},
        )
        effect = ReplacementEffect(
            effect_id="black-or-red-source-shield",
            source_id="shield",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "source_colors": {
                    "contains_any": ["B", "R"],
                }
            },
            operations=({"op": "prevent", "amount": 1},),
        )

        self.assertEqual(
            ("black-or-red-source-shield",),
            replacement_choice(event, [effect]).options,
        )
        self.assertIsNone(
            replacement_choice(
                ReplaceableEvent(
                    event_id="damage:blue",
                    kind="damage",
                    affected_player="A",
                    payload={"amount": 3, "source_colors": ["U"]},
                ),
                [effect],
            )
        )

    def test_unknown_source_predicate_fails_closed(self):
        unsupported = ReplacementEffect(
            effect_id="unsupported-source-test",
            source_id="shield",
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "source_color": {
                    "starts_with": "r",
                }
            },
            operations=({"op": "prevent", "amount": 1},),
        )

        with self.assertRaisesRegex(
            ReplacementEffectError,
            "Unsupported replacement condition predicate",
        ):
            replacement_choice(
                self.damage_event(
                    "damage:red",
                    source_color="red",
                ),
                [unsupported],
            )


if __name__ == "__main__":
    unittest.main()
