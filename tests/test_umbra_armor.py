from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from quorune.deck import DeckDefinition, DeckEntry
import test_bound_effect_programs as witnesses

from quorune.ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict, canonical_ability_fragments
from quorune.model import CardInstance
from quorune.semantic_runtime.ability_fragments import UmbraArmorFragmentHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.umbra_armor_model import UmbraArmorProtection, UmbraArmorSpec, UMBRA_ARMOR_HANDLER
from quorune.umbra_armor import current_umbra_armor_protections
from quorune.destruction_replacement_options import DestructionReplacementSubject, destruction_replacement_options
from quorune.replacement.ordering import advance_replacement_batch
from quorune.replacement.model import ReplacementEffectError


class UmbraArmorRepresentationTests(unittest.TestCase):
    def test_competing_protections_use_recipient_controller_and_shared_choice_authority(self):
        subject = DestructionReplacementSubject("body", "C", "body@0", "A", "A", shield_counters=1, regeneration_shields=1)
        first = UmbraArmorProtection("aura1", "U1", "aura1@0", "B", "body", "body@0", "A", 0)
        second = replace(first, aura_object_id="aura2", aura_ref="U2", aura_logical_object_id="aura2@0", aura_controller="C")
        options = destruction_replacement_options((subject,), (first, second), batch_id="choice", apnap_order="ABCD", cause="effect")
        progress = advance_replacement_batch(options.batch, options.effects)
        self.assertEqual("A", progress.pending.choice.chooser)
        self.assertEqual(4, len(progress.pending.choice.options))
        selected = next(effect.effect_id for effect in options.effects if effect.source_id == "aura2")
        resolved = advance_replacement_batch(options.batch, options.effects, selections=(selected,))
        self.assertIsNone(resolved.pending)
        event = resolved.batch.events[0]
        self.assertEqual("umbra_armor", event.payload["disposition"])
        self.assertTrue(event.payload["clear_damage"])
        self.assertEqual("aura2", event.payload["umbra_aura_object_id"])
        self.assertEqual("destroy", options.batch.events[0].payload["disposition"])
        with self.assertRaises(ReplacementEffectError):
            advance_replacement_batch(options.batch, options.effects, selections=("invented-choice",))

    def test_indestructible_and_forbidden_regeneration_do_not_consume_unrelated_protection(self):
        subject = DestructionReplacementSubject("body", "C", "body@0", "A", "A", indestructible=True, shield_counters=1, regeneration_shields=1)
        aura = UmbraArmorProtection("aura", "U", "aura@0", "B", "body", "body@0", "A", 0)
        options = destruction_replacement_options((subject,), (aura,), batch_id="indestructible", apnap_order="ABCD", cause="effect")
        resolved = advance_replacement_batch(options.batch, options.effects)
        self.assertIsNone(resolved.pending)
        self.assertEqual("indestructible", resolved.batch.events[0].payload["disposition"])
        self.assertFalse(resolved.batch.events[0].payload["clear_damage"])
        self.assertFalse(resolved.batch.journal)
        options = destruction_replacement_options((replace(subject, indestructible=False),), (aura,), batch_id="forbidden", apnap_order="ABCD", cause="effect", regeneration_prohibited=True)
        self.assertEqual(2, len(advance_replacement_batch(options.batch, options.effects).pending.choice.options))
        options = destruction_replacement_options((replace(subject, indestructible=False),), (aura,), batch_id="lethal", apnap_order="ABCD", cause="state_based_action")
        self.assertEqual(2, len(advance_replacement_batch(options.batch, options.effects).pending.choice.options))

    def test_destruction_choice_snapshot_rejects_invalid_resources_and_stale_recipient(self):
        subject = DestructionReplacementSubject("body", "C", "body@0", "A", "A")
        for mutation in ({"shield_counters": True}, {"regeneration_shields": -1}, {"indestructible": 1}):
            with self.assertRaises(ValueError):
                replace(subject, **mutation)
        aura = UmbraArmorProtection("aura", "U", "aura@0", "B", "body", "body@1", "A", 0)
        with self.assertRaises(ValueError):
            destruction_replacement_options((subject,), (aura,), batch_id="stale", apnap_order="ABCD", cause="effect")
        with self.assertRaises(ValueError):
            destruction_replacement_options((subject, subject), (), batch_id="duplicate", apnap_order="ABCD", cause="effect")
        with self.assertRaises(ValueError):
            destruction_replacement_options((subject,), (), batch_id="wrong", apnap_order="ABCD", cause="state_based_action", regeneration_prohibited=True)

    def test_current_source_fragment_projection_mutant_is_killed(self):
        with patch("quorune.umbra_armor.canonical_ability_fragments", return_value=()):
            with self.assertRaises(AssertionError):
                self.test_current_aura_relationship_keeps_source_and_recipient_ability_loss_distinct()

    def test_recognized_fragment_does_not_certify_unfinished_destruction_gameplay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "umbra.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/umbra-armor-cards.json"], path)
            with CardDatabase(path) as db:
                registry = load_default_capability_registry()
                record = db.lookup("Hyena Umbra")
                ir = compile_oracle_card(record, capability_registry=registry)
                node = next(n for n in ir.faces[0].nodes if n.template_id == "umbra-armor-source-fragment-v1")
                self.assertTrue(node.lowerable)
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)
                program = compile_best_available_card_program(db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                self.assertFalse(bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")["strict_capability_ready"])

    def test_source_rule_codec_preserves_multiplicity_and_rejects_unknown_fields(self):
        rule = UmbraArmorSpec()
        self.assertEqual(rule, ability_fragment_from_dict(ability_fragment_to_dict(rule)))
        self.assertEqual((rule, rule), canonical_ability_fragments((rule, rule)))
        with self.assertRaises(FrozenInstanceError):
            rule.schema_version = 2
        for value in ({"schema_version": True}, {"schema_version": 2}, {"schema_version": 1, "recipient": "all"}, {}):
            with self.assertRaises(ValueError):
                UmbraArmorSpec.from_dict(value)
        value = UmbraArmorProtection("aura", "U", "aura@0", "B", "body", "body@0", "A", 0)
        with self.assertRaises(ValueError):
            replace(value, recipient_object_id="aura")
        with self.assertRaises(ValueError):
            replace(value, instance=True)

    def test_registered_fragment_handler_has_closed_descriptor_and_no_mutation(self):
        rule = UmbraArmorSpec()
        descriptor = {"handler_id": UMBRA_ARMOR_HANDLER, "schema_version": 1,
                      "event": "characteristics.evaluate", "fragment": ability_fragment_to_dict(rule)}
        handler = UmbraArmorFragmentHandler()
        self.assertEqual((rule,), handler.lower(descriptor, None))
        for mutation in ({"extra": 0}, {"schema_version": 2}, {"event": "zone.change"},
                         {"fragment": ability_fragment_to_dict(UmbraArmorSpec()) | {"extra": 0}}):
            with self.assertRaises((ValueError, SemanticNodeError)):
                handler.validate({**descriptor, **mutation})

    def test_current_aura_relationship_keeps_source_and_recipient_ability_loss_distinct(self):
        recipient = CardInstance(object_id="body", ref="C", oracle_id="body", printed_name="Body",
                                 owner="A", controller="A", zone="battlefield")
        aura = CardInstance(object_id="aura", ref="U", oracle_id="aura", printed_name="Aura",
                            owner="B", controller="B", zone="battlefield", attached_to="body")
        rows = {"aura": {"type_line": "Enchantment — Aura", "ability_fragments": (UmbraArmorSpec(),)},
                "body": {"type_line": "Creature", "ability_fragments": ()}}
        host = SimpleNamespace(state=SimpleNamespace(cards={"body": recipient, "aura": aura}),
                               _effective_card_data=lambda card: rows[card.object_id])
        protections = current_umbra_armor_protections(host)
        self.assertEqual(1, len(protections))
        self.assertEqual(("B", "A"), (protections[0].aura_controller, protections[0].recipient_controller))
        rows["body"]["ability_fragments"] = ()
        self.assertEqual(protections, current_umbra_armor_protections(host))
        rows["aura"]["ability_fragments"] = ()
        self.assertFalse(current_umbra_armor_protections(host))
        rows["aura"]["ability_fragments"] = (UmbraArmorSpec(), UmbraArmorSpec())
        self.assertEqual(2, len(current_umbra_armor_protections(host)))
        for field, value in (("phased_out", True), ("zone", "graveyard"), ("attached_to", None)):
            previous = getattr(aura, field)
            setattr(aura, field, value)
            self.assertFalse(current_umbra_armor_protections(host))
            setattr(aura, field, previous)
        self.assertEqual(protections[0].aura_logical_object_id, aura.logical_object_id)


class UmbraArmorRepresentationReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "umbra.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", ROOT / "tests/fixtures/umbra-armor-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Umbra representation", [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay = witnesses.BoundEffectProgramRuntimeTests.replay

    def test_source_fragment_checkpoint_and_relationship_replay_are_exact(self):
        session = self.session(295001)
        engine = session.engine
        recipient = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        aura.attached_to = recipient.object_id
        aura.annotations["granted_ability_fragments"] = [ability_fragment_to_dict(UmbraArmorSpec())]
        before = current_umbra_armor_protections(engine)
        self.assertTrue(before)
        self.assertTrue(all(p.aura_controller == "B" and p.recipient_controller == "A" for p in before))
        self.checkpoint(session)
        resumed = self.replay(session, load=True)
        self.assertEqual(before, current_umbra_armor_protections(resumed.engine))
        self.assertEqual(aura.annotations["granted_ability_fragments"], resumed.state.cards[aura.object_id].annotations["granted_ability_fragments"])
