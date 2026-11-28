"""Bontu destroys the current creatures and restrains the next actual controller untap."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.next_player_untap_model import NextPlayerUntapRestriction, NextPlayerUntapScope
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Bontu's Last Reckoning"


class DestroyAndNextLandUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing
    activate = untap_fixture.NextPlayerUntapRestrictionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "destroy-and-next-land-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/destroy-and-next-land-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current lands at the next actual untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_is_an_ordered_untargeted_creature_wipe_and_controller_land_rule(self):
        admitted, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == "resolve")
        self.assertIsNone(program.target_schema)
        destroy, untap = program.effects
        self.assertEqual(("destroy_all", "any", ["creature"]),
                         (destroy["op"], destroy["set"]["controller_relation"], destroy["set"]["query"]["types_all"]))
        self.assertEqual({"op": "prohibit_next_untap", "schema_version": 2, "player": "$controller", "scope": "lands"}, untap)
        for changed in (replace(program, effects=[untap, destroy]),
                        replace(program, effects=[destroy, {**untap, "scope": "creatures_and_lands"}]),
                        replace(program, effects=[destroy, {**untap, "player": "$target.0"}])):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        for text in (card.oracle_text.replace("your next untap step", "your next two untap steps"),
                     card.oracle_text.replace("Destroy all creatures.", "Destroy all artifacts.")):
            ir = compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)

    def test_actual_two_wipes_restrict_both_old_and_new_lands_for_only_one_actual_untap(self):
        game = self.playing(50203091)
        wipes = [self.add(game.engine, ORIGINAL, zone="hand", ref=f"bontu-original-{index}") for index in range(2)]
        creatures = [self.add(game.engine, "Runeclaw Bear", seat=seat, ref=f"bontu-creature-{seat}") for seat in "AB"]
        swamp = self.add(game.engine, "Swamp", ref="bontu-existing-land")
        future = self.add(game.engine, "Forest", zone="hand", ref="bontu-late-land")
        ring = self.add(game.engine, "Sol Ring")
        manual = self.add(game.engine, "Twiddle", zone="hand")
        warps = [self.add(game.engine, "Time Warp", zone="hand", ref=f"bontu-warp-{index}") for index in range(2)]
        for card in (*wipes, *warps, manual, ring):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "B": 3, "U": 5})
        self.checkpoint(game)
        start_turn = game.state.turn_sequence
        scheduled = (NextPlayerUntapRestriction("A", NextPlayerUntapScope.LANDS),)
        self.activate(game, "A", swamp)
        self.cast_for(game, "A", wipes[0], reserve={"C": 7, "B": 2, "U": 5})
        self.assertEqual((), game.state.next_player_untap_restrictions)
        self.resolve_top(game)
        self.assertEqual(["graveyard", "graveyard"], [game.state.cards[card.object_id].zone for card in creatures])
        self.assertEqual(scheduled, game.state.next_player_untap_restrictions)
        self.cast_for(game, "A", wipes[1], reserve={"C": 6, "U": 5})
        self.resolve_top(game)
        self.assertEqual(scheduled, game.state.next_player_untap_restrictions)
        self.cast_for(game, "A", manual, [swamp.ref], reserve={"C": 6, "U": 4})
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        used = game.act("pilot:A", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(used.ok, used.summary)
        self.assertFalse(game.state.cards[swamp.object_id].tapped)
        self.assertEqual(scheduled, game.state.next_player_untap_restrictions)
        self.activate(game, "A", swamp)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        play, = (row["id"] for row in actions if row.get("action") == "play_land" and row.get("card") == future.ref)
        played = game.act("pilot:A", {"action_id": play})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", game.state.cards[future.object_id].zone)
        for source in (future, ring):
            self.activate(game, "A", source)
        self.cast_for(game, "A", warps[0], ["A"], reserve={"C": 3, "U": 2})
        self.resolve_top(game)
        self.cast_for(game, "A", warps[1], ["A"])
        self.resolve_top(game)
        self.assertEqual(["A", "A"], [entry.player for entry in game.state.extra_turns])
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.turn_sequence == start_turn + 1)
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertEqual([True, True, False],
                         [game.state.cards[source.object_id].tapped for source in (swamp, future, ring)])
        self.assertEqual((), game.state.next_player_untap_restrictions)
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.turn_sequence == start_turn + 2)
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertEqual([False, False], [game.state.cards[source.object_id].tapped for source in (swamp, future)])
        self.replay(game, load=True)
