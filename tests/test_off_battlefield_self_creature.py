"""Native evidence for typed off-battlefield self creature characteristics."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from test_controller_creature_keyword_absence import register_card
import test_qualified_hexproof as native
from quorune.ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict
from quorune.carddb import CardDatabase, Ruling
from quorune.characteristic_evaluation import evaluate_card_characteristics
from quorune.commander import initial_commander_state
from quorune.continuous_effect_model import Layer
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig
from quorune.off_battlefield_creatures import (
    OFF_BATTLEFIELD_SELF_CREATURE_HANDLER,
    OffBattlefieldSelfCreatureSpec,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cross_zone_characteristics import prospective_cross_zone_spell_type_line
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from quorune.semantic_runtime.off_battlefield_creatures import OffBattlefieldSelfCreatureHandler
from scripts.build_test_database import build_fixture_database


SOURCE = "Cedar, Peak Wanderer"
REDUCER = "Highland Training Ring"
COUNTER = "Cliffside Denial"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class OffBattlefieldSelfCreatureTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.QualifiedHexproofTests.session
    add = native.QualifiedHexproofTests.add
    prepare = native.QualifiedHexproofTests.prepare
    checkpoint = native.QualifiedHexproofTests.checkpoint
    priority = native.QualifiedHexproofTests.priority
    cast = native.QualifiedHexproofTests.cast
    resolve = native.QualifiedHexproofTests.resolve
    replay = native.QualifiedHexproofTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "off-battlefield-self-creature.sqlite3"
        build_fixture_database(
            [FIXTURES / "bound-effect-program-cards.json", FIXTURES / "off-battlefield-self-creature-cards.json"],
            database,
        )
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = cls.make_deck("Generic Bound Commander")
        cls.config = GameConfig(semantic_policy="trusted_only", auto_pass_empty_priority=False)

    @staticmethod
    def make_deck(name):
        return DeckDefinition(
            name,
            [DeckEntry(name, 1, "commander"), DeckEntry("Generic Bound Plains", 32)],
            [name],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def assert_live_shape(self, session, source, *, creature):
        data = session.engine._effective_card_data(source)
        types, subtypes, supertypes = session.engine._type_parts(data["type_line"])
        self.assertEqual(types, {"planeswalker", "creature"} if creature else {"planeswalker"})
        self.assertEqual(subtypes, {"cedar", "elk"} if creature else {"cedar"})
        self.assertEqual(supertypes, {"legendary"})
        self.assertEqual((data["power"], data["toughness"]), ("2", "3") if creature else (None, None))

    def test_off_battlefield_commander_uses_stack_cost_then_enters_as_planeswalker(self):
        decks = {seat: self.make_deck(SOURCE) if seat == "A" else self.deck for seat in "ABCD"}
        session = CommanderSession.create(
            self.db, decks, first_player="A", seed=613_004_81, config=self.config,
        )
        source_id, = session.state.players["A"].zones["command"]
        source = session.state.cards[source_id]
        self.assertTrue(source.is_commander)
        self.assertEqual(source.zone, "command")
        self.assert_live_shape(session, source, creature=True)
        for principal in tuple(session.pending_principals()):
            action = session.act(principal, {"a": "keep"})
            self.assertTrue(action.ok, action.summary)
        reducer = self.add(session.engine, REDUCER)
        self.assertEqual(reducer.zone, "battlefield")
        private = self.add(session.engine, SOURCE, seat="D", zone="hand", ref="hidden-cedar")
        self.prepare(session, mana={"W": 1})
        self.checkpoint(session)
        command_incarnation = source.logical_object_id
        command = {"action_id": "cast:" + source.ref, "pay": "manual", "payment": {"W": 1}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(authoritative_state_hash(session.state), before)
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertTrue(any(row["id"] == command["action_id"] for row in offered))
        action = session.act("pilot:A", command)
        self.assertTrue(action.ok, action.summary)
        on_stack = session.state.cards[source_id]
        self.assertEqual(on_stack.zone, "stack")
        self.assert_live_shape(session, on_stack, creature=True)
        self.assertNotEqual(on_stack.logical_object_id, command_incarnation)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        resolved = session.state.cards[source_id]
        self.assertEqual(resolved.zone, "battlefield")
        self.assertEqual(resolved.counters.get("loyalty"), 3)
        self.assertNotEqual(resolved.logical_object_id, command_incarnation)
        self.assert_live_shape(session, resolved, creature=False)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.assertIn(private.ref, json.dumps(session.packet("pilot:D", full=True)))
        self.replay(session, load=True)

    def test_creature_counter_targets_actual_spell_then_public_graveyard_retains_the_type(self):
        session = self.session(613_004_82)
        source = self.add(session.engine, SOURCE, zone="hand")
        counter = self.add(session.engine, COUNTER, seat="B", zone="hand")
        self.prepare(session, mana={"C": 1, "W": 1})
        session.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(session)
        self.cast(session, "A", source, payment={"C": 1, "W": 1})
        self.assert_live_shape(session, session.state.cards[source.object_id], creature=True)
        target = session.state.stack[-1].ref
        self.priority(session, "B")
        action = next(row for row in session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"] == "cast:" + counter.ref)
        self.assertIn(target, action["target_schema"]["legal_refs"])
        self.cast(session, "B", counter, targets=[target], payment={"U": 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(current.zone, "graveyard")
        self.assert_live_shape(session, current, creature=True)
        self.replay(session)

    def test_off_battlefield_gate_requires_canonical_current_complete_source(self):
        source = self.db.lookup(SOURCE)
        binding, registered = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        deck = self.make_deck(SOURCE)

        def setup(semantics):
            return initial_commander_state(self.db, {"A": deck, "B": self.deck},
                                           config=self.config, semantics=semantics)

        self.assertEqual(setup(registered).commander_oracle_ids["A"], [source.oracle_id])
        with self.assertRaisesRegex(ValueError, "lacks supported eligibility"):
            setup(SemanticRegistry())
        changed_rulings = [Ruling(source.oracle_id, "2026-01-01", "fixture", "Source reference revised.")]
        with patch.object(self.db, "rulings", return_value=changed_rulings):
            with self.assertRaisesRegex(ValueError, "lacks supported eligibility"):
                setup(registered)
        with patch("quorune.compiler.cross_zone_type_addition_templates.off_battlefield_self_creature_handler",
                   return_value=None):
            ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual(ir.status, "exact")
            binding, stripped = register_card(self.db, self.registry, SOURCE)
            self.assertFalse(binding["strict_capability_ready"])
            with self.assertRaisesRegex(ValueError, "lacks supported eligibility"):
                setup(stripped)
        ir = compile_oracle_card(replace(source, oracle_text=source.oracle_text + "\nRewrite history."),
                                 capability_registry=self.registry, capability_profile=PROFILE)
        self.assertNotEqual(ir.status, "exact")

    def test_source_creature_codec_face_visibility_and_cast_view_boundaries(self):
        session = self.session(613_004_83)
        source = self.add(session.engine, SOURCE, zone="hand")
        spec = OffBattlefieldSelfCreatureSpec(2, 3, "elk")
        fragment = ability_fragment_to_dict(spec)
        self.assertEqual(ability_fragment_from_dict(fragment), spec)
        program, = (p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if any(d.get("handler_id") == OFF_BATTLEFIELD_SELF_CREATURE_HANDLER for d in p.handlers))
        descriptor, = program.handlers
        for raw in (dict(descriptor, schema_version=True), dict(descriptor, requires_complete_card_program=False),
                    dict(descriptor, characteristics={"power": True, "toughness": 3, "subtype": "elk"})):
            with self.assertRaises(ValueError):
                OffBattlefieldSelfCreatureHandler().validate(raw)
        base = session.engine._compiled_base_characteristics(source, session.engine.card_record(source))
        for zone in ("command", "exile", "graveyard", "hand", "library", "stack"):
            current = replace(source, zone=zone)
            before = evaluate_card_characteristics(current, base, maximum_layer=Layer.TEXT)
            self.assertNotIn("Creature", before["type_line"])
            after = evaluate_card_characteristics(current, base)
            self.assertIn("Creature", after["type_line"])
            self.assertEqual((after["power"], after["toughness"]), ("2", "3"))
        self.assertNotIn("Creature", evaluate_card_characteristics(
            replace(source, zone="exile", face_down=True), base)["type_line"])
        ordinary = prospective_cross_zone_spell_type_line(session.engine, source, "A", base["type_line"])
        self.assertIn("Creature", ordinary)
        faceless = prospective_cross_zone_spell_type_line(
            session.engine, source, "A", "Creature", include_self_characteristics=False,
        )
        self.assertIsNone(faceless)
