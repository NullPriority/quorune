"""Actual per-attacker results when a declared creature attacks an opponent."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Briar Patch", "Calculating Lich", "Genestealer Locus", "Skyboon Evangelist")


class DeclaredAttackAgainstOpponentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "declared-attack-against-opponent.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/declared-attack-against-opponent-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Declared attacks against an opponent", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return semantics.programs_for_oracle(card.oracle_id)

    def test_complete_originals_keep_each_attack_frame_and_the_support_sibling(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                programs = self.programs(name)
                attacks = [program for program in programs if program.event == "creature.attacks"]
                self.assertEqual(2 if name == "Genestealer Locus" else 1, len(attacks))
                self.assertTrue(all(program.target_schema is None for program in attacks))
                if name == "Calculating Lich":
                    self.assertEqual("$context.defending_player", attacks[0].effects[0]["player"])
                if name == "Skyboon Evangelist":
                    self.assertTrue(any(program.event in ("creature.enter.self", "permanent.enter.self")
                                        for program in programs))

    def test_opposing_player_recipient_cannot_be_removed_from_its_raw_program(self):
        attack, = [program for program in self.programs("Calculating Lich")
                   if program.event == "creature.attacks"]
        for broken in (
            replace(attack, event="creature.blocks"),
            replace(attack, event_condition=None),
            replace(attack, event_condition={"field": "defending_player", "op": "ne", "value": "$source.controller"}),
        ):
            binding = bind_semantic_program_runtime(broken, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_actual_declared_attacks_keep_the_defender_life_and_original_creature_roles(self):
        game = self.session(50836101)
        locus = self.add(game.engine, "Genestealer Locus", seat="B")
        lich = self.add(game.engine, "Calculating Lich", seat="B")
        briar = self.add(game.engine, "Briar Patch", seat="B")
        walker = self.add(game.engine, "Jace Beleren", seat="C")
        walker.counters["loyalty"] = 3
        own, opponent, permanent = [
            self.add(game.engine, "Grizzly Bears", ref=f"opposition-test-{index}") for index in range(3)
        ]
        for card in (locus, lich, briar, walker, own, opponent, permanent):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(own, "B"), (opponent, "C"), (permanent, walker.ref)])
        self.assertEqual((2, 1, 1), tuple(self.trigger_count(game, card) for card in (locus, lich, briar)))
        game = self.replay(game, load=True)
        while game.state.stack:
            self.resolve_top(game)
        self.assertEqual([40, 40, 39, 40], [game.state.players[seat].life for seat in "ABCD"])
        for body, expected in ((own, (0, 2)), (opponent, (2, 3)), (permanent, (2, 2))):
            stats = tuple(game.engine._numeric_stat(body.object_id, field) for field in ("power", "toughness"))
            self.assertEqual(expected, stats)
        self.replay(game, load=True)

    def test_actual_support_and_any_counter_keep_only_the_eligible_declared_player_attacker(self):
        game = self.session(50836121)
        source = self.add(game.engine, "Skyboon Evangelist", zone="hand")
        supported, nonstat, ordinary, to_walker = [
            self.add(game.engine, "Grizzly Bears", ref=f"skyboon-test-{index}") for index in range(4)
        ]
        nonstat.counters["charge"] = 1
        to_walker.counters["charge"] = 1
        walker = self.add(game.engine, "Jace Beleren", seat="C")
        walker.counters["loyalty"] = 3
        for card in (source, walker, supported, nonstat, ordinary, to_walker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1, "C": 6})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        choice = self.resolve(game)
        self.assertEqual("semantic.target", choice.kind)
        self.target(game, "A", [supported.ref])
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[supported.object_id].counters["+1/+1"])
        self.attack(game, [(supported, "C"), (nonstat, "D"), (ordinary, "C"), (to_walker, walker.ref)])
        self.assertEqual(2, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        while game.state.stack:
            self.resolve_top(game)
        for card in (supported, nonstat, ordinary, to_walker):
            keywords = game.engine._combat_keywords(game.state.cards[card.object_id])
            self.assertEqual(card in (supported, nonstat), "flying" in keywords)
        self.replay(game, load=True)
