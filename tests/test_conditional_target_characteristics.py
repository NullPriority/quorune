from __future__ import annotations

"""CR 608.2b-c/h and 611.2c: same target, current trait, then a locked grant."""

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch, PropertyMock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.compiler.temporary_target_interactions import linked_creature_subtype_stats_template
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_target_interactions import TargetConditionSpec
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as attachment_controls
import test_bound_effect_programs as witnesses


class ConditionalTargetCharacteristicCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, kind="Instant", registry=None):
        return compile_oracle_card(replace(witnesses.record(text, kind), name="Conditional Trait Witness"),
            capability_registry=registry or self.registry, capability_profile="commander_review")

    def test_current_target_conditions_bind_without_narrowing_initial_targets(self):
        for text, kind in (
            ("Target creature gets +2/+2 until end of turn. If it's a Vampire, it gains first strike until end of turn.", "Instant"),
            ("Target creature you control gets +2/+2 until end of turn. If that creature is an Ally, it also gains flying until end of turn.", "Instant"),
            ("Target creature gets +1/+0 and gains first strike until end of turn. If it's a Goblin or Orc, it also gains haste until end of turn.", "Instant"),
            ("Target creature gets +1/+0 until end of turn. If that creature is a token, it also gains deathtouch until end of turn.", "Instant"),
            ("When this Equipment enters, attach it to target creature you control. If that creature is an Assassin, it gains deathtouch until end of turn.", "Artifact — Equipment"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, kind)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(1, node.target_schema["count"])
                self.assertNotIn("token", node.target_schema)
                self.assertNotIn("subtypes_any", node.target_schema)
                self.assertEqual(text[node.span.start:node.span.end], node.text)
                self.assertEqual("$target.0", node.effects[-1]["card"])
                self.assertIn("target_condition", node.effects[-1])

    def test_a_creature_only_condition_proves_a_broad_permanent_target(self):
        for text, kind in (
            ("Target permanent gains hexproof and indestructible until end of turn. If it's an artifact creature, it gets +2/+2 until end of turn.", "Instant"),
            ("{T}: Untap target Griffin. If it's a creature, it gets +1/+1 until end of turn.", "Land"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, kind)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertNotIn("types_any", node.target_schema)
                self.assertNotIn("creature", node.target_schema)
                self.assertEqual("$target.0", node.effects[-1]["card"])

    def test_type_proof_respects_every_disjunction_and_counter_alternative(self):
        creature = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
        artifact = ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",))
        both = ObjectQuerySpec(zones=("battlefield",), types_all=("artifact", "creature"))
        either = ObjectQuerySpec(zones=("battlefield",), types_any=("artifact", "creature"))
        token = ObjectQuerySpec(zones=("battlefield",), token=True)
        self.assertTrue(TargetConditionSpec(queries_any=(creature,)).guarantees_creature)
        self.assertTrue(TargetConditionSpec(queries_any=(both,)).guarantees_creature)
        for condition in (
            TargetConditionSpec(queries_any=(either,)),
            TargetConditionSpec(queries_any=(creature, artifact)),
            TargetConditionSpec(queries_any=(token,)),
            TargetConditionSpec(queries_any=(creature,), has_any_counter=True),
        ):
            self.assertFalse(condition.guarantees_creature)

    def test_old_subtype_stat_instruction_and_codec_remain_identical(self):
        parsed = linked_creature_subtype_stats_template("If that creature is an Elf, it also gets +1/+1 until end of turn.")
        self.assertIsNotNone(parsed)
        condition = TargetConditionSpec(queries_any=(ObjectQuerySpec(zones=("battlefield",), subtypes_any=("elf",)),))
        self.assertEqual("linked-creature-subtype-stats-v1", parsed[0])
        self.assertEqual(({'op': 'modify_stats_until_end_of_turn', 'card': '$target.0', 'power': 1,
            'toughness': 1, 'target_condition': condition.to_dict()},), parsed[1])
        self.assertEqual(DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema(), parsed[2])
        self.assertEqual(condition.to_dict(), TargetConditionSpec.from_dict(condition.to_dict()).to_dict())

    def test_instead_paired_objects_and_player_targets_remain_separate(self):
        for tail in (
            "If it's a Human, instead it gets +3/+3 and gains indestructible until end of turn.",
            "If it's paired with a creature, that creature also gets +2/+2 until end of turn.",
            "If it's your turn, it gains first strike until end of turn.",
        ):
            self.assertIsNone(linked_creature_subtype_stats_template(tail))
        text = "Target player gains 2 life. If it's an artifact creature, it gets +2/+2 until end of turn."
        self.assertNotEqual("exact", self.compile(text).status)

    def test_conditional_owner_and_original_target_authority_cannot_be_withdrawn(self):
        from quorune.rules.capabilities import CapabilityRegistry
        text = "Target permanent gains hexproof and indestructible until end of turn. If it's an artifact creature, it gets +2/+2 until end of turn."
        self.assertEqual("exact", self.compile(text).status)
        for dependency in ("continuous.resolution.temporary_target_interactions",
                           "resolution.effect_program.bound_references", "target.revalidate_resolution"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            next(row for row in raw["capabilities"] if row["id"] == dependency).update(
                status="blocked", blockers=["conditional target owner withdrawn"])
            negative = CapabilityRegistry(raw)
            negative.mark_evidence_verified(self.registry.evidence_fingerprint)
            self.assertNotEqual("exact", self.compile(text, registry=negative).status)

    def test_linked_lowering_and_broad_target_type_proof_omission_are_detected(self):
        text = "Target permanent gains hexproof and indestructible until end of turn. If it's an artifact creature, it gets +2/+2 until end of turn."
        self.assertEqual("exact", self.compile(text).status)
        with patch("quorune.compiler.temporary_target_interactions.linked_creature_subtype_stats_template", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)
        with patch.object(TargetConditionSpec, "guarantees_creature", new_callable=PropertyMock, return_value=False):
            self.assertNotEqual("exact", self.compile(text).status)


class ConditionalTargetCharacteristicRegistrationTests(unittest.TestCase):
    def test_full_original_sources_close_and_different_referents_remain_material(self):
        admitted = ("Hidden Blade", "Built to Last", "Vampire's Zeal", "Unchecked Growth", "Yip Yip!",
            "Built to Smash", "Rush the Room", "Blacksmith's Skill", "Ingenious Leonin", "Griffin Canyon",
            "Wedding Invitation", "Hazardroot Herbalist")
        excluded = ("Flare of Faith", "Joint Assault", "Regent's Authority", "Ray of Enfeeblement")
        registry = load_default_capability_registry()
        semantics = SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "complete-conditional-sources.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/conditional-target-characteristic-cards.json"], path)
            with CardDatabase(path) as db:
                for name in (*admitted, *excluded):
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level="provisional",
                            capability_registry=registry, capability_profile="commander_review",
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=registry, capability_profile="commander_review")
                        bound = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                        ready = bound["strict_capability_ready"] and bound["trust_basis"] == "capability_closed"
                        self.assertEqual(name in admitted, ready, bound["blockers"])


class ConditionalTargetCharacteristicRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = attachment_controls.AttachmentFollowupRuntimeTests.session
    add = attachment_controls.AttachmentFollowupRuntimeTests.add
    prepare = attachment_controls.AttachmentFollowupRuntimeTests.prepare
    closed = attachment_controls.AttachmentFollowupRuntimeTests.closed
    actions = attachment_controls.AttachmentFollowupRuntimeTests.actions
    cast_offered = attachment_controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint = attachment_controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = attachment_controls.AttachmentFollowupRuntimeTests.resolve
    replay = attachment_controls.AttachmentFollowupRuntimeTests.replay
    advance = attachment_controls.AttachmentFollowupRuntimeTests.advance
    until = attachment_controls.AttachmentFollowupRuntimeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "conditional-traits.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/conditional-target-characteristic-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Conditional target witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def setup_spell(self, seed, name, recipient, *, controls=(), responses=()):
        session = self.session(seed)
        spell = self.add(session.engine, name, zone="hand")
        target = self.add(session.engine, recipient, ref="conditional-recipient")
        others = [self.add(session.engine, control, ref=f"conditional-control-{i}")
                  for i, control in enumerate(controls)]
        replies = [self.add(session.engine, response, zone="hand", ref=f"conditional-response-{i}")
                   for i, response in enumerate(responses)]
        self.prepare(session, spell, {"C": 15, "W": 8, "U": 8, "G": 8, "R": 8, "B": 8})
        self.checkpoint(session)
        return session, spell, target, others, replies

    def test_original_conditional_bonuses_keep_nonmatching_targets_available(self):
        cases = (
            ("Vampire's Zeal", "Generic Conditional Human", 4, 6, set(), {"first strike"}),
            ("Vampire's Zeal", "Universal Automaton", 3, 3, {"first strike"}, set()),
            ("Built to Last", "Generic Conditional Human", 4, 6, set(), {"indestructible"}),
            ("Built to Last", "Generic Conditional Construct", 4, 6, {"indestructible"}, set()),
            ("Rush the Room", "Generic Conditional Human", 3, 4, {"first strike"}, {"haste"}),
            ("Rush the Room", "Universal Automaton", 2, 1, {"first strike", "haste"}, set()),
        )
        for index, (name, recipient, power, toughness, present, absent) in enumerate(cases):
            with self.subTest(name=name, recipient=recipient):
                session, spell, target, _, _ = self.setup_spell(293100 + index, name, recipient)
                action = next(row for row in self.actions(session) if row["id"] == "cast:" + spell.ref)
                self.assertIn(target.ref, str(action["target_schema"]))
                self.cast_offered(session, spell, targets=(target.ref,))
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[target.object_id]
                self.assertEqual(power, session.engine._numeric_stat(current.object_id, "power"))
                self.assertEqual(toughness, session.engine._numeric_stat(current.object_id, "toughness"))
                keywords = session.engine._combat_keywords(current)
                self.assertTrue(present <= keywords)
                self.assertFalse(absent.intersection(keywords))
                self.replay(session, load=True)

    def test_blacksmith_skill_protects_noncreatures_without_a_creature_stat_bonus(self):
        for index, recipient in enumerate(("Generic Conditional Artifact", "Generic Conditional Construct", "Generic Conditional Human")):
            with self.subTest(recipient=recipient):
                session, spell, target, _, _ = self.setup_spell(293110 + index, "Blacksmith's Skill", recipient)
                self.cast_offered(session, spell, targets=(target.ref,))
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[target.object_id]
                self.assertTrue({"hexproof", "indestructible"} <= session.engine._combat_keywords(current))
                if recipient == "Generic Conditional Construct":
                    self.assertEqual(4, session.engine._numeric_stat(current.object_id, "power"))
                    self.assertEqual(6, session.engine._numeric_stat(current.object_id, "toughness"))
                elif recipient == "Generic Conditional Human":
                    self.assertEqual(2, session.engine._numeric_stat(current.object_id, "power"))
                self.replay(session, load=True)

    def change_type(self, session, source, target, chosen):
        self.closed(session, source)
        action = next(row for row in self.actions(session) if row["id"].startswith("activate:" + source.ref + ":"))
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        before = authoritative_state_hash(session.state)
        for principal, value in (("pilot:B", chosen), ("pilot:A", "wall")):
            wrong = session.act(principal, {"action_id": "choose", "creature_type": value})
            self.assertFalse(wrong.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "creature_type": chosen})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        return session

    def test_trait_is_read_at_resolution_then_the_result_stays_locked_until_end_of_turn(self):
        session, spell, target, controls, _ = self.setup_spell(293120, "Vampire's Zeal", "Generic Conditional Human",
            controls=("Imagecrafter", "Imagecrafter"))
        self.cast_offered(session, spell, targets=(target.ref,))
        session = self.change_type(session, controls[0], target, "vampire")
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual(4, session.engine._numeric_stat(target.object_id, "power"))
        session = self.change_type(session, controls[1], target, "human")
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.advance(session, lambda current: current.state.active_player != "A")
        self.assertNotIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual(2, session.engine._numeric_stat(target.object_id, "power"))
        self.replay(session, load=True)

    def test_losing_the_trait_before_resolution_skips_the_bonus_and_later_change_does_not_enable_it(self):
        session, spell, target, controls, _ = self.setup_spell(293121, "Vampire's Zeal", "Universal Automaton",
            controls=("Imagecrafter", "Imagecrafter"))
        self.cast_offered(session, spell, targets=(target.ref,))
        session = self.change_type(session, controls[0], target, "human")
        self.assertEqual(3, session.engine._numeric_stat(target.object_id, "power"))
        self.assertNotIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        session = self.change_type(session, controls[1], target, "vampire")
        self.assertNotIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.replay(session, load=True)

    def test_griffin_canyon_untaps_a_noncreature_kindred_griffin_without_giving_it_stats(self):
        for offset, name in enumerate(("Generic Conditional Kindred Griffin", "Generic Conditional Griffin")):
            with self.subTest(name=name):
                session = self.session(293130 + offset)
                source = self.add(session.engine, "Griffin Canyon")
                target = self.add(session.engine, name, ref="selected-griffin")
                target.tapped = True
                self.prepare(session, source, {})
                self.checkpoint(session)
                action = next(row for row in self.actions(session) if row["id"] == "activate:" + source.ref + ":ab2")
                self.assertIn(target.ref, str(action["target_schema"]))
                result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[target.object_id]
                self.assertFalse(current.tapped)
                if name == "Generic Conditional Griffin":
                    self.assertEqual(3, session.engine._numeric_stat(target.object_id, "power"))
                    self.assertEqual(5, session.engine._numeric_stat(target.object_id, "toughness"))
                else:
                    self.assertIsNone(session.engine._effective_card_data(current).get("power"))
                    self.assertEqual([], session.state.continuous_effects)
                self.replay(session, load=True)

    def test_hidden_blade_conditional_result_does_not_require_successful_attachment(self):
        session, source, target, _, responses = self.setup_spell(293140, "Hidden Blade", "Universal Automaton",
            responses=("Disenchant",))
        self.cast_offered(session, source)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(result.ok, result.summary)
        self.cast_offered(session, responses[0], targets=(source.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertIn("deathtouch", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.replay(session, load=True)

    def test_actual_attacking_token_and_cat_conditions_compose_through_existing_owners(self):
        session = self.session(293150)
        herbalist = self.add(session.engine, "Hazardroot Herbalist")
        leonin = self.add(session.engine, "Ingenious Leonin")
        attacker = self.add(session.engine, "Universal Automaton", ref="conditional-attacker")
        alarm = self.add(session.engine, "Raise the Alarm", zone="hand")
        smash = self.add(session.engine, "Built to Smash", zone="hand")
        for index in range(4):
            self.add(session.engine, "Generic Bound Plains", ref=f"conditional-plains-{index}")
            self.add(session.engine, "Mountain", ref=f"conditional-mountain-{index}")
        for card in (herbalist, leonin, alarm, smash):
            self.closed(session, card)
        self.prepare(session, alarm, {"C": 15, "W": 10, "R": 5})
        self.checkpoint(session)
        self.cast_offered(session, alarm)
        self.assertIsNone(self.resolve(session))
        token = next(card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.advance(session, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:A", {"action_id": "attack", "attacks": {attacker.ref: "B"}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "targets": [token.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.engine._numeric_stat(token.object_id, "power"))
        self.assertIn("deathtouch", session.engine._combat_keywords(session.state.cards[token.object_id]))
        action = next(row for row in self.actions(session) if row["id"].startswith("activate:" + leonin.ref + ":"))
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [attacker.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[attacker.object_id].counters.get("+1/+1", 0))
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[attacker.object_id]))
        self.cast_offered(session, smash, targets=(attacker.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, session.engine._numeric_stat(attacker.object_id, "power"))
        self.assertIn("trample", session.engine._combat_keywords(session.state.cards[attacker.object_id]))
        self.replay(session, load=True)

    def test_runtime_condition_omission_mutant_is_killed_by_a_legal_false_target(self):
        session, spell, target, _, _ = self.setup_spell(293160, "Vampire's Zeal", "Generic Conditional Human")
        with patch.object(TargetConditionSpec, "matches", return_value=True):
            self.cast_offered(session, spell, targets=(target.ref,))
            self.assertIsNone(self.resolve(session))
            with self.assertRaises(AssertionError):
                self.assertNotIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))


if __name__ == "__main__":
    unittest.main()
