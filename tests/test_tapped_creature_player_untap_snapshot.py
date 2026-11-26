"""Sleep restrains the original creature incarnations in the original player's untap."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.affected_permanents import TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.exert_model import EXERT_PLAYER_ANNOTATION
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class TappedCreaturePlayerUntapSnapshotTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing
    activate = untap_fixture.NextPlayerUntapRestrictionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "tapped-creature-player-untap-snapshot.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/tapped-creature-player-untap-snapshot-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original player and creatures", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_sleep_binds_all_original_creatures_to_one_announced_player(self):
        admitted, semantics = register_card(self.db, self.registry, "Sleep")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Sleep", fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        instruction, = program.effects
        self.assertEqual("any", program.target_schema["player_relation"])
        self.assertEqual("set_public_tap_state", instruction["op"])
        self.assertEqual(1, instruction[TARGET_PLAYER_CREATURE_UNTAP_SNAPSHOT_FIELD])
        self.assertIn("untap.step.prohibit_next_controller", program.capability_dependencies)
        self.assertEqual("$target.0", instruction["set"]["target_controller"])
        for key, value in (("target_controller", "$controller"), ("query", {**instruction["set"]["query"], "tapped": False})):
            alternate = deepcopy(instruction)
            alternate["set"][key] = value
            self.assertFalse(bind_semantic_program_runtime(
                replace(program, effects=[alternate]), capability_registry=self.registry, profile="commander_review",
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("that player's next untap step",
                                                                     "their controllers' next untap steps"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def state_for(self, game, card):
        return game.state.cards[card.object_id]

    def upkeep(self, game, seat, *, after=-1):
        self.until(game, lambda current: current.state.active_player == seat
                   and current.state.step == "upkeep" and current.state.turn_sequence > after)

    def test_actual_sleep_includes_already_tapped_creatures_and_does_not_follow_a_control_exchange(self):
        game = self.playing(50203611)
        spell = self.add(game.engine, "Sleep", zone="hand")
        exchange = self.add(game.engine, "Switcheroo", zone="hand")
        departing = self.add(game.engine, "Llanowar Elves", seat="C", ref="sleep-original-moving-elf")
        retained = self.add(game.engine, "Grizzly Bears", seat="C", ref="sleep-original-retained-bear")
        newcomer = self.add(game.engine, "Llanowar Elves", seat="B", ref="sleep-new-elf")
        forest = self.add(game.engine, "Forest", seat="C", ref="sleep-foreign-type")
        for card in (spell, exchange, departing, newcomer):
            self.assert_closed(game, card)
        for elf in (departing, newcomer):
            elf.acquired_control_turn_count = -1
        self.prepare(game, mana={"C": 6, "U": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["C"], reserve={"C": 4, "U": 1})
        self.activate(game, "B", newcomer)
        self.activate(game, "C", departing)
        self.assertTrue(self.state_for(game, departing).tapped)
        self.assertFalse(self.state_for(game, retained).tapped)
        self.resolve_top(game)
        for original in (departing, retained):
            self.assertEqual(["C"], self.state_for(game, original).annotations[EXERT_PLAYER_ANNOTATION])
            self.assertTrue(self.state_for(game, original).tapped)
        self.assertNotIn(EXERT_PLAYER_ANNOTATION, self.state_for(game, newcomer).annotations)
        self.assertFalse(self.state_for(game, forest).tapped)

        self.cast_for(game, "A", exchange, [departing.ref, newcomer.ref])
        self.resolve_top(game)
        self.assertEqual(("B", "C"), (self.state_for(game, departing).controller,
                                      self.state_for(game, newcomer).controller))
        game = self.replay(game, load=True)
        self.upkeep(game, "B")
        self.assertFalse(self.state_for(game, departing).tapped)
        self.assertTrue(self.state_for(game, newcomer).tapped)
        self.assertEqual(["C"], self.state_for(game, departing).annotations[EXERT_PLAYER_ANNOTATION])
        self.upkeep(game, "C")
        self.assertTrue(self.state_for(game, retained).tapped)
        self.assertFalse(self.state_for(game, newcomer).tapped)
        for card in (departing, retained, newcomer):
            self.assertNotIn(EXERT_PLAYER_ANNOTATION, self.state_for(game, card).annotations)
        one_step = game.state.turn_sequence
        for seat in ("D", "A", "B", "C"):
            self.upkeep(game, seat, after=one_step)
        self.assertFalse(self.state_for(game, retained).tapped)
        self.replay(game, load=True)

    def test_actual_leave_and_return_erases_the_original_restriction_before_the_same_players_untap(self):
        game = self.playing(50203612)
        spell = self.add(game.engine, "Sleep", zone="hand")
        flicker = self.add(game.engine, "Ghostly Flicker", zone="hand", seat="B")
        retap = self.add(game.engine, "Twiddle", zone="hand", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        forest = self.add(game.engine, "Forest", seat="B")
        for card in (spell, flicker, retap):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 2})
        game.state.players["B"].mana_pool.update({"C": 2, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.resolve_top(game)
        old = self.state_for(game, creature).logical_object_id
        self.assertTrue(self.state_for(game, creature).tapped)
        self.assertEqual(["B"], self.state_for(game, creature).annotations[EXERT_PLAYER_ANNOTATION])
        self.cast_for(game, "B", flicker, [creature.ref, forest.ref], reserve={"U": 1})
        self.resolve_top(game)
        returned = self.state_for(game, creature)
        self.assertNotEqual(old, returned.logical_object_id)
        self.assertNotIn(EXERT_PLAYER_ANNOTATION, returned.annotations)
        self.cast_for(game, "B", retap, [returned.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        accepted = game.act("pilot:B", {"action_id": "choose", "choice": "tap"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(self.state_for(game, creature).tapped)
        game = self.replay(game, load=True)
        self.upkeep(game, "B")
        self.assertFalse(self.state_for(game, creature).tapped)
        self.replay(game, load=True)
