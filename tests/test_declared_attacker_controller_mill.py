"""Native evidence for milling the original attacker's controller."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_controller_information import is_occurrence_controller_reference
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Riddlekeeper"


class DeclaredAttackerControllerMillTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "declared-attacker-controller-mill.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/declared-attacker-controller-mill-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mill the attacker controller", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        return program

    def test_original_is_an_untargeted_mill_of_two_with_the_sealed_controller(self):
        program = self.program()
        self.assertEqual(("creature.attacks", "battlefield"), (program.event, program.active_zone))
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        self.assertEqual(("mill", 2), (effect["op"], effect["count"]))
        self.assertTrue(is_occurrence_controller_reference(effect["player"]))

    def test_raw_controller_mill_requires_the_defender_and_original_attacker(self):
        program = self.program()
        effect, = program.effects
        for altered in (
            replace(program, event="creature.blocks"),
            replace(program, active_zone="graveyard"),
            replace(program, event_condition=None),
            replace(program, effects=({**effect, "player": "$context.defending_player"},)),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_actual_player_and_planeswalker_declarations_mill_only_their_attackers_controller(self):
        game = self.session(50832001)
        observer = self.add(game.engine, ORIGINAL, seat="B")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        creatures = [self.add(game.engine, "Grizzly Bears", ref=f"riddle-attacker-{index}") for index in range(3)]
        for card in (observer, walker, *creatures):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        libraries = {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        before = len(game.state.players["A"].zones["graveyard"])
        self.attack(game, list(zip(creatures, ("B", walker.ref, "C"), strict=True)))
        self.assertEqual(2, self.trigger_count(game, observer))
        self.assertTrue(all(not item.targets for item in game.state.stack))
        game = self.replay(game, load=True)
        for _ in range(2):
            self.resolve_top(game)
        self.assertEqual(before + 4, len(game.state.players["A"].zones["graveyard"]))
        self.assertEqual(
            [4, 0, 0, 0], [libraries[seat] - len(game.state.players[seat].zones["library"]) for seat in "ABCD"],
        )
        self.replay(game, load=True)

    def test_actual_control_change_and_departure_select_the_same_original_creatures_controller(self):
        for departure in (False, True):
            with self.subTest(departure=departure):
                game = self.session(60825101 + departure)
                observer = self.add(game.engine, ORIGINAL, seat="B")
                creature = self.add(game.engine, "Grizzly Bears")
                theft = self.add(game.engine, "Turn Against", seat="C", zone="hand")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand") if departure else None
                private = self.add(game.engine, "Plains", seat="D", zone="hand", ref="riddle-private")
                for index in range(4):
                    self.add(game.engine, "Plains", seat="C", ref=f"riddle-mana-{index}")
                self.add(game.engine, "Mountain", seat="C")
                if departure:
                    self.add(game.engine, "Island", seat="B")
                for card in (observer, creature, theft, bounce):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(creature, "B")])
                self.assertEqual(1, self.trigger_count(game, observer))
                self.cast_for(game, "C", theft, [creature.ref])
                self.resolve_top(game)
                self.assertEqual("C", game.state.cards[creature.object_id].controller)
                if bounce is not None:
                    self.cast_for(game, "B", bounce, [creature.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[creature.object_id].zone)
                game = self.replay(game, load=True)
                libraries = {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}
                self.resolve_top(game)
                self.assertEqual(
                    [0, 0, 2, 0], [libraries[seat] - len(game.state.players[seat].zones["library"]) for seat in "ABCD"],
                )
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                self.replay(game, load=True)
