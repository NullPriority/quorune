"""Native evidence for an all-zone rounded maximum of opposing life totals."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import QueryPowerToughnessDefinitionSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Malignus"


class HalfHighestOpponentLifeDefiningStatsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "half-highest-opponent-life-defining-stats.sqlite3"
        fixture = ROOT / "tests/fixtures/half-highest-opponent-life-defining-stats-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Rounded opponent life statistics", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def stats(self, game, source):
        values = game.engine._effective_card_data(game.state.cards[source.object_id])
        return tuple(int(values[key]) for key in ("power", "toughness"))

    def test_original_binds_both_defining_stats_and_the_independent_damage_prohibition(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, len(programs))
        definition, = [item for item in programs if item.active_zone == "all"]
        self.assertEqual("continuous", definition.event)
        descriptor, = definition.handlers
        fragment = QueryPowerToughnessDefinitionSpec.from_dict(descriptor["fragment"]["value"])
        self.assertEqual("defining_half_maximum_opponent_life", fragment.quantity.scope.value)
        self.assertEqual(11, fragment.quantity.schema_version)
        self.assertTrue(fragment.define_power and fragment.define_toughness)
        self.assertTrue(any(item.active_zone == "battlefield" and item.handlers for item in programs))
        for alteration in (
            card.oracle_text.replace("rounded up", "rounded down"),
            card.oracle_text.replace("your opponents", "all players"),
            card.oracle_text.replace("highest", "lowest"),
            card.oracle_text.replace("rounded up.", "rounded up. Draw a card."),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=alteration), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, alteration)

    def test_signed_half_ceiling_and_undetermined_opponent_maximum_use_the_typed_definition(self):
        game = self.session(6043950)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, source)
        for seat, life in {"B": -7, "C": -3, "D": -6}.items():
            game.state.players[seat].life = life
        self.assertEqual((-1, -1), self.stats(game, source))
        for seat in "BCD":
            game.state.players[seat].in_game = False
        self.assertEqual((0, 0), self.stats(game, source))

    def test_actual_opposing_life_and_departure_update_the_original_and_its_damage_stays_unpreventable(self):
        game = self.session(6043960)
        source = self.add(game.engine, ORIGINAL)
        source.acquired_control_turn_count = -1
        gain = self.add(game.engine, "Revitalize", seat="C", zone="hand")
        healer = self.add(game.engine, "Samite Healer", seat="B")
        healer.acquired_control_turn_count = -1
        for card in (source, gain, healer):
            self.assert_closed(game, card)
        for seat, life in {"A": 48, "B": 13, "C": 17, "D": 5}.items():
            game.state.players[seat].life = life
        self.prepare(game)
        game.state.players["C"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.assertEqual((9, 9), self.stats(game, source))
        self.cast_for(game, "C", gain)
        self.resolve_top(game)
        self.assertEqual(20, game.state.players["C"].life)
        self.assertEqual((10, 10), self.stats(game, source))
        self.priority(game, "C")
        retired = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(retired.ok, retired.summary)
        self.assertFalse(game.state.players["C"].in_game)
        self.assertEqual((7, 7), self.stats(game, source))
        game = self.replay(game, load=True)

        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + healer.ref + ":")]
        shielded = game.act("pilot:B", {"action_id": action, "targets": ["B"], "pay": "auto"})
        self.assertTrue(shielded.ok, shielded.summary)
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields

        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:A", {"action_id": "attack", "attackers": {source.ref: "B"}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda current: current.state.players["B"].life < 13)
        self.assertEqual(6, game.state.players["B"].life)
        self.assertIn(shield, game.state.damage_prevention_shields)
        damage, = game.engine._current_turn_history("player_damaged")
        self.assertEqual(("A", "B", 7), (damage.actor, damage.target, damage.amount))
        self.assertEqual((3, 3), self.stats(game, source))
        self.replay(game, load=True)

    def test_actual_control_change_and_destroyed_card_use_different_opponent_rosters(self):
        game = self.session(6043970)
        source = self.add(game.engine, ORIGINAL)
        donate = self.add(game.engine, "Donate", zone="hand")
        murder = self.add(game.engine, "Murder", seat="C", zone="hand")
        for card in (source, donate, murder):
            self.assert_closed(game, card)
        for seat, life in {"A": 33, "B": 52, "C": 23, "D": 5}.items():
            game.state.players[seat].life = life
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["C"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)
        self.assertEqual((26, 26), self.stats(game, source))
        self.cast_for(game, "A", donate, ["B", source.ref])
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (source.owner, game.state.cards[source.object_id].controller))
        self.assertEqual((17, 17), self.stats(game, source))
        self.cast_for(game, "C", murder, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual((26, 26), self.stats(game, source))
        self.replay(game, load=True)
