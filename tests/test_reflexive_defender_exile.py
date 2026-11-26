from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.reflexive_defender_exile import CAPABILITY
from scripts.build_test_database import build_fixture_database


SOURCE = "Auron, Venerated Guardian"


class ReflexiveDefenderExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "reflexive-defender.sqlite3"
        fixtures = ("bound-effect-program-cards", "reflexive-defender-relative-exile-cards", "activation-public-state-cards")
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Defender-relative exile", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def setup_attack(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, SOURCE)
        source.acquired_control_turn_count = -1
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="defender-small-creature")
        equal = self.add(game.engine, "Grizzly Bears", seat="B", ref="defender-equal-creature")
        equal.counters["+1/+1"] = 1
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="other-players-creature")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        self.add(game.engine, "Island")
        self.add(game.engine, "Forest", seat="B")
        walker, = game.engine.create_token("B", name="Defender planeswalker", characteristics={
            "type_line":"Token Planeswalker — Test", "loyalty":"5",
        })
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, walker)])
        (original,) = game.state.stack
        self.assertEqual(source.object_id, original.source_object_id)
        self.assertFalse(original.context.get("trigger_target_selection_pending"))
        self.resolve_top(game)
        self.assertEqual(3, game.engine._numeric_stat(source.object_id, "power"))
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        (child,) = game.state.stack
        self.assertEqual(original.ref, child.context["reflexive_parent"])
        self.assertEqual("B", child.context["target_schema_override"]["groups"][0]["controller_seat"])
        return game, source, target, equal, foreign, bounce, growth

    def choose(self, game, target, rejected):
        before = authoritative_state_hash(game.state)
        for card in rejected:
            result = game.act("pilot:A", {"action_id":"choose","targets":[card.ref]})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id":"choose","targets":[target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_reflexive_locks_defender_at_announcement_exiles_and_returns_after_source_departure(self):
        game, source, target, equal, foreign, bounce, _ = self.setup_attack(603129)
        game = self.replay(game, load=True)
        self.choose(game, target, (equal, foreign))
        self.resolve_top(game)
        exiled = game.state.cards[target.object_id]
        self.assertEqual("exile", exiled.zone)
        incarnation = exiled.logical_object_id
        game = self.replay(game, load=True)
        self.cast(game, "A", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        stayed = game.state.cards[target.object_id]
        self.assertEqual(("battlefield", "B"), (stayed.zone, stayed.controller))
        self.assertNotEqual(incarnation, stayed.logical_object_id)
        self.replay(game, load=True)

    def test_live_power_is_rechecked_after_the_reflexive_target_is_announced(self):
        game, source, target, equal, foreign, _, growth = self.setup_attack(603130)
        self.choose(game, target, (equal, foreign))
        self.cast(game, "B", growth, [target.ref])
        self.resolve_top(game)
        self.assertGreater(game.engine._numeric_stat(target.object_id, "power"),
                           game.engine._numeric_stat(source.object_id, "power"))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_exact_printed_attack_has_a_defender_relative_reflexive_child(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        (node,) = (node for face in ir.faces for node in face.nodes if CAPABILITY in node.capability_dependencies)
        self.assertIsNone(node.target_schema)
        self.assertEqual("creature.attacks", node.event)
        self.assertEqual("$context.defending_player", node.effects[0]["source_counter_reflexive"]["target_schema"]["controller_seat"])
        for before, after in (("less than", "greater than"), ("defending player controls", "an opponent controls")):
            changed = replace(record, oracle_text=record.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_missing_counter_child_is_killed(self):
        with patch("quorune.compiler.source_counter_reflexive.source_counter_reflexive_result", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_exact_printed_attack_has_a_defender_relative_reflexive_child()
