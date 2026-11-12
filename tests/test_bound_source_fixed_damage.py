from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
from quorune.compiler.trigger_result_templates import binding_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import _effect_template
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compiled_reviewed_trigger_consumers as native


class BoundSourceFixedDamageCompilerTests(unittest.TestCase):
    def test_only_explicit_source_heads_bind_the_fixed_damage_pronoun(self):
        for subject, state, accepted in (
            ("this creature", "tapped", True), ("this creature", "untapped", True),
            ("enchanted creature", "tapped", False), ("enchanted creature", "untapped", False),
            ("a creature you control", "tapped", False), ("a creature you control", "untapped", False),
        ):
            with self.subTest(subject=subject, state=state):
                binding = fixed_counter_trigger_binding(
                    f"Whenever {subject} becomes {state}, it deals 1 damage to any target.", card_name="Fixture",
                )
                self.assertIsNotNone(binding)
                result, _current = binding_effect_template(
                    binding, binding.body, card_name="Fixture", effect_template=_effect_template,
                )
                self.assertEqual(accepted, result[0] is not None)
                if accepted:
                    self.assertEqual(({"op": "damage", "source": "$source", "target": "$target.0", "amount": 1},), result[1])


class BoundSourceFixedDamageRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.CompiledReviewedTriggerConsumerTests.session
    add = native.CompiledReviewedTriggerConsumerTests.add
    ready = native.CompiledReviewedTriggerConsumerTests.ready
    prepare = native.CompiledReviewedTriggerConsumerTests.prepare
    checkpoint = native.CompiledReviewedTriggerConsumerTests.checkpoint
    priority = native.CompiledReviewedTriggerConsumerTests.priority
    actions = native.CompiledReviewedTriggerConsumerTests.actions
    binding = native.CompiledReviewedTriggerConsumerTests.binding
    reject = native.CompiledReviewedTriggerConsumerTests.reject
    settle = native.CompiledReviewedTriggerConsumerTests.settle
    replay = native.CompiledReviewedTriggerConsumerTests.replay
    cast = native.CompiledReviewedTriggerConsumerTests.cast
    choose = native.CompiledReviewedTriggerConsumerTests.choose
    advance = native.CompiledReviewedTriggerConsumerTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "bound-source-fixed-damage.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/bound-source-fixed-damage-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Bound source fixed damage", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_tapping_source_triggers_once_and_damage_survives_its_departure(self):
        session = self.session(968_000)
        fervor = self.add(session.engine, "Fervor", zone="hand")
        source = self.add(session.engine, "Goblin Medics", zone="hand")
        island = self.add(session.engine, "Island", seat="B")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        self.ready(session, {"C": 10, "R": 10})
        self.cast(session, fervor)
        self.assertIsNone(self.settle(session))
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.advance(session, lambda s: getattr(s.state.pending_decision, "kind", None) == "combat.attackers")
        command = {"action_id": "attack", "atk": {source.ref: "B"}}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", self.settle(session).kind)
        session = self.replay(session, load=True)
        self.choose(session, targets=["B"])
        self.priority(session, "B")
        self.assertEqual(1, len(session.state.stack))
        row = next(row for row in self.actions(session, "B") if row["id"].startswith("activate:" + island.ref + ":"))
        result = session.act("pilot:B", {"action_id": row["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(session.state.stack))
        self.cast(session, bounce, [source.ref], seat="B")
        self.settle(session)
        self.assertEqual([40, 39, 40, 40], [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_existing_source_departure_grammar_deals_its_printed_damage(self):
        for index, (name, entry, departure) in enumerate((("Firemaw Kavu", 2, 4), ("Spitebellows", 0, 6))):
            with self.subTest(source=name):
                session = self.session(968_010 + index)
                source = self.add(session.engine, name, zone="hand")
                body = self.add(session.engine, "Generic Bound Body", seat="B")
                other = self.add(session.engine, "Generic Bound Body", seat="C", ref="departure-second-body")
                other.counters["+1/+1"] = 1
                bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                session.state.players["B"].mana_pool["U"] = 1
                self.ready(session, {"C": 20, "R": 20})
                self.cast(session, source, cost_option="normal")
                decision = self.settle(session)
                if entry:
                    self.assertEqual("semantic.target", decision.kind)
                    self.reject(session, {"action_id": "choose", "targets": ["B"]})
                    self.choose(session, targets=[body.ref])
                    self.assertIsNone(self.settle(session))
                else:
                    self.assertIsNone(decision)
                self.assertEqual(entry, session.state.cards[body.object_id].marked_damage)
                self.cast(session, bounce, [source.ref], seat="B")
                self.assertEqual("semantic.target", self.settle(session).kind)
                session = self.replay(session, load=True)
                self.choose(session, targets=[other.ref])
                self.assertIsNone(self.settle(session))
                self.assertEqual(departure, session.state.cards[other.object_id].marked_damage)
                self.assertEqual("hand", session.state.cards[source.object_id].zone)
                self.replay(session, load=True)

    def test_entry_and_departure_each_make_one_independently_targeted_trigger(self):
        session = self.session(968_020)
        source = self.add(session.engine, "Keldon Marauders", zone="hand")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        session.state.players["B"].mana_pool["U"] = 1
        self.ready(session, {"C": 10, "R": 10})
        self.cast(session, source)
        self.assertEqual("semantic.target", self.settle(session).kind)
        self.choose(session, targets=["B"])
        self.assertIsNone(self.settle(session))
        self.assertEqual([40, 39, 40, 40], [session.state.players[seat].life for seat in "ABCD"])
        self.cast(session, bounce, [source.ref], seat="B")
        self.assertEqual("semantic.target", self.settle(session).kind)
        session = self.replay(session, load=True)
        self.choose(session, targets=["C"])
        self.assertIsNone(self.settle(session))
        self.assertEqual([40, 39, 39, 40], [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)
