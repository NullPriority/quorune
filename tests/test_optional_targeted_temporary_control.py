"""A mandatory upkeep target and one later optional temporary-control sequence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
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


DOMINUS = "Dominus of Fealty"


class OptionalTargetedTemporaryControlTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-targeted-temporary-control.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/optional-targeted-temporary-control-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional target control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def keywords(self, game, card):
        data = game.engine._effective_card_data(self.current(game, card))
        return {item.casefold() for item in data["keywords"]}

    def scene(self, seed, *, owner="B", with_bounce=False):
        game = self.playing(seed)
        chief = self.add(game.engine, DOMINUS)
        ring = self.add(game.engine, "Sol Ring", seat=owner)
        self.assert_closed(game, chief)
        self.assert_closed(game, ring)
        reply = None
        if with_bounce:
            reply = self.add(game.engine, "Boomerang", seat="B", zone="hand")
            self.assert_closed(game, reply)
            self.add(game.engine, "Island", seat="B", ref="control-response-island-a")
            self.add(game.engine, "Island", seat="B", ref="control-response-island-b")
        self.prepare(game, actor="D", active="D")
        game.state.current_turn = replace(game.state.current_turn, player="D")
        game.state.last_normal_turn_player = "D"
        self.checkpoint(game)
        self.until(game, lambda item: item.state.active_player == "A"
                   and item.state.pending_decision is not None and item.state.pending_decision.kind == "semantic.target")
        self.assertEqual("upkeep", game.state.step)
        self.assertIn("flying", self.keywords(game, chief))
        self.target(game, "A", [ring.ref])
        return game, ring, reply

    def tap_for_mana(self, game, ring, seat):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        act, = (row for row in actions if row["id"].startswith(f"activate:{ring.ref}:"))
        result = game.act("pilot:" + seat, {"action_id": act["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(self.current(game, ring).tapped)

    def choose_apply(self, game, choice, *, try_foreign=False):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual({"apply", "decline"}, set(schema["legal_values"]))
        command = {"action_id": "choose", "choice": choice}
        if try_foreign:
            before = authoritative_state_hash(game.state)
            denied = game.act("pilot:B", command)
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_apply_on_an_already_controlled_permanent_still_untaps_and_grants_haste(self):
        game, ring, _ = self.scene(61127287, owner="A")
        self.tap_for_mana(game, ring, "A")
        game = self.choose_apply(game, "apply", try_foreign=True)
        current = self.current(game, ring)
        self.assertEqual(("A", "A", False), (current.controller, current.owner, current.tapped))
        self.assertIn("haste", self.keywords(game, ring))
        self.replay(game, load=True)

    def test_actual_applying_or_declining_controls_the_original_opposing_permanent_as_chosen(self):
        for seed, choice in ((61127288, "apply"), (61127289, "decline")):
            with self.subTest(choice=choice):
                game, ring, _ = self.scene(seed)
                self.tap_for_mana(game, ring, "B")
                origin = self.current(game, ring)
                identity = (origin.logical_object_id, origin.zone_timestamp)
                game = self.choose_apply(game, choice)
                changed = self.current(game, ring)
                applied = choice == "apply"
                self.assertEqual(("A" if applied else "B", "B", not applied),
                                 (changed.controller, changed.owner, changed.tapped))
                self.assertEqual(identity, (changed.logical_object_id, changed.zone_timestamp))
                self.assertEqual(applied, "haste" in self.keywords(game, ring))
                turn = game.state.turn_sequence
                self.until(game, lambda item: item.state.turn_sequence > turn)
                self.assertEqual("B", self.current(game, ring).controller)
                self.assertNotIn("haste", self.keywords(game, ring))
                self.replay(game, load=True)

    def test_actual_response_makes_the_mandatory_target_illegal_before_any_optional_offer(self):
        game, ring, bounce = self.scene(61127290, with_bounce=True)
        self.cast_for(game, "B", bounce, [ring.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, ring).zone)
        self.assertNotIn("haste", self.keywords(game, ring))
        self.replay(game, load=True)

    def test_complete_pin_uses_one_normal_target_and_one_ordered_optional_offer(self):
        original = self.db.lookup(DOMINUS, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Dominus of Fealty")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        trigger, = (program for program in semantics.programs_for_oracle(original.oracle_id)
                    if program.ability_id.startswith("trigger:"))
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1}, trigger.target_schema)
        offer, = trigger.effects
        self.assertEqual("offer_optional_effect", offer["op"])
        self.assertEqual(("gain_control", "untap", "grant_keyword_until_end_of_turn"),
                         tuple(effect["op"] for effect in offer["effects"]))
        modified = replace(original, oracle_text=original.oracle_text.replace("untap it", "tap it"))
        self.assertNotEqual("exact", compile_oracle_card(
            modified, capability_registry=self.registry, capability_profile="commander_review",
        ).status)
