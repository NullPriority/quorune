"""Native Voyager Staff payment, delayed return, and incarnation replay."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class VoyagerStaffLinkedReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "voyager-staff.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/voyager-staff-linked-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Voyager Staff", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_exiled_card_link_and_paid_sacrifice(self):
        card = self.db.lookup("Voyager Staff", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("activate", node.event)
        self.assertIs(True, node.cost["sacrifice_source"])
        self.assertEqual(2, node.cost["mana"]["GENERIC"])
        self.assertEqual(["creature"], node.target_schema["types_any"])
        exile, returned = node.effects
        self.assertEqual(("exile", "return"), (exile["phase"], returned["phase"]))
        self.assertEqual(exile["binding_id"], returned["binding_id"])
        self.assertEqual("next_end_step", returned["spec"]["timing"])
        self.assertEqual("owner", returned["spec"]["controller"])
        for old, new in (("Exile target creature", "Exile two target creatures"),
                         ("owner's control", "your control")):
            mutant = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                                         capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", mutant.status)

    def activate(self, game, staff, target, *, accepted=True):
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in rows if row["id"].startswith(f"activate:{staff.ref}:")]
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))
        else:
            self.assertEqual("graveyard", game.state.cards[staff.object_id].zone)
            self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
            self.assertEqual([target.ref], game.state.stack[-1].targets)

    def end_step(self, game):
        self.until(game, lambda current: current.state.step == "end_step")
        self.assertIsNone(self.resolve(game))

    def test_actual_cost_and_return_under_owner_or_stale_exile_replay(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                game = self.session(747043001 + stale)
                staff = self.add(game.engine, "Voyager Staff")
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                land = self.add(game.engine, "Plains")
                borrow = self.add(game.engine, "Act of Treason", zone="hand")
                pull = self.add(game.engine, "Pull from Eternity", seat="B", zone="hand") if stale else None
                if pull is not None:
                    self.add(game.engine, "Plains", seat="B", ref="staff-response-plains")
                for card in (staff, borrow, pull):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, mana={"C": 4, "R": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", borrow, [target.ref], reserve={"C": 2})
                self.resolve_top(game)
                self.assertEqual("A", game.state.cards[target.object_id].controller)
                self.activate(game, staff, land, accepted=False)
                old = game.state.cards[target.object_id].logical_object_id
                self.activate(game, staff, target)
                self.resolve_top(game)
                exile = game.state.cards[target.object_id]
                self.assertEqual("exile", exile.zone)
                self.assertNotEqual(old, exile.logical_object_id)
                exiled_id = exile.logical_object_id
                if pull is not None:
                    self.cast_for(game, "B", pull, [exile.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                game = self.replay(game, load=True)
                self.end_step(game)
                returned = game.state.cards[target.object_id]
                self.assertEqual("graveyard" if stale else "battlefield", returned.zone)
                if not stale:
                    self.assertEqual(("B", "B"), (returned.owner, returned.controller))
                    self.assertNotEqual(exiled_id, returned.logical_object_id)
                self.replay(game, load=True)

    def test_actual_tokens_and_commander_move_to_command_do_not_return(self):
        for use_commander in (False, True):
            with self.subTest(commander=use_commander):
                game = self.session(747043010 + use_commander)
                staff = self.add(game.engine, "Voyager Staff")
                if use_commander:
                    target = next(card for card in game.state.cards.values() if card.owner == "B" and card.zone == "command")
                    game.engine.move_card(target.object_id, "battlefield", controller="B", reason="commander witness")
                    alarm = None
                else:
                    alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
                    self.assert_closed(game, alarm)
                self.assert_closed(game, staff)
                self.prepare(game, mana={"C": 2} if alarm is None else {"C": 3, "W": 1})
                self.checkpoint(game)
                if alarm is not None:
                    self.cast_for(game, "A", alarm, reserve={"C": 2})
                    self.resolve_top(game)
                    target = next(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
                self.activate(game, staff, target)
                if use_commander:
                    decision = self.resolve(game)
                    self.assertEqual("state.commander_zone", decision.kind)
                    self.assertEqual(("pilot:B",), tuple(game.pending_principals()))
                    game = self.replay(game, load=True)
                    result = game.act("pilot:B", {"action_id": "command", "choice": "command"})
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.end_step(game)
                self.assertEqual("command" if use_commander else "outside",
                                 game.state.cards[target.object_id].zone)
                self.replay(game, load=True)
