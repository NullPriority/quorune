"""Native replay for the closed passive reciprocal damage loop."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_received_damage_watch as watched
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mandatory_loop_model import (
    LINKED_EXILE_LOOP_FAMILY, MandatoryLinkedLoopWatch, RECEIVED_DAMAGE_LOOP_FAMILY,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


_ARMOR = "Urza's Armor"


class LoopWitnessVersionTests(unittest.TestCase):
    def test_version_two_requires_an_exact_family_and_separates_proof_domains(self):
        position = "a" * 64
        witness = MandatoryLinkedLoopWatch(schema_version=2)
        linked, repeated = witness.observe(position, "S1", family=LINKED_EXILE_LOOP_FAMILY)
        self.assertFalse(repeated)
        self.assertEqual(linked, MandatoryLinkedLoopWatch.from_dict(linked.to_dict()))
        for invalid in (None, True, ["received_damage"], "another_family"):
            with self.subTest(family=invalid), self.assertRaises(ValueError):
                MandatoryLinkedLoopWatch.from_dict({**linked.to_dict(), "family": invalid})
        received, repeated = linked.observe(position, "S2", family=RECEIVED_DAMAGE_LOOP_FAMILY)
        self.assertFalse(repeated)
        self.assertEqual(RECEIVED_DAMAGE_LOOP_FAMILY, received.family)
        self.assertEqual(received, MandatoryLinkedLoopWatch.from_dict(received.to_dict()))
        self.assertEqual(witness, received.cleared())
        self.assertTrue(received.observe(position, "S3", family=RECEIVED_DAMAGE_LOOP_FAMILY)[1])


class ReceivedDamageLoopTests(InheritedNativeFixture, unittest.TestCase):
    _cast = watched.ReceivedDamageWatchTests._cast
    _watch = watched.ReceivedDamageWatchTests._watch
    _record = watched.ReceivedDamageWatchTests._record
    _triggers = staticmethod(watched.ReceivedDamageWatchTests._triggers)
    _life = staticmethod(watched.ReceivedDamageWatchTests._life)

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "received-damage-loop.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "received-damage-watch-cards.json", "received-damage-loop-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Mandatory reciprocal damage",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def _scene(self, seed, *, opening="Spark Spray", protected="ABCD", response=None, life=40, old=False):
        game = self.session(seed)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
            player.life = life
        arcbonds = [self.add(game.engine, "Arcbond", zone="hand", ref=f"loop-arcbond-{i}") for i in (1, 2)]
        starter = self.add(game.engine, opening, zone="hand")
        myrs = [self.add(game.engine, "Darksteel Myr", seat=seat, ref=f"loop-myr-{seat}") for seat in "BC"]
        shields = [self.add(game.engine, _ARMOR, seat=seat, ref=f"loop-armor-{seat}") for seat in protected]
        helper = self.add(game.engine, response, seat="D", zone="hand") if response is not None else None
        for card in (*arcbonds, starter, *myrs, *shields, *((helper,) if helper else ())):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "R": 3})
        if response == "Healing Salve":
            game.state.players["D"].mana_pool["W"] = 1
        if response == "Stifle":
            game.state.players["D"].mana_pool["U"] = 1
        if old:
            game.state.mandatory_linked_loop = MandatoryLinkedLoopWatch()
        self.checkpoint(game)

        if response == "Healing Salve":
            self.priority(game, "D")
            offered = self.action(game, "D", helper)
            result = game.act("pilot:D", {"action_id": offered["id"], "modes": ["mode_2"],
                                         "targets": ["D"], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.resolve_top(game)
        self._watch(game, "A", arcbonds[0], myrs[0], reserve={"C": 2, "R": 2})
        if response == "Stifle":
            self.cast_for(game, "A", arcbonds[1], [myrs[1].ref], reserve={"R": 1})
            self.priority(game, "D")
            offers = game.packet("pilot:D", full=True)["decision"]["ctx"]["legal"]["cast"]
            self.assertNotIn(helper.ref, offers)
            self.resolve_top(game)
        else:
            self._watch(game, "A", arcbonds[1], myrs[1], reserve={"R": 1})
        self._cast(game, "A", starter, myrs[0])
        return game, myrs, helper

    @staticmethod
    def _loop_draws(game):
        return [event for event in game.state.events if event.code == "game.draw"
                and "received-damage abilities" in event.summary]

    def test_closed_reciprocal_damage_draws_and_survives_pending_reload(self):
        game, myrs, _ = self._scene(61127042)
        for _ in range(6):
            if game.state.game_over:
                break
            if game.state.mandatory_linked_loop.cursor is not None:
                break
            self.resolve_top(game)
        self.assertEqual(RECEIVED_DAMAGE_LOOP_FAMILY, game.state.mandatory_linked_loop.family)
        game = self.replay(game, load=True)
        for _ in range(6):
            if game.state.game_over:
                break
            self.resolve_top(game)
        self.assertTrue(game.state.game_over)
        self.assertTrue(game.state.draw)
        self.assertEqual(1, len(self._loop_draws(game)))
        self.assertEqual([40] * 4, self._life(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in myrs))
        batches = [event for event in game.state.events if event.code == "effect.damage.fixed_set"]
        self.assertGreaterEqual(len(batches), 2)
        self._record(game, "arcbond-loop-draw")

    def test_damage_exceeding_static_prevention_progresses_to_a_finite_end(self):
        game, _, _ = self._scene(61127043, opening="Shock", life=2)
        self.resolve_top(game)
        self.assertEqual([1] * 4, self._life(game))
        self.assertFalse(game.state.game_over)
        self.assertIsNone(game.state.mandatory_linked_loop.cursor)
        self.resolve_top(game)
        self.assertTrue(game.state.game_over)
        self.assertEqual([0] * 4, self._life(game))
        self.assertEqual([], self._loop_draws(game))
        self._record(game, "arcbond-loop-finite-player-damage")

    def test_one_shot_prevention_consumes_capacity_before_player_damage_progresses(self):
        game, _, _ = self._scene(61127044, protected="ABC", response="Healing Salve")
        for _ in range(3):
            self.resolve_top(game)
            self.assertEqual([40] * 4, self._life(game))
            self.assertIsNone(game.state.mandatory_linked_loop.cursor)
            self.assertFalse(game.state.game_over)
        self.resolve_top(game)
        self.assertEqual([40, 40, 40, 39], self._life(game))
        self.assertFalse(game.state.game_over)
        self.assertEqual([], self._loop_draws(game))
        self._record(game, "arcbond-loop-one-shot-prevention")

    def test_new_counter_trigger_offer_keeps_priority_and_can_end_the_cycle(self):
        game, _, counter = self._scene(61127045, response="Stifle")
        for _ in range(4):
            self.priority(game, "D")
            offers = game.packet("pilot:D", full=True)["decision"]["ctx"]["legal"]["cast"]
            self.assertIn(counter.ref, offers)
            self.assertIsNone(game.state.mandatory_linked_loop.cursor)
            self.resolve_top(game)
            self.assertFalse(game.state.game_over)
        (item,) = self._triggers(game)
        self._cast(game, "D", counter, item.ref)
        self.assertFalse(self._triggers(game))
        self.assertFalse(game.state.game_over)
        self.assertEqual([40] * 4, self._life(game))
        self._record(game, "arcbond-loop-response")

    def test_version_one_position_retains_its_original_run_behavior(self):
        game, _, _ = self._scene(61127046, old=True)
        for _ in range(6):
            self.resolve_top(game)
        self.assertFalse(game.state.game_over)
        self.assertEqual([40] * 4, self._life(game))
        self.assertEqual(1, game.state.mandatory_linked_loop.schema_version)
        self.assertIsNone(game.state.mandatory_linked_loop.cursor)
        self.assertNotIn("family", game.state.mandatory_linked_loop.to_dict())
        self._record(game, "arcbond-loop-legacy-v1")
