from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.designation_prevention import DESIGNATION_INFORMATION_FIELD
from quorune.entry_designations import CHOSEN_COLOR_TARGET_PREDICATE
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.chosen_color_target_shapes import (
    CHOSEN_COLOR_TARGET_CAPABILITY, TARGET_SCHEMA, chosen_color_target_runtime_dependencies,
)
from quorune.selection import targeting
from scripts.build_test_database import build_fixture_database


class SourceChosenColorTargetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-color-target.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-chosen-color-target-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Chosen-color destruction", [DeckEntry(commander, 1, "commander"),
                                                               DeckEntry("Plains", 30)], [commander])

    def choose_color(self, game, color):
        decision = self.resolve(game)
        self.assertEqual("replacement.order", decision.kind)
        packet = game.packet("pilot:A", full=True)["decision"]
        choice = next(row["id"] for row in packet["ctx"]["options"] if row["label"].endswith("choose color " + color))
        result = game.act("pilot:A", {"action_id": "choose", "choices": {"replacement": choice}})
        self.assertTrue(result.ok, result.summary)

    def board(self, seed, *, multicolor=False, response_name=None):
        game = self.session(seed)
        paladin = self.add(game.engine, "Pentarch Paladin", zone="hand")
        self.assert_closed(game, paladin)
        haste = self.add(game.engine, "Fervor")
        self.assert_closed(game, haste)
        victim = self.add(game.engine, "Boros Swiftblade" if multicolor else "Hill Giant", seat="C")
        self.assert_closed(game, victim)
        green = self.add(game.engine, "Grizzly Bears", seat="C")
        land = self.add(game.engine, "Plains", seat="C", ref="colorless-land")
        response = self.add(game.engine, response_name, seat="B", zone="hand") if response_name else None
        if response is not None:
            self.assert_closed(game, response)
        self.prepare(game, mana={"W": 7})
        game.state.players["B"].mana_pool.update({"C": 2, "W": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", paladin)
        self.choose_color(game, "R")
        self.priority(game, "A")
        current = game.state.cards[paladin.object_id]
        self.assertEqual("R", current.annotations["chosen_color"])
        return game, current, victim, green, land, response

    def offer(self, game, source):
        packet = game.packet("pilot:A", full=True)["decision"]
        actions = packet["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"].startswith("activate:" + source.ref + ":")), None)

    def use(self, game, source, target):
        result = game.act("pilot:A", {"action_id": self.offer(game, source)["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_printed_choice_limits_offer_and_submission_and_destroys_multicolor(self):
        game, paladin, victim, green, land, _ = self.board(607020401, multicolor=True)
        offer = self.offer(game, paladin)
        self.assertIsNotNone(offer)
        legal = offer["target_schema"]["legal_refs"]
        self.assertIn(victim.ref, legal)
        self.assertNotIn(green.ref, legal, "the chosen color limits the offer")
        self.assertNotIn(land.ref, legal)
        before = authoritative_state_hash(game.state)
        for actor, target in (("A", green), ("A", land), ("B", victim)):
            result = game.act("pilot:" + actor, {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.use(game, paladin, victim)
        frame = game.state.stack[-1].context[DESIGNATION_INFORMATION_FIELD]
        self.assertEqual({"color": "R"}, frame["values"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
        self.replay(game, load=True)

    def test_returned_source_cannot_change_the_original_designation_after_replay(self):
        game, paladin, victim, _, _, blink = self.board(607020402, response_name="Flicker of Fate")
        original = paladin.logical_object_id
        self.use(game, paladin, victim)
        self.cast_for(game, "B", blink, [paladin.ref])
        self.choose_color(game, "G")
        current = game.state.cards[paladin.object_id]
        self.assertNotEqual(original, current.logical_object_id)
        self.assertEqual("G", current.annotations["chosen_color"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[victim.object_id].zone)
        self.replay(game, load=True)

    def test_recolored_target_fails_revalidation_and_remains_on_the_battlefield(self):
        game, paladin, victim, _, _, recolor = self.board(607020403, response_name="Moonlace")
        self.use(game, paladin, victim)
        self.cast_for(game, "B", recolor, [victim.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[victim.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertFalse(game.engine._effective_card_data(current)["colors"])
        self.replay(game, load=True)

    def test_already_present_copy_has_no_choice_and_cannot_offer_the_activation(self):
        game = self.session(607020404)
        paladin = self.add(game.engine, "Pentarch Paladin", zone="hand")
        copy = self.add(game.engine, "Mirage Mirror")
        haste = self.add(game.engine, "Fervor")
        target = self.add(game.engine, "Hill Giant", seat="C")
        for card in (paladin, copy, haste, target):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 10})
        self.checkpoint(game)
        self.cast_for(game, "A", paladin)
        self.choose_color(game, "R")
        self.priority(game, "A")
        self.use(game, copy, paladin)
        self.assertIsNone(self.resolve(game))
        model = game.state.cards[copy.object_id]
        self.assertEqual("Pentarch Paladin", game.engine.display_name(model.object_id))
        self.assertNotIn("chosen_color", model.annotations)
        self.priority(game, "A")
        self.assertIsNone(self.offer(game, model))
        self.replay(game, load=True)

    def test_copy_cannot_reuse_a_color_from_an_unrelated_entry_ability(self):
        game = self.session(607020405)
        paladin = self.add(game.engine, "Pentarch Paladin", zone="hand")
        knight = self.add(game.engine, "Diamond Knight", zone="hand")
        second = self.add(game.engine, "Pentarch Paladin", zone="hand", ref="another-paladin")
        weave = self.add(game.engine, "Mirrorweave", zone="hand")
        haste = self.add(game.engine, "Fervor")
        target = self.add(game.engine, "Hill Giant", seat="C")
        for card in (paladin, knight, second, weave, haste, target):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 23})
        self.checkpoint(game)
        for card in (paladin, knight, second):
            self.cast_for(game, "A", card)
            self.choose_color(game, "R")
            self.priority(game, "A")
        self.cast_for(game, "A", weave, [paladin.ref])
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        for original in (knight, second):
            copy = game.state.cards[original.object_id]
            self.assertEqual("Pentarch Paladin", game.engine.display_name(copy.object_id))
            self.assertEqual("R", copy.annotations["chosen_color"])
            self.assertIsNone(self.offer(game, copy), "unrelated color cannot authorize this activation")
        self.replay(game, load=True)

    def test_copy_as_it_enters_gets_its_own_linked_choice(self):
        game = self.session(607020406)
        paladin = self.add(game.engine, "Pentarch Paladin", zone="hand")
        clone = self.add(game.engine, "Clone", zone="hand")
        haste = self.add(game.engine, "Fervor")
        red = self.add(game.engine, "Hill Giant", seat="C")
        green = self.add(game.engine, "Grizzly Bears", seat="C")
        for card in (paladin, clone, haste):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 12, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", paladin)
        self.choose_color(game, "R")
        self.cast_for(game, "A", clone)
        decision = self.resolve(game)
        self.assertEqual("replacement.order", decision.kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option, = [item["id"] for item in options if item["id"].endswith(":" + paladin.ref) and not item.get("decline")]
        result = game.act("pilot:A", {"action_id": "choose", "replacement": option})
        self.assertTrue(result.ok, result.summary)
        self.choose_color(game, "G")
        game = self.replay(game, load=True)
        current = game.state.cards[clone.object_id]
        self.assertEqual("Pentarch Paladin", game.engine.display_name(current.object_id))
        self.assertEqual("G", current.annotations["chosen_color"])
        self.priority(game, "A")
        offer = self.offer(game, current)
        self.assertIsNotNone(offer)
        self.assertNotIn(red.ref, offer["target_schema"]["legal_refs"])
        self.assertIn(green.ref, offer["target_schema"]["legal_refs"])
        self.use(game, current, green)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[green.object_id].zone)
        self.replay(game, load=True)

    def test_raw_target_requires_a_complete_chosen_color_destruction(self):
        valid = dict(event="activate", active_zone="battlefield", target_schema=TARGET_SCHEMA,
                     effects=({"op": "destroy", "card": "$target.0"},))
        self.assertIn(CHOSEN_COLOR_TARGET_CAPABILITY,
                      chosen_color_target_runtime_dependencies(SimpleNamespace(**valid)))
        for changes in ({"event": "resolve"}, {"active_zone": "graveyard"},
                        {"target_schema": {**TARGET_SCHEMA, "count": True}},
                        {"effects": ({"op": "destroy", "card": "$source"},)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                chosen_color_target_runtime_dependencies(SimpleNamespace(**{**valid, **changes}))

    def test_ignoring_the_color_is_rejected_by_the_printed_offer(self):
        original = targeting.target_predicate_matches

        def omit(host, group, row, **kwargs):
            if group.predicate == CHOSEN_COLOR_TARGET_PREDICATE:
                return True
            return original(host, group, row, **kwargs)

        with patch.object(targeting, "target_predicate_matches", side_effect=omit):
            with self.assertRaisesRegex(AssertionError, "the chosen color limits the offer"):
                self.test_printed_choice_limits_offer_and_submission_and_destroys_multicolor()
