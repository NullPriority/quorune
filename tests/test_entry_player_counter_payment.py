
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class EntryPlayerCounterPaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-player-counter-payment.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-player-counter-payment-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Land entry counter payment",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def play_land(self, game, seat, card):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["action"] == "play_land" and row.get("card") == card.ref]
        result = game.act(f"pilot:{seat}", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)

    def choice(self, game, seat, refs):
        self.assertEqual([f"pilot:{seat}"], game.pending_principals())
        self.assertEqual([], list(game.state.stack[-1].targets))
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal_actions"]
        schema = actions[0]["choice_schema"]
        self.assertEqual(set(refs), set(schema["legal_refs"]))
        self.assertEqual((0, 1), (schema["minimum"], schema["maximum"]))

    def test_actual_thelon_lets_the_swamp_player_pay_one_counter_and_replays_the_choice(self):
        game = self.session(6020031)
        source = self.add(game.engine, "Thelon's Chant", seat="A")
        swamp = self.add(game.engine, "Swamp", seat="B", zone="hand")
        payer = self.add(game.engine, "Grizzly Bears", seat="B", ref="payer-creature")
        rival = self.add(game.engine, "Grizzly Bears", seat="C", ref="other-creature")
        self.assert_closed(game, source)
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        self.play_land(game, "B", swamp)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.choice(game, "B", [payer.ref])
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        before = authoritative_state_hash(game.state)
        for seat, refs in (("A", [payer.ref]), ("B", [rival.ref]), ("B", [payer.ref, payer.ref])):
            result = game.act(f"pilot:{seat}", {"action_id": "choose", "cards": refs})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choice(game, "B", [payer.ref])
        result = game.act("pilot:B", {"action_id": "choose", "cards": [payer.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.cards[payer.object_id].counters.get("-1/-1"))
        self.assertEqual(0, game.state.cards[rival.object_id].counters.get("-1/-1", 0))
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_flicker_leaves_the_third_seat_as_payer_when_another_seat_controls_the_land(self):
        game = self.session(6020032)
        source = self.add(game.engine, "Thelon's Chant", seat="A")
        flicker = self.add(game.engine, "Flicker of Fate", seat="C", zone="hand")
        arbor = self.add(game.engine, "Dryad Arbor", seat="B")
        urborg = self.add(game.engine, "Urborg, Tomb of Yawgmoth", seat="D")
        payer = self.add(game.engine, "Grizzly Bears", seat="C")
        for card in (source, flicker, arbor, urborg):
            self.assert_closed(game, card)
        self.prepare(game, active="A", actor="C", mana={"W": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "C", flicker, [arbor.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[arbor.object_id].controller)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.choice(game, "C", [payer.ref])
        game = self.replay(game, load=True)
        result = game.act("pilot:C", {"action_id": "choose", "cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, game.state.cards[payer.object_id].counters.get("-1/-1", 0))
        self.assertEqual([40, 40, 37, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_tourach_deals_fixed_damage_when_the_forest_player_cannot_pay(self):
        for prevention in (False, True):
            with self.subTest(counter_prohibition=prevention):
                game = self.session(6020033 + prevention)
                source = self.add(game.engine, "Tourach's Chant", seat="A")
                other_land = self.add(game.engine, "Swamp", seat="B", zone="hand")
                forest = self.add(game.engine, "Forest", seat="B", zone="hand")
                self.add(game.engine, "Grizzly Bears", seat="C", ref="forest-rival")
                if prevention:
                    self.add(game.engine, "Grizzly Bears", seat="B", ref="prohibited-payer")
                    solemnity = self.add(game.engine, "Solemnity", seat="D")
                    self.assert_closed(game, solemnity)
                self.assert_closed(game, source)
                self.prepare(game, active="B", actor="B")
                self.checkpoint(game)
                self.play_land(game, "B", forest)
                self.assertIsNone(self.resolve(game))
                self.assertEqual([40, 37, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
                self.assertEqual("hand", game.state.cards[other_land.object_id].zone)
                self.replay(game, load=True)

    def test_actual_tourach_ignores_swamp_entry(self):
        game = self.session(6020035)
        source = self.add(game.engine, "Tourach's Chant")
        swamp = self.add(game.engine, "Swamp", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        self.play_land(game, "B", swamp)
        self.assertFalse(game.state.stack)
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_payment_descriptor_rejects_other_actors_and_requires_the_land_occurrence(self):
        from types import SimpleNamespace
        from quorune.rules.entry_player_counter_payment import (
            bind_entry_player_counter_choice, entry_player_counter_payment_effect,
            is_entry_player_counter_payment,
        )

        complete = entry_player_counter_payment_effect()
        self.assertTrue(is_entry_player_counter_payment(complete))
        for actor in ("$controller", "$context.controller", "C"):
            self.assertFalse(is_entry_player_counter_payment({**complete, "player": actor}))
        for context in (
            {"event": "land.enter", "putting_player": "B"},
            {"event": "land.play", "putting_player": "B", "card_object_identity": "entrant:1"},
            {"event": "land.enter", "putting_player": "C", "card_object_identity": "entrant:1"},
        ):
            with self.subTest(context=context), self.assertRaises(ValueError):
                bind_entry_player_counter_choice(SimpleNamespace(context=context), {"player": "B",
                    "entry_player_counter_payment_version": 1})

    def test_substituting_the_land_controller_breaks_the_actual_flicker_result(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune import zone_trigger_events

        normalize = zone_trigger_events._normalized_battlefield_entry_events

        def with_controller(occurrence, common):
            return normalize(replace(occurrence, putting_player=occurrence.current_controller), common)

        with patch.object(zone_trigger_events, "_normalized_battlefield_entry_events", side_effect=with_controller):
            with self.assertRaisesRegex(AssertionError, r"pilot:C.*!=.*pilot:B"):
                self.test_actual_flicker_leaves_the_third_seat_as_payer_when_another_seat_controls_the_land()

    def test_actual_automatic_exile_return_and_a_players_return_instruction_are_distinct(self):
        for index, returner in enumerate(("Glass Casket", "Journey to Nowhere")):
            with self.subTest(returner=returner):
                game = self.session(6020038 + index)
                source = self.add(game.engine, "Thelon's Chant")
                exile = self.add(game.engine, returner, zone="hand")
                disenchant = self.add(game.engine, "Disenchant", seat="C", zone="hand")
                arbor = self.add(game.engine, "Dryad Arbor", seat="B")
                urborg = self.add(game.engine, "Urborg, Tomb of Yawgmoth", seat="D")
                for card in (source, exile, disenchant, arbor, urborg):
                    self.assert_closed(game, card)
                game.state.players["C"].mana_pool.update({"W": 1, "C": 1})
                self.prepare(game, mana={"W": 1, "C": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", exile)
                self.resolve_top(game)
                self.assertEqual("semantic.target", game.state.pending_decision.kind)
                result = game.act("pilot:A", {"action_id": "choose", "targets": [arbor.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile", game.state.cards[arbor.object_id].zone)
                self.cast_for(game, "C", disenchant, [exile.ref])
                self.assertIsNone(self.resolve(game))
                arrived = game.state.cards[arbor.object_id]
                self.assertEqual(("battlefield", "B"), (arrived.zone, arrived.controller))
                self.assertEqual([40 if index == 0 else 37, 40, 40, 40],
                                 [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)
