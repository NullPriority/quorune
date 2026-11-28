"""Fight excess and turn-red provenance through ordinary casts and replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_provenance import RETAIN_RED_MANA_UNTIL_CLEANUP
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fight_excess_mana import FIGHT_EXCESS_CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "The Last Agni Kai"
FIXTURE = ROOT / "tests/fixtures/fight-excess-mana-cards.json"


class FightExcessManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        work = Path(cls.temp.name)
        base = ROOT / "tests/fixtures/bound-effect-program-cards.json"
        neighbors = []
        for name, fixture in (
            ("Ambush Viper", "compiled-reviewed-trigger-cards.json"),
            ("Unsummon", "ability-label-composition-cards.json"),
        ):
            card, = (row for row in json.loads((ROOT / "tests/fixtures" / fixture).read_bytes())["cards"]
                     if row["name"] == name)
            neighbors.append(card)
        subset = work / "neighbors.json"
        subset.write_text(json.dumps({"schema_version": 1, "cards": neighbors, "rulings": []}), encoding="utf8")
        database = work / "fight-excess.sqlite3"
        build_fixture_database([base, FIXTURE, subset], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        command = "Generic Bound Commander"
        cls.deck = DeckDefinition("Fight excess mana", [
            DeckEntry(command, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [command])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_exact_fight_and_turn_mana_grammar_rejects_mutated_qualifiers(self):
        card = self.db.lookup(CARD, fuzzy=False)

        def compile(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        ir = compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(all(FIGHT_EXCESS_CAPABILITY in row.capability_dependencies for row in ir.faces[0].nodes))
        for old, new in (("target creature an opponent controls", "another target creature"),
                         ("is dealt excess damage", "deals excess damage"), ("unspent red mana", "unspent green mana")):
            with self.subTest(new=new):
                self.assertNotEqual("exact", compile(
                    replace(card, oracle_text=card.oracle_text.replace(old, new)),
                ).status)

    def board(self, seed, *, uses_viper):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        spell = self.add(game.engine, CARD, zone="hand")
        mine = self.add(game.engine, "Ambush Viper" if uses_viper else "Grizzly Bears", ref="agni-fighter")
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="agni-opposing-fighter")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "R": 2, "G": 1})
        return game, spell, mine, target

    def test_actual_fight_seals_deathtouch_excess_and_preserves_only_red_until_cleanup(self):
        game, spell, mine, target = self.board(120100911, uses_viper=True)
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [mine.ref, target.ref], reserve={"R": 1, "G": 1})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (mine, target):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        self.assertEqual(1, game.state.players["A"].stats[RETAIN_RED_MANA_UNTIL_CLEANUP])
        self.until(game, lambda s: s.state.step == "beginning_combat")
        self.assertEqual((2, 0), tuple(game.state.players["A"].mana_pool[key] for key in ("R", "G")))
        self.until(game, lambda s: s.state.active_player != "A")
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.assertNotIn(RETAIN_RED_MANA_UNTIL_CLEANUP, game.state.players["A"].stats)
        self.replay(game, load=True)

    def test_actual_single_illegal_fighter_leaves_the_independent_red_retention(self):
        game, spell, mine, target = self.board(120100912, uses_viper=False)
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        game.state.players["B"].mana_pool.update(U=1)
        self.assert_closed(game, bounce)
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [mine.ref, target.ref], reserve={"R": 1, "G": 1})
        self.cast_for(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[mine.object_id].marked_damage)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.until(game, lambda s: s.state.step == "beginning_combat")
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.replay(game, load=True)
