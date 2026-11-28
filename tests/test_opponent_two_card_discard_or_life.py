"""Actual Court of Ambition choices use full private payments and the current title."""

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
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_monarch_movement_replacements as movement_fixture


CAPABILITY = "choice.apnap.optional_two_card_discard_or_life_loss"
PROFILE = "commander_review"


class OpponentTwoCardDiscardOrLifeTests(InheritedNativeFixture, unittest.TestCase):
    throne = movement_fixture.MonarchMovementReplacementTests.throne
    upkeep = movement_fixture.MonarchMovementReplacementTests.upkeep
    transfer = movement_fixture.MonarchMovementReplacementTests.transfer
    reject = movement_fixture.MonarchMovementReplacementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-two-card-discard.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-two-card-discard-or-life-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Court of Ambition", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def prepare_upkeep(self, *, changed, redirected):
        game = self.session(76315010 + changed)
        source = self.add(game.engine, "Court of Ambition")
        throne = self.throne(game) if changed else None
        if redirected:
            replacement = self.add(game.engine, "Rest in Peace")
            self.assert_closed(game, replacement)
        hands = {}
        for seat, count in (("B", 1), ("C", 2), ("D", 2)):
            existing = tuple(game.state.players[seat].zones["hand"])
            for object_id in existing:
                game.engine.move_card(object_id, "library", reason="Prepare the printed payment sizes")
            hands[seat] = tuple(self.add(game.engine, "Plains", seat=seat, zone="hand",
                                         ref=f"private-{seat}-{index}") for index in range(count))
        self.upkeep(game, source, throne)
        self.assertEqual(1, self.trigger_count(game, source))
        if changed:
            self.transfer(game, throne)
        pending = self.resolve(game)
        self.assertEqual("choice.apnap", pending.kind)
        return game, hands

    def payment(self, game, seat, cards):
        self.assertEqual(["pilot:" + seat], game.pending_principals())
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)

    def choice(self, game, seat):
        self.assertEqual(["pilot:" + seat], game.pending_principals())
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]

    def test_actual_monarch_requires_both_cards_and_keeps_them_private_until_discard(self):
        game, hands = self.prepare_upkeep(changed=False, redirected=True)
        before = tuple(game.state.players[seat].life for seat in "ABCD")
        offer = self.choice(game, "C")
        self.assertEqual((2, 6), (offer["count"], offer["declined_life_loss"]))
        action, = game.packet("pilot:C", full=True)["decision"]["legal_actions"]
        field, = action["form"]["fields"]
        self.assertEqual([0, 2], field["allowed_cardinalities"])
        self.assertTrue(field["distinct"])
        self.assertEqual({"B": False}, offer["prior_payment_choices"])
        first, second = hands["C"]
        self.reject(game, "B", {"action_id": "choose", "cards": []})
        for refs in ([first.ref], [first.ref, first.ref], [first.ref, hands["D"][0].ref]):
            self.reject(game, "C", {"action_id": "choose", "cards": refs})
        self.payment(game, "C", (first, second))
        offer = self.choice(game, "D")
        self.assertEqual({"B": False, "C": True}, offer["prior_payment_choices"])
        self.assertEqual({}, offer["prior_public_choices"])
        for card in hands["C"]:
            self.assertNotIn(card.ref, json.dumps(offer))
            self.assertEqual("hand", game.state.cards[card.object_id].zone)
        self.assertEqual(before, tuple(game.state.players[seat].life for seat in "ABCD"))
        game = self.replay(game, load=True)
        self.payment(game, "D", ())
        self.assertIsNone(self.resolve(game))
        self.assertEqual([before[0], before[1]-6, before[2], before[3]-6],
                         [game.state.players[seat].life for seat in "ABCD"])
        for card in hands["C"]:
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        self.assertEqual("hand", game.state.cards[hands["B"][0].object_id].zone)
        discard, = (event for event in game.state.events if event.code == "choice.discard")
        loss, = (event for event in game.state.events if event.code == "effect.life"
                 and event.details.get("players") == ["B", "D"])
        self.assertLess(discard.event_id, loss.event_id)
        self.replay(game, load=True)

    def test_actual_throne_response_changes_the_entire_payment_before_choices(self):
        game, hands = self.prepare_upkeep(changed=True, redirected=False)
        before = tuple(game.state.players[seat].life for seat in "ABCD")
        offer = self.choice(game, "B")
        self.assertEqual((1, 3), (offer["count"], offer["declined_life_loss"]))
        self.payment(game, "B", hands["B"])
        self.assertEqual({"B": True}, self.choice(game, "C")["prior_payment_choices"])
        self.payment(game, "C", ())
        self.assertEqual({"B": True, "C": False}, self.choice(game, "D")["prior_payment_choices"])
        self.payment(game, "D", hands["D"][:1])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([before[0], before[1], before[2]-3, before[3]],
                         [game.state.players[seat].life for seat in "ABCD"])
        for card in (hands["B"][0], hands["D"][0]):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.assertEqual("hand", game.state.cards[hands["D"][1].object_id].zone)
        self.replay(game, load=True)

    def test_the_full_original_requires_independent_two_card_payment_authority(self):
        report, semantics = register_card(self.db, self.registry, "Court of Ambition")
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = semantics.programs_for_oracle(report["oracle_id"])
        self.assertEqual(2, len(programs))
        program, = (row for row in programs if CAPABILITY in row.capability_dependencies)
        normal, alternate = program.effects[0]["otherwise_effects"][0], program.effects[0]["effects"][0]
        self.assertEqual((1, 3), (normal["count"], normal["declined_life_loss"]))
        self.assertEqual((2, 6), (alternate["count"], alternate["declined_life_loss"]))
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
        absent = replace(program, capability_dependencies=dependencies,
                         capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", binding["blockers"])
        card = self.db.lookup("Court of Ambition", fuzzy=False)
        for old, new in (("two cards", "three cards"), ("two cards", "two nonland cards"),
                         ("two cards.", "two cards. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
