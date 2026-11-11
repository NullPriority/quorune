from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_compiled_reviewed_trigger_consumers as native


class SourceEventPlayerDamageCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text):
        return compile_oracle_card(replace(query_record(text), type_line="Creature — Vampire"),
            capability_registry=self.registry, capability_profile="commander_review")

    def test_defending_player_is_untargeted_and_keeps_the_original_damage_source(self):
        ir = self.compile("Whenever this creature attacks, it deals 1 damage to defending player.")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertEqual(({"op": "damage", "source": "$source", "target": "$context.defending_player", "amount": 1},), tuple(node.effects))
        self.assertIn("resolution.event_player.fixed_damage", node.capability_dependencies)
        targeted = self.compile("Whenever this creature attacks, it deals 1 damage to target opponent.")
        self.assertEqual("exact", targeted.status, targeted.material_residuals)
        self.assertIsNotNone(targeted.faces[0].nodes[0].target_schema)

    def test_other_sources_and_distinct_attacked_object_recipients_stay_residual(self):
        for text in (
            "Whenever a creature attacks, it deals 1 damage to defending player.",
            "Whenever equipped creature attacks, it deals 1 damage to defending player.",
            "Whenever this creature attacks, it deals 1 damage to the player or planeswalker it's attacking.",
            "Whenever this creature attacks, it deals 1 damage to defending player chosen at random.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)


class SourceEventPlayerDamageRuntimeTests(unittest.TestCase):
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
    advance = native.CompiledReviewedTriggerConsumerTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-event-player-damage.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/source-event-player-damage-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source event-player damage", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_attacking_a_planeswalker_damages_its_hexproof_controller_without_targeting(self):
        session = self.session(970_000)
        source = self.add(session.engine, "Falkenrath Perforator", zone="hand")
        fervor = self.add(session.engine, "Fervor", zone="hand")
        shock = self.add(session.engine, "Shock", zone="hand")
        walker = self.add(session.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        self.add(session.engine, "Leyline of Sanctity", seat="B")
        self.ready(session, {"C": 20, "R": 10})
        row = next(row for row in self.actions(session) if row.get("card") == shock.ref)
        self.reject(session, {"action_id": row["id"], "targets": ["B"], "pay": "auto"})
        for card in (fervor, source):
            self.cast(session, card)
            self.assertIsNone(self.settle(session))
        self.advance(session, lambda s: getattr(s.state.pending_decision, "kind", None) == "combat.attackers")
        command = {"action_id": "attack", "atk": {source.ref: walker.ref}}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.priority(session, "A")
        self.assertEqual(1, len(session.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        self.assertEqual("priority", session.state.pending_decision.kind)
        self.assertEqual([40, 39, 40, 40], [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(3, session.state.cards[walker.object_id].counters["loyalty"])
        self.replay(session, load=True)
