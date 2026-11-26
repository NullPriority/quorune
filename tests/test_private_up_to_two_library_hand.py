"""Original domain and activated-X inspection permit a private hand subset."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.query_effect_amount_model import CastXAmountSpec, PublicQueryAmountSpec
from quorune.record import authoritative_state_hash
from quorune.resolution_conditions import CONTROLLER_BASIC_LAND_TYPES
from quorune.rules.capabilities import load_default_capability_registry
from quorune.variable_library_selection_model import (
    VARIABLE_UP_TO_TWO_HAND_SELECTION_VERSION, validate_variable_library_selection,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SHADOW = "Shadow Prophecy"
RIG = "The Mana Rig"


class PrivateUpToTwoLibraryHandTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-up-to-two-library-hand.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/private-up-to-two-library-hand-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private top cards into hand", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_the_count_remainder_and_independent_siblings(self):
        for name, event, ordered in (
            (SHADOW, "resolve", ("variable_library_selection", "lose_life")),
            (RIG, "activate", ("variable_library_selection",)),
        ):
            with self.subTest(card=name):
                bound, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                rows = semantics.programs_for_oracle(card.oracle_id)
                program, = (row for row in rows if row.event == event)
                self.assertEqual(ordered, tuple(effect["op"] for effect in program.effects))
                self.assertIsNone(program.target_schema)
                selection = program.effects[0]
                validate_variable_library_selection(selection)
                self.assertEqual(VARIABLE_UP_TO_TWO_HAND_SELECTION_VERSION, selection["schema_version"])
                self.assertEqual((0, 2, False, False), (selection["minimum"], selection["maximum"],
                                                         selection["public_reveal"], selection["selected_reveal"]))
                if name == SHADOW:
                    amount = PublicQueryAmountSpec.from_dict(selection["look_count"])
                    self.assertEqual(CONTROLLER_BASIC_LAND_TYPES, amount.quantity)
                    self.assertEqual(("graveyard", "chosen"), (selection["remainder_destination"], selection["remainder_order"]))
                else:
                    self.assertEqual(CastXAmountSpec(), CastXAmountSpec.from_dict(selection["look_count"]))
                    self.assertEqual(("library_bottom", "random"), (selection["remainder_destination"], selection["remainder_order"]))
                    token, = (row for row in rows if row.event == "spell.cast")
                    self.assertTrue(token.effects[0]["tapped"])
                for change in ({"maximum": 3}, {"selected_reveal": True}, {"selection_policy": "fixed_any"},
                               {"remainder_order": "random" if name == SHADOW else "chosen"}):
                    with self.assertRaises(ValueError):
                        validate_variable_library_selection({**selection, **change})

    def add_top(self, game):
        return tuple(self.add(game.engine, name, zone="library", ref=f"private-hand-look-{index}")
                     for index, name in enumerate(("Plains", "Island", "Mountain", "Swamp", "Giant Growth")))

    def inspection(self, game, count):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("fixed_library_selection", context["operation"])
        objects = [value["id"] for value in context["objects"]]
        lib = game.state.players["A"].zones["library"]
        self.assertEqual([game.state.cards[oid].ref for oid in reversed(lib[-count:])], objects)
        for seat in ("B", "C", "D"):
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(ref not in json.dumps(packet) for ref in objects))
        return context, objects

    def reject(self, game, seat, fields):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", **fields})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose(self, game, cards):
        result = game.act("pilot:A", {"action_id": "choose", "cards": cards})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_shadow_moves_the_private_subset_and_ordered_public_remainder_before_losing_life(self):
        game = self.session(40430101)
        spell = self.add(game.engine, SHADOW, zone="hand")
        for name in ("Tropical Island", "Mountain", "Swamp"):
            self.add(game.engine, name)
        self.add(game.engine, "Plains", seat="B")
        top = self.add_top(game)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.cast_for(game, "A", spell)
        context, refs = self.inspection(game, 4)
        self.assertIn("order the rest into your graveyard", context["prompt"].casefold())
        self.assertEqual(life, game.state.players["A"].life)
        selected = (top[4], top[2])
        remainder = (top[1], top[3])
        cards = {"hand": [card.ref for card in selected], "graveyard": [card.ref for card in remainder]}
        self.reject(game, "B", {"cards": cards})
        self.reject(game, "A", {"cards": {"hand": refs[:3], "graveyard": refs[3:]}})
        self.reject(game, "A", {"cards": {"hand": [top[0].ref], "graveyard": refs}})
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.choose(game, cards)
        self.assertEqual(life - 2, game.state.players["A"].life)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in selected))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in remainder))
        ids = {card.object_id for card in remainder}
        public_graveyard = [oid for oid in game.state.players["A"].zones["graveyard"] if oid in ids]
        self.assertEqual([card.object_id for card in reversed(remainder)], public_graveyard)
        codes = [event.code for event in game.state.events[cursor:]]
        self.assertLess(codes.index("library.selection"), codes.index("effect.life"))
        self.assertFalse(any(code.startswith("card.draw") for code in codes))
        opponent = json.dumps(game.packet("pilot:B", full=True))
        self.assertTrue(all(card.ref not in opponent for card in selected))
        self.assertTrue(all(card.ref in opponent for card in remainder))
        self.replay(game, load=True)

    def test_actual_zero_domain_skips_inspection_but_keeps_printed_life_loss(self):
        game = self.session(40430102)
        spell = self.add(game.engine, SHADOW, zone="hand")
        self.add(game.engine, "Tropical Island", seat="B")
        self.add_top(game)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)
        life = game.state.players["A"].life
        lib = list(game.state.players["A"].zones["library"])
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((life - 2, lib), (game.state.players["A"].life, game.state.players["A"].zones["library"]))
        self.replay(game, load=True)

    def test_actual_rig_preserves_multicolored_powerstone_and_pays_triple_x_before_random_bottom(self):
        game = self.session(40430103)
        rig = self.add(game.engine, RIG, zone="hand")
        helix = self.add(game.engine, "Lightning Helix", zone="hand")
        top = self.add_top(game)
        for card in (rig, helix):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 12, "R": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", rig, reserve={"C": 9, "R": 1, "W": 1})
        self.resolve_top(game)
        self.cast_for(game, "A", helix, ["B"], reserve={"C": 9})
        self.assertIsNone(self.resolve(game))
        token, = (card for card in game.state.cards.values() if card.is_token and card.printed_name == "Powerstone Token")
        self.assertEqual(("battlefield", "A", True), (token.zone, token.controller, token.tapped))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (row["id"] for row in actions if row["id"].startswith(f"activate:{rig.ref}:"))
        before = authoritative_state_hash(game.state)
        reject = game.act("pilot:A", {"action_id": activation, "x": 4, "pay": "auto"})
        self.assertFalse(reject.ok, reject.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accept = game.act("pilot:A", {"action_id": activation, "x": 3, "pay": "auto"})
        self.assertTrue(accept.ok, accept.summary)
        self.assertEqual(3, game.state.stack[-1].x_value)
        self.assertTrue(game.state.cards[rig.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        context, refs = self.inspection(game, 3)
        self.assertIn("random order", context["prompt"])
        self.reject(game, "A", {"cards": {"hand": [top[3].ref], "bottom": [top[2].ref, top[4].ref]}})
        self.reject(game, "A", {"cards": refs})
        game = self.replay(game, load=True)
        self.choose(game, [top[3].ref])
        lib = game.state.players["A"].zones["library"]
        self.assertEqual("hand", game.state.cards[top[3].object_id].zone)
        self.assertEqual({top[2].object_id, top[4].object_id}, set(lib[:2]))
        self.assertTrue(all(not game.state.cards[oid].known_to for oid in lib[:2]))
        packet = json.dumps(game.packet("pilot:B", full=True))
        self.assertTrue(all(ref not in packet for ref in refs))
        self.replay(game, load=True)
