"""The current monarch changes the mill count across one original player set."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.monarch_selected_player_mill import MONARCH_SELECTED_PLAYER_MILL_CAPABILITY
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_monarch_movement_replacements as movement_fixture


PROFILE = "commander_review"


class MonarchSelectedPlayerMillTests(InheritedNativeFixture, unittest.TestCase):
    throne = movement_fixture.MonarchMovementReplacementTests.throne
    upkeep = movement_fixture.MonarchMovementReplacementTests.upkeep
    transfer = movement_fixture.MonarchMovementReplacementTests.transfer
    reject = movement_fixture.MonarchMovementReplacementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "monarch-selected-mill.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/monarch-selected-player-mill-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Court of Cunning", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def cue(self, seed, *, response=False, redirected=False, short=False, competing=False):
        game = self.session(seed)
        court = self.add(game.engine, "Court of Cunning")
        throne = self.throne(game) if response else None
        if redirected:
            replacement = self.add(game.engine, "Rest in Peace")
            self.assert_closed(game, replacement)
        if competing:
            leyline = self.add(game.engine, "Leyline of the Void")
            self.assert_closed(game, leyline)
            for object_id in tuple(game.state.players["B"].zones["library"])[:-2]:
                game.engine.move_card(object_id, "exile", reason="Prepare both library moves for replay")
        if short:
            library = tuple(game.state.players["C"].zones["library"])
            for object_id in library[:-1]:
                game.engine.move_card(object_id, "exile", reason="Prepare a short selected library")
        self.upkeep(game, court, throne)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        return game, throne

    def libraries(self, game):
        return {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}

    def test_actual_title_change_keeps_every_original_target_and_mills_as_many_as_possible(self):
        for changed, competing in ((False, False), (True, False), (False, True)):
            with self.subTest(changed=changed, competing=competing):
                game, throne = self.cue(76317100 + changed + 2 * int(competing), response=changed,
                                        redirected=not changed, short=True, competing=competing)
                self.reject(game, "A", {"action_id": "choose", "targets": ["B", "B"]})
                self.target(game, "A", ["B", "C"])
                self.assertEqual(["B", "C"], game.state.stack[-1].targets)
                if changed:
                    self.transfer(game, throne)
                before = self.libraries(game)
                game = self.replay(game, load=True)
                moves = []
                original_move = ZoneTransitionOwner.move_cards_simultaneously

                def observe(owner, changes, **kwargs):
                    milling = changes and all(owner.state.cards[object_id].zone == "library" for object_id, _ in changes)
                    result = original_move(owner, changes, **kwargs)
                    if milling:
                        moves.append(tuple(changes))
                    return result

                with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", observe):
                    pending = self.resolve(game)
                    if competing:
                        hidden = [game.state.cards[object_id]
                                  for object_id in (*before["B"], *before["C"])]
                        for viewer in "ABCD":
                            packet = game.packet("pilot:" + viewer, full=True)
                            self.assertEqual(viewer == "B", packet["decision"] is not None)
                            public = json.dumps(packet)
                            for card in hidden:
                                self.assertNotIn(card.ref, public)
                                self.assertNotIn(card.object_id, public)
                        game = self.replay(game, load=True)
                        for seat in ("B", "B", "C"):
                            pending = game.state.pending_decision
                            self.assertEqual(("replacement.order", [seat]), (pending.kind, pending.actors))
                            self.assertEqual(before, self.libraries(game))
                            option = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"][0]["id"]
                            result = game.act("pilot:" + seat, {"action_id": "choose", "replacement": option})
                            self.assertTrue(result.ok, result.summary)
                        pending = self.resolve(game)
                    self.assertIsNone(pending)
                expected_b = min(2 if changed else 10, len(before["B"]))
                expected = {"A": 0, "B": expected_b, "C": 1, "D": 0}
                after = self.libraries(game)
                for seat, count in expected.items():
                    self.assertEqual(len(before[seat]) - count, len(after[seat]))
                milled = {*before["B"][-expected_b:], before["C"][-1]}
                batch, = moves
                self.assertEqual(milled, {object_id for object_id, _ in batch})
                destination = "graveyard" if changed else "exile"
                self.assertTrue(all(game.state.cards[object_id].zone == destination for object_id in milled))
                self.replay(game, load=True)

    def test_actual_zero_targets_and_departed_targets_keep_ordinary_target_revalidation(self):
        for players in ((), ("B",), ("B", "C")):
            with self.subTest(players=players):
                game, _ = self.cue(76317110 + len(players))
                self.target(game, "A", players)
                if players:
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertFalse(game.state.players["B"].in_game)
                before = self.libraries(game)
                self.assertIsNone(self.resolve(game))
                after = self.libraries(game)
                for seat in "ACD":
                    amount = 10 if seat in players else 0
                    self.assertEqual(len(before[seat]) - amount, len(after[seat]))
                self.replay(game, load=True)

    def test_complete_original_requires_one_unrestricted_target_set_and_both_fixed_counts(self):
        cap = MONARCH_SELECTED_PLAYER_MILL_CAPABILITY
        report, semantics = register_card(self.db, self.registry, "Court of Cunning")
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = semantics.programs_for_oracle(report["oracle_id"])
        self.assertEqual(2, len(programs))
        program, = (row for row in programs if cap in row.capability_dependencies)
        self.assertEqual("any_number", program.target_schema["count_parameter"]["parameter"])
        selection = program.effects[0]
        self.assertEqual(("$targets", "$targets"), (selection["effects"][0]["players"],
                                                    selection["otherwise_effects"][0]["players"]))
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", result["blockers"])
        card = self.db.lookup("Court of Cunning", fuzzy=False)
        for old, new in (("target players", "target opponents"), ("each of those players mills ten", "each opponent mills ten"),
                         ("ten cards instead.", "ten cards instead. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
