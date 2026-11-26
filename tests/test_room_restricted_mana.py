from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from inherited_composition_fixture import InheritedNativeFixture
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_restriction_model import (
    ROOM_CAST_UNLOCK_MANA_CAPABILITY,
    ROOM_CAST_UNLOCK_MANA_RESTRICTION,
    canonical_mana_spend_restriction,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.shared_type_doors import SharedTypeDoors
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_room_door_count_token as token_cases


class RoomRestrictedManaTests(InheritedNativeFixture, unittest.TestCase):
    cast_room_half = token_cases.RoomDoorCountTokenTests.cast_room_half
    room_unlock_offer = token_cases.RoomDoorCountTokenTests.room_unlock_offer
    resolve_room_abilities = token_cases.RoomDoorCountTokenTests.resolve_room_abilities
    new_tokens = token_cases.RoomDoorCountTokenTests.new_tokens
    room = "Kiln Alcove // Rook Loft"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "room-restricted-mana.sqlite"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "room-restricted-mana-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Room mana witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_first_main_mana_pays_for_room_cast_and_unlock_and_replays(self):
        game = self.session(70962701)
        room = self.add(game.engine, self.room, ref="installed-room")
        room.shared_type_doors = SharedTypeDoors(("Kiln Alcove","Rook Loft"), ("Kiln Alcove",))
        other = self.add(game.engine, self.room, zone="hand", ref="fresh-room")
        bolt = self.add(game.engine, "Pinprick Bolt", zone="hand")
        hidden = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand", ref="private-room-ability-card")
        for card in (room, bolt):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.phase_index = 2
        game.state.phase, game.state.step = "beginning", "draw"
        game.state.priority_player = "A"
        game.engine.pump()
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(("beginning", "draw"), (game.state.phase, game.state.step))
        self.checkpoint(game)
        self.until(game, lambda value: value.state.phase=="precombat_main" and bool(value.state.stack))
        parent, = game.state.stack
        self.assertEqual(("A", room.object_id), (parent.controller, parent.source_object_id))
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        self.assertIn(ROOM_CAST_UNLOCK_MANA_RESTRICTION, json.dumps(game.state.players["A"].stats))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:D", full=True)))
        game = self.replay(game, load=True)

        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row.get("card")==bolt.ref for row in actions))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id":"cast", "card":bolt.ref, "targets":["B"], "pay":"auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_room_half(game, other, "Kiln Alcove")
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])

        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offers = [row for row in actions if row.get("card")==room.ref and row.get("door")=="Rook Loft"]
        self.assertEqual(1,len(offers),"the restricted unit pays the real door action")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": offers[0]["id"],"pay":"auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": offers[0]["id"],"pay":"auto"})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.resolve_room_abilities(game,"pilot:A")
        token, = self.new_tokens(game)
        self.assertEqual((3,3),tuple(game.engine._numeric_stat(token.object_id,key) for key in ("power","toughness")))
        self.replay(game,load=True)

    def test_room_mana_cannot_drop_the_spending_capability(self):
        report, semantics = register_card(self.db,self.registry,self.room)
        self.assertTrue(report["strict_capability_ready"],report["blockers"])
        card=self.db.lookup(self.room,fuzzy=False)
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                    if ROOM_CAST_UNLOCK_MANA_CAPABILITY in p.capability_dependencies)
        deps=tuple(cap for cap in program.capability_dependencies if cap!=ROOM_CAST_UNLOCK_MANA_CAPABILITY)
        changed=replace(program,capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps,profile="commander_review").to_dict())
        checked=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile="commander_review")
        self.assertFalse(checked["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:"+ROOM_CAST_UNLOCK_MANA_CAPABILITY,checked["blockers"])
        with self.assertRaises(ValueError):
            canonical_mana_spend_restriction((("room_unlock","any",()),("spell","subtypes_any",("arcane",))))

    def test_removing_the_unlock_context_breaks_the_ordinary_offer(self):
        from quorune.rules import room_unlock

        test = type(self)("test_first_main_mana_pays_for_room_cast_and_unlock_and_replays")
        result = unittest.TestResult()
        with patch.object(room_unlock,"ROOM_UNLOCK_MANA_SPEND_CONTEXT",None):
            test.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1,len(result.failures))
        self.assertIn("the restricted unit pays the real door action",result.failures[0][1])
