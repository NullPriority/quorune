from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.shared_type_doors import ROOM_DOOR_AMOUNT_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_nonmodal_face_mana_value as room_cases


class RoomDoorCountTokenTests(InheritedNativeFixture, unittest.TestCase):
    cast_room_half = room_cases.NonmodalFaceManaValueRuntimeTests.cast_room_half
    room_unlock_offer = room_cases.NonmodalFaceManaValueRuntimeTests.room_unlock_offer
    resolve_room_abilities = room_cases.NonmodalFaceManaValueRuntimeTests.resolve_room_abilities
    room = "Archive Corridor // Lantern Court"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "room-token-size.sqlite"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "room-door-count-token-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Door count token witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def new_tokens(self, game):
        return [card for card in game.state.cards.values() if card.object_kind == "token" and card.zone == "battlefield"]

    def test_paid_second_door_sets_one_token_size_and_replays(self):
        game = self.session(70962601)
        room = self.add(game.engine, self.room, zone="hand")
        hidden = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand", ref="room-token-private-card")
        self.assert_closed(game, room)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 3, "W": 2})
        self.checkpoint(game)
        self.cast_room_half(game, room, "Archive Corridor", reserve={"C":2,"W":1})
        self.assertEqual([], self.new_tokens(game))
        room = game.state.cards[room.object_id]
        offer = self.room_unlock_offer(game, room, "Lantern Court")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id":offer["id"],"pay":"auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id":offer["id"],"pay":"auto"})
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.resolve_room_abilities(game, "pilot:A")
        token, = self.new_tokens(game)
        self.assertEqual("A", token.controller)
        self.assertEqual((2,2), tuple(game.engine._numeric_stat(token.object_id,key)
                                     for key in ("power","toughness")), "both doors set the token size")
        self.assertIn("flying", game.engine._combat_keywords(token))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_token_statistics_require_the_typed_door_quantity(self):
        report, semantics = register_card(self.db, self.registry, self.room)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        card = self.db.lookup(self.room, fuzzy=False)
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                    if ROOM_DOOR_AMOUNT_CAPABILITY in p.capability_dependencies)
        dependencies = tuple(cap for cap in program.capability_dependencies
                             if cap != ROOM_DOOR_AMOUNT_CAPABILITY)
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        checked = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(checked["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:"+ROOM_DOOR_AMOUNT_CAPABILITY, checked["blockers"])

    def test_counting_one_per_room_fails_the_native_token_witness(self):
        from quorune import card_program_faces

        original = card_program_faces.controlled_unlocked_room_door_count
        def at_most_one(*args, **kwargs):
            count = original(*args, **kwargs)
            return min(count, 1) if count is not None else None
        test = type(self)("test_paid_second_door_sets_one_token_size_and_replays")
        result = unittest.TestResult()
        with patch.object(card_program_faces, "controlled_unlocked_room_door_count", side_effect=at_most_one):
            test.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("both doors set the token size", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
