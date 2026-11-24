from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_vanishing_rules as upkeep_witness
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.effect_runtime import player_life_exchange as runtime
from quorune.life_gain_permission import LifeGainPermissionSnapshot
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.player_life_exchange import (
    PLAYER_LIFE_EXCHANGE_CAPABILITY, PlayerLifeExchangeSpec, plan_player_life_exchange,
    player_life_exchange_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database


class PlayerLifeExchangeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "player-life-exchange.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/player-life-exchange-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Two-player life exchange", [DeckEntry(commander, 1, "commander"),
                                                               DeckEntry("Plains", 30)], [commander])

    def life(self, game):
        return tuple(game.state.players[seat].life for seat in "ABCD")

    def board(self, seed, name):
        game = self.session(seed)
        card = self.add(game.engine, name)
        self.assert_closed(game, card)
        for seat, value in zip("ABCD", (26, 10, 23, 40), strict=True):
            game.state.players[seat].life = value
        return game, card

    def offer(self, game, card):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"].startswith("activate:" + card.ref + ":")), None)

    def activate(self, game, card, targets):
        offered = self.offer(game, card)
        self.assertIsNotNone(offered)
        command = {"action_id": offered["id"], "targets": list(targets), "pay": "auto"}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def upkeep(self, game, seat):
        upkeep_witness.VanishingRuntimeTests.upkeep(self, game, seat=seat)

    def test_actual_conduit_only_swaps_its_two_distinct_revalidated_players(self):
        game, conduit = self.board(701120801, "Soul Conduit")
        self.prepare(game, mana={"C": 6})
        self.checkpoint(game)
        before = authoritative_state_hash(game.state)
        offer = self.offer(game, conduit)
        self.assertIsNotNone(offer)
        for actor, targets in (("A", ["B", "B"]), ("A", ["B"]), ("C", ["B", "C"])):
            rejected = game.act("pilot:" + actor, {"action_id": offer["id"], "targets": targets, "pay": "auto"})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.activate(game, conduit, ["B", "C"])
        self.assertTrue(game.state.cards[conduit.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((26, 23, 10, 40), self.life(game))
        self.replay(game, load=True)

    def test_actual_conduit_cancels_both_requests_for_an_unavailable_gain(self):
        game, conduit = self.board(701120802, "Soul Conduit")
        prohibition = self.add(game.engine, "Giant Cindermaw", seat="D")
        self.assert_closed(game, prohibition)
        self.prepare(game, mana={"C": 6})
        self.checkpoint(game)
        expected = self.life(game)
        self.activate(game, conduit, ["B", "C"])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[conduit.object_id].tapped)
        self.assertEqual(expected, self.life(game), "the entire exchange must be canceled")
        self.replay(game, load=True)

    def test_actual_conduit_does_not_exchange_after_one_target_departs(self):
        game, conduit = self.board(701120803, "Soul Conduit")
        self.prepare(game, mana={"C": 6})
        self.checkpoint(game)
        expected = self.life(game)
        self.activate(game, conduit, ["B", "C"])
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(expected, self.life(game))
        self.replay(game, load=True)

    def test_actual_mirror_pair_requires_own_upkeep_and_keeps_sacrifice_paid(self):
        for offset, name in enumerate(("Mirror Universe", "Magus of the Mirror")):
            with self.subTest(name=name):
                game, mirror = self.board(701120804 + offset, name)
                self.prepare(game)
                self.assertIsNone(self.offer(game, mirror))
                self.upkeep(game, "B")
                self.priority(game, "A")
                self.assertIsNone(self.offer(game, mirror))
                self.upkeep(game, "A")
                self.priority(game, "A")
                self.checkpoint(game)
                self.activate(game, mirror, ["B"])
                self.assertEqual("graveyard", game.state.cards[mirror.object_id].zone)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((10, 26, 23, 40), self.life(game))
                self.replay(game, load=True)

    def test_actual_axis_chooses_targets_then_may_decline_or_exchange_with_replacements(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                game, axis = self.board(701120806 + apply, "Axis of Mortality")
                for index in range(2):
                    doubler = self.add(game.engine, "Boon Reflection", seat="B", ref=f"axis-reflection-{index}")
                    self.assert_closed(game, doubler)
                self.prepare(game)
                self.upkeep(game, "A")
                self.checkpoint(game)
                choice = self.resolve(game)
                self.assertEqual(("semantic.target", ["A"]), (choice.kind, choice.actors))
                self.target(game, "A", ["B", "C"])
                choice = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
                result = game.act("pilot:A", {"action_id": "choose", "choice": "apply" if apply else "decline"})
                self.assertTrue(result.ok, result.summary)
                if apply:
                    choice = self.resolve(game)
                    self.assertEqual(("replacement.order", ["B"]), (choice.kind, choice.actors))
                    self.assertEqual((26, 10, 23, 40), self.life(game))
                    game = self.replay(game, load=True)
                    option = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"][0]["id"]
                    response = {"action_id": "choose", "replacement": option}
                    before = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:A", response).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    result = game.act("pilot:B", response)
                    self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((26, 62, 10, 40) if apply else (26, 10, 23, 40), self.life(game))
                self.replay(game, load=True)

    def test_shape_requires_the_frozen_players_and_no_life_event_on_equal_totals(self):
        spec = PlayerLifeExchangeSpec("two_targets")
        valid = dict(event="activate", active_zone="battlefield", effects=(spec.to_instruction(),),
                     handlers=(), event_condition=None, target_schema=spec.to_target_schema())
        self.assertIn(PLAYER_LIFE_EXCHANGE_CAPABILITY,
                      player_life_exchange_runtime_dependencies(SimpleNamespace(**valid)))
        for changes in (
            {"target_schema": {**spec.to_target_schema(), "count": 1}},
            {"effects": ({**spec.to_instruction(), "players": ["$target.0", "$controller"]},)},
            {"event": "step.begin"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                player_life_exchange_runtime_dependencies(SimpleNamespace(**{**valid, **changes}))
        self.assertEqual((), plan_player_life_exchange("B", "C", {"B": 7, "C": 7},
                                                     LifeGainPermissionSnapshot(("B",))))

    def test_half_exchange_mutation_is_detected_on_the_printed_conduit(self):
        def ignore_gain_permission(first, second, lives, permission):
            return plan_player_life_exchange(first, second, lives, LifeGainPermissionSnapshot())

        with patch.object(runtime, "plan_player_life_exchange", side_effect=ignore_gain_permission):
            with self.assertRaisesRegex(AssertionError, "the entire exchange must be canceled"):
                self.test_actual_conduit_cancels_both_requests_for_an_unavailable_gain()
