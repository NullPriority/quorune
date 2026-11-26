from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

from quorune.compiler.cast_trigger_life_payment_templates import cast_trigger_life_payment_body
from quorune.rules.cast_trigger_life_payment import CastTriggerLifePaymentSpec


class CastTriggerLifePaymentCodecTests(unittest.TestCase):
    def test_exact_printed_prices_use_the_current_payers_life_and_close_the_wire(self):
        fixed = cast_trigger_life_payment_body(
            "Any player may pay 5 life. If a player does, counter Dash Hopes.",
            source_name="Dash Hopes",
        )
        fractional = cast_trigger_life_payment_body(
            "Any player may pay half their life, rounded up. If a player does, counter Temporal Extortion.",
            source_name="Temporal Extortion",
        )
        self.assertIsNotNone(fixed)
        self.assertIsNotNone(fractional)
        self.assertEqual(5, fixed.amount(3))
        self.assertEqual([0, 1, 5, 6], [fractional.amount(life) for life in (-1, 1, 10, 11)])
        for spec in (fixed, fractional):
            self.assertEqual(spec, CastTriggerLifePaymentSpec.from_dict(spec.to_dict()))
        for raw in ({"schema_version": True, "price": 5}, {"schema_version": 1, "price": False},
                    {"schema_version": 1, "price": 0}, {"schema_version": 1, "price": 5, "payer": "A"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                CastTriggerLifePaymentSpec.from_dict(raw)

    def test_nearby_payer_quantities_and_different_counter_results_stay_unowned(self):
        for text in (
            "Any opponent may pay 5 life. If a player does, counter Dash Hopes.",
            "Any player may pay half their life, rounded down. If a player does, counter Dash Hopes.",
            "Any player may pay 5 life. If a player does, counter another spell.",
            "Any player may pay 5 life. If a player does, counter Dash Hopes. Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(cast_trigger_life_payment_body(text, source_name="Dash Hopes"))


ORIGINALS = ("Dash Hopes", "Temporal Extortion")


class CastTriggerLifePaymentNativeTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-trigger-life-payment.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/cast-trigger-life-payment-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast trigger payment", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_the_cast_trigger_and_their_other_spell_body(self):
        for name in ORIGINALS:
            with self.subTest(card=name):
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                record = self.db.lookup(name, fuzzy=False)
                self.assertEqual({"spell.cast", "resolve"},
                                 {program.event for program in semantics.programs_for_oracle(record.oracle_id)})
                with patch("quorune.compiler.cast_trigger_life_payment_templates.cast_trigger_life_payment_body",
                           return_value=None):
                    unresolved = compile_oracle_card(record, capability_registry=self.registry,
                                                    capability_profile="commander_review")
                self.assertTrue(unresolved.material_residuals)

    def choose_payment(self, game, actor, cost, choice):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        ctx = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]
        self.assertEqual(cost, ctx["life_cost"])
        action = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        foreign = "A" if actor != "A" else "B"
        self.assertFalse(game.act("pilot:" + foreign, action).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + actor, action)
        self.assertTrue(result.ok, result.summary)

    def test_actual_dash_offers_remaining_seats_after_the_first_payment_counters_it(self):
        game = self.playing(61127242)
        spell = self.add(game.engine, "Dash Hopes", zone="hand")
        target = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        for card in (spell, target):
            self.assert_closed(game, card)
        self.until(game, lambda item: item.state.current_turn is not None
                   and item.state.current_turn.player == "C" and item.state.phase == "precombat_main")
        self.prepare(game, actor="C", active="C")
        game.state.players["C"].life = 4
        game.state.players["A"].mana_pool["B"] = 2
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        self.choose_payment(game, "D", 5, "pay")
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("stack", game.state.cards[target.object_id].zone)
        self.assertEqual((4, 35), (game.state.players["C"].life, game.state.players["D"].life))
        game = self.replay(game, load=True)
        self.choose_payment(game, "A", 5, "decline")
        self.choose_payment(game, "B", 5, "pay")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual((33, 2, 33), tuple(game.state.players[seat].life for seat in "BCD"))
        self.replay(game, load=True)

    def test_actual_temporal_extortion_reads_each_current_life_and_can_leave_the_extra_turn(self):
        for paid in (False, True):
            with self.subTest(paid=paid):
                game = self.playing(61127243 + paid)
                spell = self.add(game.engine, "Temporal Extortion", zone="hand")
                self.assert_closed(game, spell)
                self.prepare(game, actor="A", active="A", mana={"B": 4})
                for seat, life in zip("ABCD", (11, 10, 9, 13), strict=True):
                    game.state.players[seat].life = life
                self.checkpoint(game)
                self.cast_for(game, "A", spell)
                self.choose_payment(game, "A", 6, "decline")
                self.choose_payment(game, "B", 5, "pay" if paid else "decline")
                self.assertEqual("graveyard" if paid else "stack", game.state.cards[spell.object_id].zone)
                game = self.replay(game, load=True)
                self.choose_payment(game, "C", 5, "pay" if paid else "decline")
                self.choose_payment(game, "D", 7, "decline")
                self.assertIsNone(self.resolve(game))
                self.assertEqual((11, 5 if paid else 10, 4 if paid else 9, 13),
                                 tuple(game.state.players[seat].life for seat in "ABCD"))
                self.assertEqual([] if paid else ["A"], [turn.player for turn in game.state.extra_turns])
                self.replay(game, load=True)
