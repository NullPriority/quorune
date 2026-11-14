from __future__ import annotations

"""CR 113.7a/608.2b-c/609.3/701.3: a bound target outlives attachment success."""

import unittest
import json
from dataclasses import replace
from pathlib import Path
import tempfile
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
import test_temporary_keyword_removal as turn_controls


class AttachmentFollowupCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, registry=None):
        return compile_oracle_card(
            replace(witnesses.record(text, "Artifact — Equipment"), name="Attachment Followup Witness"),
            capability_registry=registry or self.registry, capability_profile="commander_review",
        )

    def test_attachment_followups_reuse_one_selected_creature(self):
        for tail in (
            "That creature gains first strike until end of turn.",
            "That creature gains shroud until end of turn.",
            "That creature gains hexproof and indestructible until end of turn.",
            "Untap that creature.",
        ):
            text = "When this Equipment enters, attach it to target creature you control. " + tail
            with self.subTest(tail=tail):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(text[node.span.start:node.span.end], node.text)
                self.assertEqual(1, node.target_schema["count"])
                self.assertEqual("you", node.target_schema["controller"])
                self.assertEqual("attach", node.effects[0]["op"])
                self.assertEqual("$source.zone_object", node.effects[0]["source"])
                self.assertEqual("$target.0", node.effects[0]["target"])
                self.assertEqual("$target.0", node.effects[-1]["card"])

    def test_legacy_type_guarantee_requires_true_creature_predicate(self):
        from quorune.compiler.bound_effect_programs import _target_type_is_guaranteed
        base = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}
        self.assertTrue(_target_type_is_guaranteed({**base, "creature": True}, "creature"))
        for value in (False, None, 1, "true"):
            self.assertFalse(_target_type_is_guaranteed({**base, "creature": value}, "creature"))
        self.assertFalse(_target_type_is_guaranteed({**base, "types_any": ["artifact", "creature"]}, "creature"))
        self.assertTrue(_target_type_is_guaranteed({**base, "types_all": ["artifact", "creature"]}, "creature"))

    def test_unconsumed_and_reflexive_siblings_remain_material(self):
        for tail in (
            "When you do, equipped creature fights up to one target creature an opponent controls.",
            "That creature gains first strike until end of turn. You win the game.",
        ):
            text = "When this Equipment enters, attach it to target creature you control. " + tail
            self.assertNotEqual("exact", self.compile(text).status)

    def test_attachment_prefix_and_exact_legacy_schema_independently_gate_closure(self):
        from quorune.rules.bound_effect_program_shapes import bound_effect_program_node_capabilities
        text = ("When this Equipment enters, attach it to target creature you control. "
                "That creature gains first strike until end of turn.")
        ir = self.compile(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        for schema in (
            {**node.target_schema, "creature": False},
            {**node.target_schema, "creature": 1},
            {**node.target_schema, "controller": "any"},
            {**node.target_schema, "count": True},
            {**node.target_schema, "unknown": True},
        ):
            self.assertFalse(bound_effect_program_node_capabilities(
                effects=node.effects, target_schema=schema, mechanic_ids=node.mechanics))
        for first in (
            {**node.effects[0], "source": "$source"},
            {**node.effects[0], "attachment_kind": "aura"},
            {**node.effects[0], "target": "$target.1"},
            {"op": "unknown", "card": "$target.0"},
        ):
            self.assertFalse(bound_effect_program_node_capabilities(
                effects=(first, *node.effects[1:]), target_schema=node.target_schema, mechanic_ids=node.mechanics))

    def test_withdrawing_legacy_type_proof_blocks_the_new_composition(self):
        from quorune.compiler import bound_effect_programs
        text = ("When this Equipment enters, attach it to target creature you control. "
                "That creature gains first strike until end of turn.")
        self.assertEqual("exact", self.compile(text).status)
        original = bound_effect_programs._target_type_is_guaranteed

        def omit_legacy(schema, kind):
            return False if schema.get("creature") is True else original(schema, kind)

        with patch.object(bound_effect_programs, "_target_type_is_guaranteed", side_effect=omit_legacy):
            self.assertNotEqual("exact", self.compile(text).status)

    def test_attachment_composition_requires_each_real_component_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        text = ("When this Equipment enters, attach it to target creature you control. "
                "That creature gains first strike until end of turn.")
        self.assertEqual("exact", self.compile(text).status)
        for dependency in ("attachment.action.fixed_source", "resolution.effect_program.bound_references",
                           "continuous.resolution.fixed_characteristics_until_end_of_turn", "target.revalidate_resolution"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            next(row for row in raw["capabilities"] if row["id"] == dependency).update(
                status="blocked", blockers=["attachment composition authority withdrawn"])
            negative = CapabilityRegistry(raw)
            negative.mark_evidence_verified(self.registry.evidence_fingerprint)
            self.assertNotEqual("exact", self.compile(text, negative).status, dependency)


class AttachmentFollowupRegistrationTests(unittest.TestCase):
    def test_complete_originals_admit_only_the_closed_direct_followups(self):
        admitted = (
            "Squire's Lightblade", "Barbed Bloodletter", "Quick-Draw Dagger", "Celestial Armor",
            "Stolen Stark Tech", "Super Suit", "Twin Blades", "Galadhrim Bow", "Silver Shroud Costume",
            "Bladed Battle-Fan", "Illvoi Light Jammer", "Coral Sword", "Hidden Footblade",
        )
        excluded = ("Hidden Blade", "Phantom Blade", "Novel Nunchaku", "Hunter's Bow", "Assassin Gauntlet")
        registry = load_default_capability_registry()
        semantics = SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "complete-attachment-sources.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/attachment-followup-cards.json"], path)
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
                        binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                        ready = binding["strict_capability_ready"] and binding["trust_basis"] == "capability_closed"
                        self.assertEqual(name in admitted, ready, binding["blockers"])
                        if name in excluded:
                            self.assertFalse(any(p.trust_level == "trusted" and p.event == "permanent.enter.self"
                                for p in semantics.programs_for_oracle(card.oracle_id)))


class AttachmentFollowupRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = witnesses.BoundEffectProgramRuntimeTests.replay
    advance = turn_controls.TemporaryKeywordRemovalRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "attachment-followup.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/attachment-followup-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attachment followup witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def closed(self, session, card):
        record = self.db.by_oracle_id(card.oracle_id)
        program = compile_best_available_card_program(self.db, record, semantic_registry=session.engine.semantics,
            capability_registry=self.registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        self.assertEqual("capability_closed", binding["trust_basis"])

    def prepare(self, session, source, mana):
        from quorune.semantic_runtime.activated_abilities import is_structural_activated_ability_catalog_program
        self.closed(session, source)
        programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
        executable = [program for program in programs if not is_structural_activated_ability_catalog_program(program)]
        self.assertTrue(executable)
        self.assertTrue(all(session.engine.semantic_program_is_current_trusted(program) for program in executable))
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool.update(mana)
        engine._grant_priority("A")
        engine.pump()

    def actions(self, session, seat="A"):
        return session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def cast_offered(self, session, card, *, targets=(), seat="A"):
        self.closed(session, card)
        offered = next(row for row in self.actions(session, seat) if row["id"] == "cast:" + card.ref)
        command = {"action_id": offered["id"], "pay": "auto"}
        if targets:
            command["targets"] = list(targets)
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def start(self, seed, name, *, responses=(), owned_elsewhere=False, tapped=False, second_recipient=False):
        session = self.session(seed)
        source = self.add(session.engine, name, zone="hand")
        target = self.add(session.engine, "Generic Bound Body", seat="B" if owned_elsewhere else "A", ref="selected-recipient")
        if owned_elsewhere:
            session.engine.change_control(target.object_id, "A")
        target.tapped = tapped
        if second_recipient:
            self.add(session.engine, "Generic Bound Body", ref="second-recipient")
        enemy = self.add(session.engine, "Generic Bound Body", seat="C", ref="opposing-recipient")
        secret = self.add(session.engine, "Generic Bound Growth", seat="B", zone="hand", ref="private-opponent-hand")
        response_cards = [self.add(session.engine, response, zone="hand", ref=f"response-{i}")
                          for i, response in enumerate(responses)]
        for card in (source, *response_cards):
            self.closed(session, card)
        self.prepare(session, source, {"C": 20, "W": 10, "U": 10, "B": 10, "R": 10, "G": 10})
        self.checkpoint(session)
        self.cast_offered(session, source)
        decision = self.resolve(session)
        self.assertEqual("semantic.target", decision.kind)
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertNotIn(secret.ref, str(session.packet("pilot:A", full=True)))
        return session, source, target, enemy, response_cards

    def select(self, session, target):
        result = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(result.ok, result.summary)

    def until(self, session, condition):
        for _ in range(48):
            if condition():
                return
            pending = session.state.pending_decision
            self.assertTrue(pending is not None and pending.kind == "priority", pending)
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Attachment response did not reach its bounded stopping point")

    def original_case(self, seed, name, expected_keywords, *, tapped=False):
        session, source, target, enemy, _ = self.start(seed, name, owned_elsewhere=True, tapped=tapped)
        mana_before = dict(session.state.players["A"].mana_pool)
        before = authoritative_state_hash(session.state)
        for principal, selected in (("pilot:B", target), ("pilot:A", enemy)):
            result = session.act(principal, {"action_id": "choose", "targets": [selected.ref]})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.select(session, target)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertEqual(target.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual("B", current.owner)
        self.assertEqual("A", current.controller)
        self.assertTrue(set(expected_keywords) <= session.engine._combat_keywords(current))
        if tapped:
            self.assertFalse(current.tapped)
        self.assertEqual(mana_before, session.state.players["A"].mana_pool)
        self.replay(session, load=True)

    def test_original_equipment_results_bind_to_one_controlled_creature_and_replay(self):
        for offset, (name, keywords, tapped) in enumerate((
            ("Quick-Draw Dagger", {"first strike"}, False),
            ("Celestial Armor", {"hexproof", "indestructible", "flying"}, False),
            ("Silver Shroud Costume", {"shroud"}, False),
            ("Twin Blades", {"double strike"}, False),
            ("Barbed Bloodletter", {"wither"}, False),
            ("Super Suit", set(), True),
        )):
            with self.subTest(name=name):
                self.original_case(292100 + offset, name, keywords, tapped=tapped)

    def test_departed_equipment_does_not_suppress_the_legal_creature_grant(self):
        session, source, target, _, responses = self.start(292110, "Quick-Draw Dagger", responses=("Disenchant",))
        self.select(session, target)
        self.cast_offered(session, responses[0], targets=(source.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual(2, session.engine._numeric_stat(target.object_id, "power"))
        self.replay(session, load=True)

    def test_bounced_and_recast_equipment_is_not_redirected_by_the_older_trigger(self):
        session, source, target, _, responses = self.start(292111, "Quick-Draw Dagger",
            responses=("Into the Roil",), second_recipient=True)
        second = next(card for card in session.state.cards.values() if card.ref == "second-recipient")
        old_identity = session.state.cards[source.object_id].logical_object_id
        self.select(session, target)
        self.cast_offered(session, responses[0], targets=(source.ref,))
        self.until(session, lambda: session.state.cards[source.object_id].zone == "hand")
        self.cast_offered(session, source)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        self.select(session, second)
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(old_identity, session.state.cards[source.object_id].logical_object_id)
        self.assertEqual(second.object_id, session.state.cards[source.object_id].attached_to)
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.assertEqual(2, session.engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(3, session.engine._numeric_stat(second.object_id, "power"))
        self.replay(session, load=True)

    def test_returned_target_is_a_new_incarnation_and_invalidates_both_instructions(self):
        session, source, target, _, responses = self.start(292113, "Quick-Draw Dagger", responses=("Cloudshift",))
        old_identity = session.state.cards[target.object_id].logical_object_id
        self.select(session, target)
        self.cast_offered(session, responses[0], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertNotEqual(old_identity, current.logical_object_id)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertNotIn("first strike", session.engine._combat_keywords(current))
        self.replay(session, load=True)

    def test_equipment_cast_remains_available_without_a_legal_entry_target(self):
        session = self.session(292114)
        source = self.add(session.engine, "Quick-Draw Dagger", zone="hand")
        self.closed(session, source)
        self.prepare(session, source, {"C": 3})
        self.checkpoint(session)
        self.cast_offered(session, source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("C", 0))
        self.replay(session, load=True)

    def test_attachment_omission_mutant_is_killed_by_original_actions(self):
        with patch("quorune.effect_runtime.zones_and_attachments._apply_attach", return_value=None):
            with self.assertRaises(AssertionError):
                self.original_case(292115, "Quick-Draw Dagger", {"first strike"})

    def test_actual_equip_moves_only_the_attachment_and_keeps_original_temporary_recipient(self):
        session, source, target, _, _ = self.start(292116, "Quick-Draw Dagger", second_recipient=True)
        second = next(card for card in session.state.cards.values() if card.ref == "second-recipient")
        self.select(session, target)
        self.assertIsNone(self.resolve(session))
        action = next(row for row in self.actions(session) if row["id"] == "activate:" + source.ref + ":ab4")
        mana_before = session.state.players["A"].mana_pool["C"]
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto", "targets": [second.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(mana_before - 1, session.state.players["A"].mana_pool["C"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(second.object_id, session.state.cards[source.object_id].attached_to)
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertNotIn("first strike", session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.assertEqual(2, session.engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(3, session.engine._numeric_stat(second.object_id, "power"))
        self.replay(session, load=True)

    def test_new_shroud_invalidates_the_only_target_before_attachment_and_followup(self):
        session, source, target, _, responses = self.start(292117, "Quick-Draw Dagger",
            responses=("Generic Bound Shroud Untap",))
        self.select(session, target)
        self.cast_offered(session, responses[0], targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertIn("shroud", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertNotIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.replay(session, load=True)

    def test_temporary_entry_grant_expires_while_equipment_stays_attached(self):
        session, source, target, _, _ = self.start(292118, "Quick-Draw Dagger")
        self.select(session, target)
        self.assertIsNone(self.resolve(session))
        self.assertIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.advance(session, lambda current: current.state.active_player != "A")
        self.assertEqual(target.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual(3, session.engine._numeric_stat(target.object_id, "power"))
        self.assertNotIn("first strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
