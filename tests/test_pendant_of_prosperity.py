"""Original Pendant entry control and ordered private activation witnesses."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from scripts.build_test_database import build_fixture_database

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import ENTRY_CONTROL_OPPONENT_CAPABILITY, LINKED_ENTRY_OPPONENT_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.ordered_source_owner_hand_entry import CAPABILITY, FIELD


PENDANT = "Pendant of Prosperity"
LAND = "Generic Bound Plains"


class PendantOfProsperityTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "pendant.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/external-entry-tapped-cards.json",
            ROOT / "tests/fixtures/pendant-of-prosperity-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Pendant opponents", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(LAND, 30),
        ], ["Generic Bound Commander"])

    def test_original_two_compiler_owners_withdraw_independently(self):
        card = self.db.lookup(PENDANT)

        def compile_card(original):
            return compile_oracle_card(original, capability_registry=self.registry,
                                       capability_profile="commander_review")

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        by_capability = {dependency: node for face in ir.faces for node in face.nodes
                         for dependency in node.capability_dependencies}
        entry, activation = by_capability[ENTRY_CONTROL_OPPONENT_CAPABILITY], by_capability[CAPABILITY]
        for node in (entry, activation):
            self.assertEqual(node.text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(["draw", "put_card_from_hand"] * 2, [row["op"] for row in activation.effects])
        self.assertEqual([None, None, 1, 1], [row.get(FIELD) for row in activation.effects])
        for original, changed in (("an opponent", "a player"), ("{2}", "{3}"),
                                  ("This artifact's owner", "This artifact's controller")):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", compile_card(
                    replace(card, oracle_text=card.oracle_text.replace(original, changed))).status)
        for owner, other, matcher in (
            (ENTRY_CONTROL_OPPONENT_CAPABILITY, CAPABILITY,
             "quorune.compiler.entry_designation_templates._entry_control_opponent_handler"),
            (CAPABILITY, ENTRY_CONTROL_OPPONENT_CAPABILITY,
             "quorune.compiler.activation_paid_sacrifice_results.ordered_source_owner_hand_entry_activation"),
        ):
            with self.subTest(owner=owner), patch(matcher, return_value=None):
                mutant = compile_card(card)
                caps = {dependency for face in mutant.faces for node in face.nodes
                        for dependency in node.capability_dependencies}
                self.assertNotEqual("exact", mutant.status)
                self.assertNotIn(owner, caps)
                self.assertIn(other, caps)

    def _activate(self, game, seat, source):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        accepted = game.act("pilot:" + seat, {"action_id": action, "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("semantic.choice", self.resolve(game).kind)

    def _private_entry(self, game, seat, land, *, foreign):
        self.assertEqual(["pilot:" + seat], list(game.pending_principals()))
        actor = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertIn(land.ref, {row["id"] for row in actor["objects"]})
        self.assertNotIn(land.ref, json.dumps(game.packet("pilot:" + foreign, full=True)))
        command = {"action_id": "choose", "card": land.ref}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + foreign, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        return command

    def _give(self, game, source, opponent):
        self.cast_for(game, "A", source)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        entry = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual({"B", "C", "D"}, {option["id"].rsplit(":", 1)[-1] for option in entry})
        selected, = [row["id"] for row in entry if row["id"].endswith(":" + opponent)]
        command = {"action_id": "choose", "replacement": selected}
        self.assertFalse(game.act("pilot:C", command).ok)
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        return game

    def test_original_enters_under_opponent_then_orders_both_private_choices_and_replays(self):
        game = self.session(19461601)
        source = self.add(game.engine, PENDANT, zone="hand")
        controller_land = self.add(game.engine, LAND, seat="B", zone="hand", ref="private-controller-land")
        owner_land = self.add(game.engine, LAND, zone="hand", ref="private-owner-land")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3})
        game.state.players["B"].mana_pool["C"] = 2
        self.checkpoint(game)
        game = self._give(game, source, "B")
        moved = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", "A", "B"), (moved.zone, moved.owner, moved.controller))
        self.assertIn(moved.object_id, game.state.players["B"].zones["battlefield"])
        self.assertNotIn(moved.object_id, game.state.players["A"].zones["battlefield"])
        self.assertNotIn(LINKED_ENTRY_OPPONENT_FIELD, moved.annotations)

        library = {seat: len(game.state.players[seat].zones["library"]) for seat in "AB"}
        self._activate(game, "B", moved)
        self.assertEqual("A", game.state.active_player)
        self.assertEqual(0, game.state.players["B"].mana_pool["C"])
        self.assertEqual(library["B"] - 1, len(game.state.players["B"].zones["library"]))
        self.assertEqual(library["A"], len(game.state.players["A"].zones["library"]))
        command = self._private_entry(game, "B", controller_land, foreign="A")
        game = self.replay(game, load=True)
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(("battlefield", "B"), (game.state.cards[controller_land.object_id].zone,
                                                game.state.cards[controller_land.object_id].controller))
        self.assertEqual(library["A"] - 1, len(game.state.players["A"].zones["library"]))
        command = self._private_entry(game, "A", owner_land, foreign="B")
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A"), (game.state.cards[owner_land.object_id].zone,
                                                game.state.cards[owner_land.object_id].controller))
        self.replay(game, load=True)

    def test_same_player_controls_and_owns_both_ordered_pairs_and_replays(self):
        game = self.session(19461602)
        source = self.add(game.engine, PENDANT)
        land = self.add(game.engine, LAND, zone="hand", ref="private-both-role-land")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        before = len(game.state.players["A"].zones["library"])
        self._activate(game, "A", source)
        self.assertEqual(before - 1, len(game.state.players["A"].zones["library"]))
        self._private_entry(game, "A", land, foreign="B")
        accepted = game.act("pilot:A", {"action_id": "choose"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(before - 2, len(game.state.players["A"].zones["library"]))
        command = self._private_entry(game, "A", land, foreign="B")
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.replay(game, load=True)

    def test_prospective_controller_changes_external_entry_scope_and_replays(self):
        for recipient, tapped in (("B", False), ("C", True)):
            with self.subTest(recipient=recipient):
                game = self.session(19461611 + tapped)
                source = self.add(game.engine, PENDANT, zone="hand")
                taxation = self.add(game.engine, "Blind Obedience", seat="B")
                self.assert_closed(game, source)
                self.assert_closed(game, taxation)
                self.prepare(game, mana={"C": 3})
                self.checkpoint(game)
                game = self._give(game, source, recipient)
                current = game.state.cards[source.object_id]
                self.assertEqual(("battlefield", "A", recipient, tapped),
                                 (current.zone, current.owner, current.controller, current.tapped))
                self.replay(game, load=True)
