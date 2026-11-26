"""The complete Lantern either puts its private land in tapped or offers bottom and keep."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_battlefield_model import private_top_land_entry_effect
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Lantern of Revealing"
REBALANCED = "A-Lantern of Revealing"


class PrivateTopLandOptionalBottomTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-top-land-optional-bottom.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/private-top-land-optional-bottom-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private singleton and optional bottom", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_and_rebalanced_text_share_the_typed_bottom_alternative(self):
        for name, amount in ((ORIGINAL, 4), (REBALANCED, 3)):
            with self.subTest(name=name):
                result, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(result["strict_capability_ready"], result["blockers"])
                record = self.db.lookup(name, fuzzy=False)
                rows = semantics.programs_for_oracle(record.oracle_id)
                selection, = (row for row in rows if any(e["op"] == "fixed_library_selection" for e in row.effects))
                self.assertEqual("activate", selection.event)
                self.assertIsNone(selection.target_schema)
                self.assertEqual([private_top_land_entry_effect(may_put_on_bottom=True)], selection.effects)
                self.assertIn(f"{{{amount}}}, {{T}}", record.oracle_text)
                self.assertIn("library.select.battlefield_controller", selection.capability_dependencies)

    def scene(self, seed, top_name):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        previous = self.add(game.engine, "Forest", zone="library", ref="lantern-previous-next")
        top = self.add(game.engine, top_name, zone="library", ref="lantern-opaque-inspected-card")
        self.assertEqual([previous.object_id, top.object_id], game.state.players["A"].zones["library"][-2:])
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 7})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 4})
        self.resolve_top(game)
        return game, source, top, previous

    def activation(self, game, source, generic):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")
                  and row.get("cost_summary", {}).get("m", {}).get("GENERIC", 0) == generic]
        return offer

    def paid_look(self, game, source, top, options, choice):
        offer = self.activation(game, source, 4)
        result = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual([{"id": top.ref, "name": top.printed_name}], context["objects"])
        action, = context["legal_actions"]
        self.assertEqual(options, tuple(action["choice_schema"]["legal_values"]))
        for seat in "BCD":
            packet = game.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(top.ref, json.dumps(packet))
        command = {"action_id": action["id"], "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        if "put_onto_battlefield" not in options:
            self.assertFalse(game.act("pilot:A", {**command, "choice": "put_onto_battlefield"}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_nonland_can_be_put_on_bottom_without_moving_its_incarnation_or_revealing_it(self):
        game, source, top, previous = self.scene(40104071, "Sol Ring")
        old = list(game.state.players["A"].zones["library"])
        original = top.logical_object_id
        game = self.paid_look(game, source, top, ("put_on_bottom", "keep"), "put_on_bottom")
        self.assertEqual([top.object_id, *old[:-1]], game.state.players["A"].zones["library"])
        self.assertEqual(previous.object_id, game.state.players["A"].zones["library"][-1])
        self.assertEqual(original, game.state.cards[top.object_id].logical_object_id)
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_land_offers_tapped_entry_bottom_or_keep_and_resolves_only_the_selected_result(self):
        for index, choice in enumerate(("put_onto_battlefield", "keep")):
            with self.subTest(choice=choice):
                game, source, top, previous = self.scene(40104072 + index, "Island")
                original = top.logical_object_id
                game = self.paid_look(game, source, top, ("put_onto_battlefield", "put_on_bottom", "keep"), choice)
                current = game.state.cards[top.object_id]
                if choice == "keep":
                    self.assertEqual(("library", original, top.object_id),
                                     (current.zone, current.logical_object_id, game.state.players["A"].zones["library"][-1]))
                    self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
                else:
                    self.assertEqual(("battlefield", True), (current.zone, current.tapped))
                    self.assertNotEqual(original, current.logical_object_id)
                    self.assertEqual(previous.object_id, game.state.players["A"].zones["library"][-1])
                self.replay(game, load=True)

    def test_actual_any_color_mana_sibling_pays_its_tap_and_leaves_the_top_uninspected(self):
        game, source, top, _ = self.scene(40104074, "Sol Ring")
        offer = self.activation(game, source, 0)
        choices = offer["choice_schema"]["mana_output"]["options"]
        self.assertCountEqual([{color: 1} for color in "WUBRG"], [row["value"] for row in choices])
        before = tuple(game.state.cards[top.object_id].known_to)
        result = game.act("pilot:A", {"action_id": offer["id"], "mana_output": {"G": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertEqual(before, tuple(game.state.cards[top.object_id].known_to))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)
