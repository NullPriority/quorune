"""Original cost, supplying opponent, and public-exile-gated source return."""

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
from quorune.land_exile_source_return_model import LAND_EXILE_RETURN_CAPABILITY, land_exile_source_return_instruction
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class ActualExiledLandSourceReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "exiled-land-source-return.sqlite3"
        fixture = ROOT / "tests/fixtures/actual-exiled-land-source-return-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Exiled land return", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def top(self, game, name, seat):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=f"return-private-top-{seat}")
        card.known_to = []
        card.revealed_to = []
        self.assertEqual(card.object_id, game.state.players[seat].zones["library"][-1])
        return card

    def ready(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def actions(self, game, seat="A"):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def activate(self, game, source):
        prefix = f"activate:{source.ref}:"
        offer, = (row for row in self.actions(game) if row["id"].startswith(prefix))
        command = {"action_id": offer["id"], "targets": ["B"], "pay": "auto"}
        self.reject(game, "A", {**command, "targets": ["A"]})
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def actual_exile(self, game, hidden, other):
        current = game.state.cards[hidden.object_id]
        self.assertEqual(("exile", "B", False), (current.zone, current.owner, current.face_down))
        self.assertNotIn("temporary_play_permission", current.annotations)
        self.assertEqual(other.object_id, game.state.players["C"].zones["library"][-1])
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertIn(current.ref, packet)
            self.assertNotIn(other.ref, packet)
        return current

    def test_actual_land_gives_only_the_resolving_controller_an_immediate_return_or_decline(self):
        for accept in (True, False):
            with self.subTest(accept=accept):
                game = self.session(76341000 + accept)
                source = self.add(game.engine, "Dimensional Infiltrator", seat="B" if accept else "A")
                theft = self.add(game.engine, "Act of Treason", zone="hand") if accept else None
                wastes = self.add(game.engine, "Wastes")
                plains = self.add(game.engine, "Plains")
                hidden = self.top(game, "Forest", "B")
                other = self.top(game, "Forest", "C")
                self.ready(game, source, *((theft,) if theft is not None else ()),
                           mana={"R": 1, "W": 2} if accept else None)
                if theft is not None:
                    self.cast(game, "A", theft, [source.ref])
                    self.resolve_top(game)
                    source = game.state.cards[source.object_id]
                    self.assertEqual(("B", "A"), (source.owner, source.controller))
                self.activate(game, source)
                self.assertTrue(game.state.cards[wastes.object_id].tapped)
                if not accept:
                    self.assertTrue(game.state.cards[plains.object_id].tapped)
                for seat in "ABCD":
                    packet = json.dumps(game.packet("pilot:" + seat, full=True))
                    self.assertNotIn(hidden.ref, packet)
                    self.assertNotIn(hidden.object_id, packet)
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                self.actual_exile(game, hidden, other)
                self.reject(game, "B", {"action_id": "choose", "choice": "return"})
                self.reject(game, "A", {"action_id": "choose", "choice": "cast"})
                game = self.replay(game, load=True)
                choice = "return" if accept else "decline"
                result = game.act("pilot:A", {"action_id": "choose", "choice": choice})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[source.object_id]
                self.assertEqual("hand" if accept else "battlefield", current.zone)
                if accept:
                    self.assertIn(source.object_id, game.state.players["B"].zones["hand"])
                    self.assertNotIn(source.object_id, game.state.players["A"].zones["hand"])
                exiled = self.actual_exile(game, hidden, other)
                self.assertFalse(any(row["id"] == f"play-land:{exiled.ref}" for row in self.actions(game)))
                self.replay(game, load=True)

    def test_actual_nonland_or_new_source_never_gets_the_return_choice(self):
        for recast in (True, False):
            with self.subTest(recast=recast):
                game = self.session(76341010 + recast)
                source = self.add(game.engine, "Dimensional Infiltrator")
                self.add(game.engine, "Wastes")
                self.add(game.engine, "Plains")
                hidden = self.top(game, "Forest" if recast else "Unsummon", "B")
                other = self.top(game, "Forest", "C")
                if recast:
                    bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    self.add(game.engine, "Island", seat="B")
                    self.ready(game, source, bounce, mana={"U": 1, "W": 2})
                else:
                    self.ready(game, source)
                original = source.logical_object_id
                self.activate(game, source)
                if recast:
                    self.cast(game, "B", bounce, [source.ref])
                    self.resolve_top(game)
                    moved = game.state.cards[source.object_id]
                    self.assertEqual("hand", moved.zone)
                    self.cast(game, "A", moved)
                    self.resolve_top(game)
                    self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.actual_exile(game, hidden, other)
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_actual_departed_target_never_supplies_a_replacement_library(self):
        game = self.session(76341020)
        source = self.add(game.engine, "Dimensional Infiltrator")
        self.add(game.engine, "Wastes")
        self.add(game.engine, "Plains")
        self.top(game, "Forest", "B")
        other = self.top(game, "Forest", "C")
        self.ready(game, source)
        self.activate(game, source)
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(other.object_id, game.state.players["C"].zones["library"][-1])
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_complete_original_requires_its_scoped_return_and_retains_other_abilities(self):
        cap = LAND_EXILE_RETURN_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Dimensional Infiltrator")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        self.assertEqual(land_exile_source_return_instruction(), dict(program.effects[0]))
        self.assertEqual("opponent", program.target_schema["player_relation"])
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        unavailable = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(unavailable, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Dimensional Infiltrator", fuzzy=False)
        for old, new in (("Target opponent", "Target player"), ("land card", "creature card"),
                         ("its owner's hand.", "your hand."), ("Flying", "Flying\nRewrite history.")):
            with self.subTest(unsupported=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                compiled = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", compiled.status)
