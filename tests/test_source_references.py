from __future__ import annotations

from dataclasses import FrozenInstanceError
import re
import unittest
from unittest.mock import patch

from quorune.abilities import parse_activated_abilities
from quorune.carddb import CardRecord
from quorune.compiler.counter_placement_templates import (
    CounterPlacementSubject,
    fixed_counter_placement_effect_template,
)
from quorune.compiler.damage_templates import fixed_damage_effect_template
from quorune.compiler.prevention_templates import (
    fixed_prevention_effect_template,
    prevention_trigger_effect_template,
)
from quorune.declaration_costs import normalized_oracle_line
from quorune.declaration_restrictions import parse_declaration_restriction_line
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_references import (
    SOURCE_REFERENCE_SCHEMA_VERSION,
    SourceReferenceError,
    SourceReferenceSpec,
    source_self_permanent_type,
)


def card_record(
    *,
    name: str,
    oracle_text: str,
    type_line: str = "Legendary Creature — Human",
) -> CardRecord:
    return CardRecord(
        oracle_id="source-reference-fixture",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="1" if "Creature" in type_line else None,
        toughness="1" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class SourceReferenceModelTests(unittest.TestCase):
    def test_full_and_complete_precomma_names_form_closed_vocabulary(self):
        source = SourceReferenceSpec("  Ant-Man,   Scott Lang ")

        self.assertEqual(SOURCE_REFERENCE_SCHEMA_VERSION, source.schema_version)
        self.assertEqual("Ant-Man, Scott Lang", source.full_name)
        self.assertEqual("Ant-Man", source.shortened_name)
        self.assertEqual(
            ("Ant-Man, Scott Lang", "Ant-Man"),
            source.display_names,
        )
        self.assertTrue(source.matches("ant-man, scott lang"))
        self.assertTrue(source.matches("ANT-MAN"))
        self.assertFalse(source.matches("Ant"))
        self.assertFalse(source.matches("Scott Lang"))
        self.assertFalse(source.matches(None))

        equivalent = SourceReferenceSpec("Ant-Man, Scott Lang")
        self.assertEqual(source, equivalent)
        self.assertEqual(hash(source), hash(equivalent))
        with self.assertRaises(FrozenInstanceError):
            source.full_name = "Mutated"  # type: ignore[misc]

    def test_title_delimiter_preserves_complete_name_without_first_word_guess(self):
        source = SourceReferenceSpec("Syr Carah the Bold")

        self.assertEqual(
            ("Syr Carah the Bold", "Syr Carah"),
            source.display_names,
        )
        self.assertTrue(source.matches("Syr Carah the Bold"))
        self.assertTrue(source.matches("Syr Carah"))
        self.assertFalse(source.matches("Syr"))

    def test_bounded_two_word_and_of_title_forms_preserve_existing_oracle_names(self):
        zurgo = SourceReferenceSpec("Zurgo Bellstriker")
        daxos = SourceReferenceSpec("Daxos of Meletis")

        self.assertEqual("Zurgo", zurgo.shortened_name)
        self.assertTrue(zurgo.matches("Zurgo"))
        self.assertEqual("Daxos", daxos.shortened_name)
        self.assertTrue(daxos.matches("Daxos"))

    def test_matching_normalizes_supported_punctuation_only(self):
        source = SourceReferenceSpec("Urza’s Saga")

        self.assertTrue(source.matches("Urza's Saga"))
        self.assertFalse(source.matches("Urzas Saga"))

    def test_regex_pattern_is_escaped_and_closed(self):
        source = SourceReferenceSpec("Karn (Legacy), Silver Golem")

        self.assertIsNotNone(
            re.fullmatch(source.regex_pattern, "Karn (Legacy)", re.IGNORECASE)
        )
        self.assertIsNotNone(
            re.fullmatch(
                source.regex_pattern,
                "Karn (Legacy), Silver Golem",
                re.IGNORECASE,
            )
        )
        self.assertIsNone(
            re.fullmatch(source.regex_pattern, "Karn Legacy", re.IGNORECASE)
        )

    def test_malformed_names_fail_closed(self):
        for value in (None, True, 3, "", "   ", ", Suffix", "Name, "):
            with self.subTest(value=value):
                with self.assertRaises(SourceReferenceError):
                    SourceReferenceSpec(value)  # type: ignore[arg-type]

    def test_source_self_descriptors_are_identity_not_runtime_type_predicates(self):
        expected = {
            "this artifact": "artifact",
            "this Aura": "enchantment",
            "this battle": "battle",
            "this creature": "creature",
            "this enchantment": "enchantment",
            "this Equipment": "artifact",
            "this land": "land",
            "this permanent": "permanent",
            "this planeswalker": "planeswalker",
            "this Saga": "enchantment",
            "this Spacecraft": "artifact",
            "this Vehicle": "artifact",
        }
        for text, card_type in expected.items():
            with self.subTest(text=text):
                self.assertEqual(card_type, source_self_permanent_type(text))
        for text in (
            None,
            "this card",
            "this token",
            "this Vehicle you control",
            "target Vehicle",
        ):
            with self.subTest(text=text):
                self.assertIsNone(source_self_permanent_type(text))


class SourceReferenceCompilerTests(unittest.TestCase):
    def test_source_event_prefilter_preserves_named_matching_and_skips_unrelated_text(self):
        from quorune.compiler.source_self_effect_templates import normalized_source_event_line

        with patch("quorune.compiler.source_self_effect_templates.SourceReferenceSpec",
                   side_effect=AssertionError("unneeded source-name parsing")):
            for line in ("Flying", "Draw a card.", "When this creature enters, draw a card.",
                         "Whenever another creature dies, you gain 1 life."):
                self.assertIsNone(normalized_source_event_line(line, source_name="Witness, Source"))
        for name in ("Witness, Source", "Regex (Source)+", "Name attacks, Then"):
            text=f"Whenever {name} attacks, draw a card."
            self.assertEqual("Whenever this creature attacks, draw a card.",
                             normalized_source_event_line(text, source_name=name))
        self.assertIsNone(normalized_source_event_line(
            "Whenever Witness and another creature attacks, draw a card.", source_name="Witness, Source"))

    def test_trigger_prefilters_preserve_original_names_events_and_captured_bodies(self):
        from quorune.oracle_ir import _source_self_zone_trigger_match
        from quorune.compiler.fixed_counter_trigger_nodes import _zone_change_trigger_binding
        from quorune.compiler.fixed_public_event_trigger_bindings import _named_source_graveyard_spec

        for name in ("Witness, Source", "Regex (Source)+", "Name or another Spirit", "Line\nName", "İmage"):
            with self.subTest(name=name):
                line=f"When {name} is put into a graveyard from the battlefield, draw a card."
                self.assertIsNotNone(_named_source_graveyard_spec(line,card_name=name))
                union=f"Whenever {name} or another creature dies, draw a card."
                self.assertIsNotNone(_zone_change_trigger_binding(union,card_name=name))
                if "\n" not in name:
                    for event in ("enters", "dies", "leaves the battlefield"):
                        match=_source_self_zone_trigger_match(f"When {name} {event}, draw a card.",card_name=name)
                        self.assertIsNotNone(match)
                        self.assertEqual(event,match.group("event"))
                        self.assertEqual("draw a card.",match.group("body"))
        for line in ("Draw a card.","When Witness dies, draw a card.","When Witness is put into a graveyard from anywhere, draw a card."):
            self.assertIsNone(_named_source_graveyard_spec(line,card_name="Witness"))
        for line in ("When another creature enters, draw a card.","When Witness and another creature dies, draw a card."):
            self.assertIsNone(_source_self_zone_trigger_match(line,card_name="Witness"))

    def rulings(self, record):
        return ()

    def test_named_source_event_and_result_keep_source_identity_and_original_spans(self):
        registry = load_default_capability_registry()
        cases = (
            ("War Machine, James Rhodes", "Whenever War Machine attacks, tap up to one target creature.", "creature.attacks", "$source.ref"),
            ("Jareth, Leonine Titan", "Whenever Jareth blocks, draw a card.", "creature.blocks", "$source.ref"),
            ("Gregor, Shrewd Magistrate", "Whenever Gregor deals combat damage to a player, draw a card.", "damage.dealt", "$source.ref"),
            ("Zegana, Utopian Speaker", "When Zegana enters, if you control another creature with a +1/+1 counter on it, draw a card.", "permanent.enter.self", None),
        )
        for name, text, event, identity in cases:
            with self.subTest(name=name):
                ir = compile_oracle_card(card_record(name=name, oracle_text=text), capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(event, node.event)
                if identity is not None:
                    self.assertIn(identity, str(node.event_condition))
                self.assertEqual(text, node.text)
                self.assertEqual(text, ir.faces[0].oracle_text[node.span.start:node.span.end])
        for body in ("Ant-Man gets +2/+0", "Ant-Man gains flying", "Ant-Man gets +1/+1 and gains lifelink"):
            text = "{1}: " + body + " until end of turn."
            ir = compile_oracle_card(card_record(name="Ant-Man, Scott Lang", oracle_text=text), capability_registry=registry, capability_profile="commander_review")
            self.assertEqual("exact", ir.status, ir.material_residuals)
            effect = ir.faces[0].nodes[0].effects[0]
            self.assertEqual("apply_source_characteristics_until_end_of_turn", effect["op"])
            self.assertEqual("$source.zone_object", effect["card"])

    def test_named_self_closure_preserves_unknown_events_names_and_siblings(self):
        registry = load_default_capability_registry()
        for text in (
            "Whenever Scott Lang attacks, draw a card.",
            "Whenever Ant attacks, draw a card.",
            "Whenever Ant-Man attacks alone, draw a card.",
            "Whenever Ant-Man or a remembered creature attacks, draw a card.",
            "{1}: Ant-Man gets +X/+X until end of turn.",
            "{1}: Ant-Man gains banding until end of turn.",
            "{1}: Ant-Man gets +1/+1 until your next turn.",
            "{1}: Target creature named Ant-Man gets +1/+1 until end of turn.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(card_record(name="Ant-Man, Scott Lang", oracle_text=text), capability_registry=registry, capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
        text = "Whenever Ant-Man attacks, draw a card.\nWhenever the moon remembers a creature, draw a card."
        ir = compile_oracle_card(card_record(name="Ant-Man, Scott Lang", oracle_text=text), capability_registry=registry, capability_profile="commander_review")
        self.assertTrue(ir.faces[0].nodes[0].exact)
        self.assertFalse(ir.faces[0].nodes[1].exact)
        self.assertNotEqual("exact", ir.status)

    def test_named_source_reference_normalization_mutant_is_killed(self):
        from quorune.compiler import fixed_counter_trigger_nodes as owner
        original = owner.fixed_counter_trigger_binding
        def deny_normalized(text, *, card_name=None, _source_normalized=False):
            return None if _source_normalized else original(text, card_name=card_name)
        with patch.object(owner, "fixed_counter_trigger_binding", deny_normalized):
            with self.assertRaises(AssertionError):
                binding = owner.fixed_counter_trigger_binding(
                    "Whenever War Machine attacks, draw a card.",
                    card_name="War Machine, James Rhodes",
                )
                self.assertIsNotNone(binding)

    def test_named_source_characteristic_physical_reference_mutant_is_killed(self):
        from quorune.compiler.fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
        original = FixedSourceCharacteristicsTemplate.effects.fget
        def physical_source(spec):
            effects = original(spec)
            return tuple({**effect, "card": "$source"} for effect in effects)
        with patch.object(FixedSourceCharacteristicsTemplate, "effects", property(physical_source)):
            with self.assertRaises(AssertionError):
                ir = compile_oracle_card(
                    card_record(name="Ant-Man, Scott Lang", oracle_text="{1}: Ant-Man gets +2/+0 until end of turn."),
                    capability_registry=load_default_capability_registry(), capability_profile="commander_review",
                )
                self.assertEqual("$source.zone_object", ir.faces[0].nodes[0].effects[0]["card"])

    def test_registered_source_reference_measurement_binds_complete_original_programs(self):
        from scripts.work_selection_cohort_measurements import _bound_effect_program_measurement
        original = card_record(name="Ant-Man, Scott Lang", oracle_text="{1}: Ant-Man gets +2/+0 until end of turn.")
        sibling = card_record(name=original.name, oracle_text=original.oracle_text + "\nWhenever the moon remembers a creature, draw a card.")
        for value, expected in ((original, 1), (sibling, 0)):
            with self.subTest(sibling=expected == 0):
                frontier = {"cards": [{"oracle_id": value.oracle_id, "oracle_ir_status": "unresolved", "abilities": [
                    {"face_id": "front", "ability_id": "front:n1", "status": "unresolved", "residuals": [{}]},
                    *([{"face_id": "front", "ability_id": "front:n2", "status": "unresolved", "residuals": [{}]}] if expected == 0 else []),
                ]}]}
                result = _bound_effect_program_measurement(
                    frontier=frontier, bundle_id="bundle:source-self-reference-closure",
                    probe_id="source-self-reference-closure-existing-owner-v1", cards_by_oracle_id={value.oracle_id: value},
                    coverage={"minimum_complete_card_gain": 1, "minimum_exact_ability_gain": 100, "minimum_material_residual_reduction": 100},
                    cohort_fingerprint="source-reference-fixture", database=self,
                )
                self.assertEqual(expected, result["complete_card_gain"])
                self.assertEqual(1, result["exact_ability_gain"])
                self.assertFalse(result["grants_gameplay_trust"])

    def test_counter_damage_and_prevention_share_shortened_source_identity(self):
        counter = fixed_counter_placement_effect_template(
            "Put a +1/+1 counter on Ant-Man.",
            card_name="Ant-Man, Scott Lang",
        )
        self.assertIsNotNone(counter)
        assert counter is not None
        self.assertIs(CounterPlacementSubject.SOURCE, counter.subject)

        damage = fixed_damage_effect_template(
            "Kamahl deals 3 damage to any target.",
            card_name="Kamahl, Pit Fighter",
        )
        self.assertIsNotNone(damage)
        assert damage is not None
        self.assertEqual("named", damage.source_kind)

        trigger = prevention_trigger_effect_template(
            "Whenever damage that would be dealt to you is prevented, "
            "put that many +1/+1 counters on Ant-Man.",
            card_name="Ant-Man, Scott Lang",
        )
        self.assertIsNotNone(trigger)

        aftermath = fixed_prevention_effect_template(
            "The next time a source of your choice would deal damage to you "
            "this turn, prevent that damage. When damage is prevented this "
            "way, Ant-Man deals that much damage to that source's controller "
            "and you draw that many cards.",
            card_name="Ant-Man, Scott Lang",
        )
        self.assertIsNotNone(aftermath)

    def test_unrelated_or_partial_names_remain_residual(self):
        self.assertIsNone(
            fixed_counter_placement_effect_template(
                "Put a +1/+1 counter on Ant.",
                card_name="Ant-Man, Scott Lang",
            )
        )
        self.assertIsNone(
            fixed_damage_effect_template(
                "Pit Fighter deals 3 damage to any target.",
                card_name="Kamahl, Pit Fighter",
            )
        )
        self.assertIsNone(
            prevention_trigger_effect_template(
                "Whenever damage that would be dealt to you is prevented, "
                "put that many +1/+1 counters on Scott Lang.",
                card_name="Ant-Man, Scott Lang",
            )
        )

    def test_declaration_normalization_accepts_only_complete_precomma_name(self):
        exact = parse_declaration_restriction_line(
            "Syr Carah can't block.",
            card_name="Syr Carah, the Bold",
        )
        guessed = parse_declaration_restriction_line(
            "Syr can't block.",
            card_name="Syr Carah, the Bold",
        )

        self.assertTrue(exact.exact)
        self.assertFalse(guessed.exact)
        self.assertEqual(
            "this creature can't block.",
            normalized_oracle_line(
                "Syr Carah can't block.",
                card_name="Syr Carah, the Bold",
            ),
        )
        self.assertEqual(
            "syr can't block.",
            normalized_oracle_line(
                "Syr can't block.",
                card_name="Syr Carah, the Bold",
            ),
        )

    def test_activated_source_cost_uses_shortened_name_without_guessing(self):
        exact = parse_activated_abilities(
            card_name="Ant-Man, Scott Lang",
            oracle_text="{1}, Sacrifice Ant-Man: Draw a card.",
        )[0]
        guessed = parse_activated_abilities(
            card_name="Ant-Man, Scott Lang",
            oracle_text="{1}, Sacrifice Ant: Draw a card.",
        )[0]

        self.assertTrue(exact.sacrifice_source)
        self.assertTrue(exact.compiled_cost)
        self.assertFalse(guessed.sacrifice_source)
        self.assertFalse(guessed.compiled_cost)
        self.assertEqual(("Sacrifice Ant",), guessed.uncompiled_costs)

    def test_oracle_ir_uses_same_reference_for_trigger_body_and_entry(self):
        trigger_text = (
            "When Ant-Man enters, put a +1/+1 counter on Ant-Man."
        )
        trigger_ir = compile_oracle_card(
            card_record(
                name="Ant-Man, Scott Lang",
                oracle_text=trigger_text,
            )
        )
        trigger_node = next(
            node
            for node in trigger_ir.faces[0].nodes
            if node.template_id
            and node.template_id.startswith("place-fixed-counter-source-")
        )
        self.assertEqual(trigger_text, trigger_node.text)
        self.assertEqual(
            trigger_text,
            trigger_text[trigger_node.span.start : trigger_node.span.end],
        )

        entry_text = "Vault-13 enters tapped."
        entry_ir = compile_oracle_card(
            card_record(
                name="Vault-13, Dwellers' Home",
                oracle_text=entry_text,
                type_line="Legendary Land",
            )
        )
        entry_node = next(
            node
            for node in entry_ir.faces[0].nodes
            if node.template_id == "zone-entry-state-self-tapped-v1"
        )
        self.assertEqual(entry_text, entry_node.text)
        self.assertEqual(
            entry_text,
            entry_text[entry_node.span.start : entry_node.span.end],
        )

        unsupported = compile_oracle_card(
            card_record(
                name="Vault-13, Dwellers' Home",
                oracle_text="Vault enters tapped.",
                type_line="Legendary Land",
            )
        )
        self.assertFalse(
            any(
                node.template_id == "zone-entry-state-self-tapped-v1"
                for node in unsupported.faces[0].nodes
            )
        )


if __name__ == "__main__":
    unittest.main()
