"""Native original caster payment, effect origin and unrepresented variants."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_caster_discard_payment import CASTER_DISCARD_PAYMENT_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class OpponentCasterDiscardOrLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-caster-discard.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-caster-discard-or-life-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Painful Quandary", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def cast_original(self, seed, *, caster="B", payment=None, redirected=False):
        game = self.session(seed)
        source = self.add(game.engine, "Painful Quandary")
        for object_id in tuple(game.state.players[caster].zones["hand"]):
            game.engine.move_card(object_id, "library", reason="Prepare the caster's private payment")
        spell = self.add(game.engine, "Unsummon", seat=caster, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", ref="original-spell-target")
        self.add(game.engine, "Island", seat=caster)
        selected = self.add(game.engine, payment, seat=caster, zone="hand", ref="private-payment") if payment else None
        if redirected:
            redirect = self.add(game.engine, "Rest in Peace")
            self.assert_closed(game, redirect)
        for card in (source, spell, selected):
            if card is not None:
                self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=caster, active=caster)
        self.checkpoint(game)
        self.cast(game, caster, spell, [target.ref])
        self.assertEqual(0 if caster == "A" else 1, self.trigger_count(game, source))
        self.assertEqual(["spell"] if caster == "A" else ["spell", "triggered_ability"],
                         [item.kind for item in game.state.stack])
        return game, target, selected

    def test_actual_optional_discard_keeps_opposing_origin_and_triggers_guerrilla_tactics(self):
        game, target, payment = self.cast_original(76315101, payment="Guerrilla Tactics", redirected=True)
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        before_b = game.state.players["B"].life
        before_c = game.state.players["C"].life
        offer = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual(([payment.ref], 5), (offer["options"], offer["declined_life_loss"]))
        for seat in "ACD":
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(payment.ref, json.dumps(packet))
        initial = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "cards": [payment.ref]}
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", game.state.cards[payment.object_id].zone)
        self.assertEqual(before_b, game.state.players["B"].life)
        self.target(game, "B", ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before_c - 4, game.state.players["C"].life)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_decline_empty_hand_departure_and_own_cast_keep_the_original_caster(self):
        for mode in ("decline", "empty", "departure", "own"):
            with self.subTest(mode=mode):
                game, target, remaining = self.cast_original(
                    76315120 + len(mode), caster="A" if mode == "own" else "B",
                    payment="Plains" if mode in {"decline", "departure"} else None,
                )
                life = game.state.players["B"].life
                if mode == "departure":
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertFalse(game.state.players["B"].in_game)
                pending = self.resolve(game)
                if mode == "decline":
                    self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
                    result = game.act("pilot:B", {"action_id": "choose", "cards": []})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("hand", game.state.cards[remaining.object_id].zone)
                else:
                    self.assertIsNone(pending)
                self.assertEqual(life - 5 if mode in {"decline", "empty"} else life, game.state.players["B"].life)
                self.assertEqual("battlefield" if mode == "departure" else "hand", game.state.cards[target.object_id].zone)
                self.replay(game, load=True)

    def test_complete_original_requires_the_sealed_cast_and_its_independent_authority(self):
        report, semantics = register_card(self.db, self.registry, "Painful Quandary")
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(report["oracle_id"])
        self.assertIn(CASTER_DISCARD_PAYMENT_CAPABILITY, program.capability_dependencies)
        deps = tuple(cap for cap in program.capability_dependencies if cap != CASTER_DISCARD_PAYMENT_CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CASTER_DISCARD_PAYMENT_CAPABILITY}", bound["blockers"])
        card = self.db.lookup("Painful Quandary", fuzzy=False)
        for old, new in (("an opponent casts", "a player casts"), ("discard a card", "exile a card"),
                         ("discard a card.", "discard a card. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
