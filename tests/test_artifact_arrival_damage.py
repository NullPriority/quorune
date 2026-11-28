"""Real committed artifact counts, damage recipients and replay."""

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture

from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.aggregated_arrival_counters import matched_arrival_identities
from quorune.rules.aggregated_arrival_damage import CAPABILITY, artifact_arrival_damage_effect
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


SOURCE = "Ingenious Artillerist"


class ArtifactArrivalDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        timing = json.loads((fixtures / "activation-target-view-cards.json").read_bytes())
        timing["cards"] = [card for card in timing["cards"] if card["name"] == "Vedalken Orrery"]
        assert len(timing["cards"]) == 1
        oracle = timing["cards"][0]["oracle_id"]
        timing["rulings"] = [row for row in timing.get("rulings", []) if row["oracle_id"] == oracle]
        support = folder / "orrery.json"
        support.write_text(json.dumps(timing), encoding="utf-8")
        database = folder / "artifact-arrival-damage.sqlite3"
        build_fixture_database([support, fixtures / "artifact-arrival-damage-cards.json",
                                fixtures / "next-personal-turn-protection-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed artifact arrival damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def start(self, game, *cards, mana):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def triggers(self, game, source):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and item.source_object_id == source.object_id]

    def assert_life(self, game, opponents):
        self.assertEqual([40, opponents, opponents, opponents], [game.state.players[seat].life for seat in "ABCD"])

    def test_exact_bound_arrival_result_rejects_other_predicates_and_damage_shapes(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual((artifact_arrival_damage_effect(),), tuple(node.effects))
        self.assertIsNone(node.target_schema)
        self.assertIn(CAPABILITY, node.capability_dependencies)
        for old, new in (
            ("artifacts you control", "creatures you control"), ("artifacts you control", "artifacts an opponent controls"),
            ("each opponent", "each player"), ("each opponent", "any target"),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        game = self.session(60320601)
        source = self.add(game.engine, SOURCE)
        self.assert_closed(game, source)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        conditions = [{"field": "types", "op": "contains_any", "value": ["creature"]},
                      {"field": "controller", "op": "eq", "value": "$source.controller"}]
        bad = replace(program, event_condition={"all": conditions})
        self.assertFalse(bind_semantic_program_runtime(
            bad, capability_registry=self.registry, profile="commander_review",
        )["strict"])

    def test_actual_two_artifact_batch_matches_controller_and_survives_source_removal(self):
        game = self.session(60320602)
        source = self.add(game.engine, SOURCE)
        exhibition = self.add(game.engine, "Servo Exhibition", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", zone="hand")
        offscope = self.add(game.engine, "Sol Ring", zone="hand", seat="B")
        flash = self.add(game.engine, "Vedalken Orrery", seat="B")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand", seat="B")
        game.state.players["B"].mana_pool.update({"C": 1, "R": 1})
        original_identity = source.logical_object_id
        self.start(game, source, exhibition, bear, offscope, flash, bolt, mana={"C": 2, "W": 1, "G": 1})
        self.cast_for(game, "B", offscope, reserve={"R": 1})
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[offscope.object_id].controller)
        self.assertFalse(self.triggers(game, source))
        self.cast_for(game, "A", bear, reserve={"C": 1, "W": 1})
        self.resolve_top(game)
        self.assertFalse(self.triggers(game, source))
        self.cast_for(game, "A", exhibition)
        self.resolve_top(game)
        trigger, = self.triggers(game, source)
        tokens = [card for card in game.state.cards.values() if card.is_token and card.controller == "A"]
        self.assertEqual(2, len(tokens))
        self.assertEqual({card.logical_object_id for card in tokens}, set(matched_arrival_identities(trigger.context)))
        self.cast_for(game, "B", bolt, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assert_life(game, 40)
        game = self.replay(game, load=True)
        cutoff = game.state.event_sequence
        self.resolve_top(game)
        self.assert_life(game, 38)
        batch, = [event for event in game.state.events if event.event_id > cutoff
                  and event.code == "effect.damage.fixed_set"]
        self.assertEqual(("A", 2, 3), (batch.actor, batch.details["assigned_amount"], batch.details["recipient_count"]))
        self.assertEqual({(seat, "player", 2, original_identity, False) for seat in "BCD"},
                         {(damage["target"], damage["target_kind"], damage["amount"],
                           damage["source_logical_object_id"], damage["combat"])
                          for damage in batch.details["damage_events"]})
        self.replay(game, load=True)

    def test_separate_played_artifacts_have_independent_one_member_triggers(self):
        game = self.session(60320603)
        source = self.add(game.engine, SOURCE)
        flash = self.add(game.engine, "Vedalken Orrery")
        first = self.add(game.engine, "Sol Ring", zone="hand")
        second = self.add(game.engine, "Bonesplitter", zone="hand")
        self.start(game, source, flash, first, second, mana={"C": 2})
        self.cast_for(game, "A", first)
        self.resolve_top(game)
        trigger, = self.triggers(game, source)
        self.assertEqual((game.state.cards[first.object_id].logical_object_id,), matched_arrival_identities(trigger.context))
        self.cast_for(game, "A", second)
        self.resolve_top(game)
        both = self.triggers(game, source)
        self.assertEqual(2, len(both))
        self.assertEqual({(game.state.cards[card.object_id].logical_object_id,) for card in (first, second)},
                         {matched_arrival_identities(item.context) for item in both})
        self.assert_life(game, 40)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assert_life(game, 39)
        self.resolve_top(game)
        self.assert_life(game, 38)
        self.replay(game, load=True)
