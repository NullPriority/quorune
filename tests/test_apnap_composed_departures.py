"""APNAP object choices commit their ordered physical cards together."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_choices.apnap_composition import APNAP_PHYSICAL_ORDER_FIELD, ApnapCompositionOrder
from scripts.build_test_database import build_fixture_database


class ApnapCompositionNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "apnap-composed-departures.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/apnap-composed-departures-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("APNAP departures", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def mutate_bear(self, game, bear, heron):
        for card in (bear, heron):
            self.assert_closed(game, card)
        offer = self.action(game, "A", heron)
        option, = [choice["id"] for choice in offer["cost_options"] if choice["id"].startswith("mutate")]
        result = game.act("pilot:A", {
            "action_id": offer["id"], "cost_option": option, "targets": [bear.ref], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertIsNotNone(game.state.cards[bear.object_id].permanent_composition)

    def act(self, game, seat, action, cards):
        result = game.act(f"pilot:{seat}", {"action_id": action, "cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)
        return game.state.pending_decision

    def assert_rejected(self, game, seat, cards):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", {"action_id": "order", "cards": [card.ref for card in cards]})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_edict_saves_the_resolving_controller_and_the_owners_private_order(self):
        game = self.session(61343681)
        bear = self.add(game.engine, "Grizzly Bears")
        heron = self.add(game.engine, "Dreamtail Heron", zone="hand")
        edict = self.add(game.engine, "Diabolic Edict", seat="B", zone="hand")
        self.assert_closed(game, edict)
        self.prepare(game, mana={"C": 3, "U": 1})
        game.state.players["B"].mana_pool.update({"B": 1, "C": 1})
        self.checkpoint(game)
        self.mutate_bear(game, bear, heron)
        self.cast_for(game, "B", edict, ["A"])
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        order = self.act(game, "A", "choose", (bear,))
        self.assertEqual(("permanent.composition_order", ["A"]), (order.kind, order.actors))
        frame = ApnapCompositionOrder.from_dict(order.continuation[APNAP_PHYSICAL_ORDER_FIELD])
        self.assertEqual("B", frame.continuation.semantic_frame.controller)
        self.assertEqual("B", frame.continuation.effect["actor"])
        changed = deepcopy(frame.to_dict())
        changed["continuation"]["effect"]["actor"] = "A"
        with self.assertRaises(ValueError):
            ApnapCompositionOrder.from_dict(changed)
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assertEqual([], game.state.players["A"].zones["graveyard"])
        self.assert_rejected(game, "B", (heron, bear))
        self.assert_rejected(game, "A", (bear, bear))
        game = self.replay(game, load=True)
        self.act(game, "A", "order", (heron, bear))
        self.assertEqual([heron.object_id, bear.object_id], game.state.players["A"].zones["graveyard"])
        self.assertEqual("graveyard", game.state.cards[edict.object_id].zone)
        sacrifice, = [event for event in game.state.events if event.code == "choice.sacrifice"]
        self.assertEqual("B", sacrifice.actor)
        self.replay(game, load=True)

    def test_two_owners_order_one_simultaneous_mixed_sacrifice_batch(self):
        game = self.session(61343682)
        bear = self.add(game.engine, "Grizzly Bears", ref="apnap-root")
        other = self.add(game.engine, "Grizzly Bears", ref="apnap-ordinary-a")
        heron = self.add(game.engine, "Dreamtail Heron", zone="hand")
        first_b = self.add(game.engine, "Grizzly Bears", seat="B", ref="apnap-ordinary-b")
        second_b = self.add(game.engine, "Grizzly Bears", seat="B", ref="apnap-ordinary-b2")
        barter = self.add(game.engine, "Barter in Blood", zone="hand")
        self.assert_closed(game, barter)
        self.prepare(game, mana={"C": 5, "U": 1, "B": 2})
        self.checkpoint(game)
        self.mutate_bear(game, bear, heron)
        self.cast_for(game, "A", barter)
        self.assertEqual(("choice.apnap", ["A"]), (self.resolve(game).kind, game.state.pending_decision.actors))
        next_pick = self.act(game, "A", "choose", (bear, other))
        self.assertEqual(("choice.apnap", ["B"]), (next_pick.kind, next_pick.actors))
        order_a = self.act(game, "B", "choose", (first_b, second_b))
        self.assertEqual(("permanent.composition_order", ["A"]), (order_a.kind, order_a.actors))
        self.assertEqual({bear.ref, heron.ref, other.ref}, {
            row["id"] for row in order_a.payload_by_actor["A"]["cards"]
        })
        order_b = self.act(game, "A", "order", (heron, other, bear))
        self.assertEqual(("permanent.composition_order", ["B"]), (order_b.kind, order_b.actors))
        for card in (bear, other, first_b, second_b):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        game = self.replay(game, load=True)
        self.act(game, "B", "order", (second_b, first_b))
        self.assertEqual(
            [heron.object_id, other.object_id, bear.object_id, barter.object_id],
            game.state.players["A"].zones["graveyard"],
        )
        self.assertEqual([second_b.object_id, first_b.object_id], game.state.players["B"].zones["graveyard"])
        self.assertEqual(1, sum(event.code == "choice.sacrifice" for event in game.state.events))
        self.replay(game, load=True)

    def test_replacement_order_precedes_exile_order_for_the_resolving_controller(self):
        game = self.session(61343683)
        bear = self.add(game.engine, "Grizzly Bears")
        heron = self.add(game.engine, "Dreamtail Heron", zone="hand")
        edict = self.add(game.engine, "Diabolic Edict", seat="B", zone="hand")
        rest = self.add(game.engine, "Rest in Peace", seat="B")
        leyline = self.add(game.engine, "Leyline of the Void", seat="B")
        for card in (edict, rest, leyline):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1})
        game.state.players["B"].mana_pool.update({"B": 1, "C": 1})
        self.checkpoint(game)
        self.mutate_bear(game, bear, heron)
        self.cast_for(game, "B", edict, ["A"])
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        replacement = self.act(game, "A", "choose", (bear,))
        self.assertEqual(("replacement.order", ["A"]), (replacement.kind, replacement.actors))
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        game = self.replay(game, load=True)
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(2, len(ctx["options"]))
        result = game.act("pilot:A", {"action_id": "choose", "replacement": ctx["options"][0]["id"]})
        self.assertTrue(result.ok, result.summary)
        order = game.state.pending_decision
        self.assertEqual(("permanent.composition_order", ["B"]), (order.kind, order.actors))
        frame = ApnapCompositionOrder.from_dict(order.continuation[APNAP_PHYSICAL_ORDER_FIELD])
        self.assertEqual("exile", frame.request.subjects[0].destination)
        self.assertEqual(1, len(frame.replacement_selections))
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assert_rejected(game, "A", (bear, heron))
        game = self.replay(game, load=True)
        result = game.act("pilot:B", {"action_id": "order", "cards": [heron.ref, bear.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([heron.object_id, bear.object_id], game.state.players["A"].zones["exile"])
        self.assertEqual("B", next(event.actor for event in game.state.events if event.code == "choice.sacrifice"))
        self.replay(game, load=True)
