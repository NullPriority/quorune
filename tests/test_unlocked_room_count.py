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
from quorune.shared_type_doors import ROOM_UNLOCKED_COUNT_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_nonmodal_face_mana_value as room_cases


class UnlockedRoomCountTests(InheritedNativeFixture, unittest.TestCase):
    cast_room_half = room_cases.NonmodalFaceManaValueRuntimeTests.cast_room_half
    room_unlock_offer = room_cases.NonmodalFaceManaValueRuntimeTests.room_unlock_offer
    resolve_room_abilities = room_cases.NonmodalFaceManaValueRuntimeTests.resolve_room_abilities
    observer_name = "Gallery Sentinel"
    private_name = "Generic Bound Growth"
    base_power = 2
    boosted_power = 4

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "unlocked-room-count.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database(
            [fixtures / name for name in (
                "bound-effect-program-cards.json",
                "nonmodal-face-mana-value-cards.json",
                "expanded-basic-type-setting-cards.json",
                "attached-occurrence-cards.json",
                "recipient-control-cards.json",
                "unlocked-room-count-cards.json",
            )], path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public door count", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def power(self, game, observer):
        return game.engine._numeric_stat(observer.object_id, "power")

    def cast_at(self, game, spell, targets):
        self.priority(game, "A")
        offer = self.action(game, "A", game.state.cards[spell.object_id])
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": targets, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_live_two_doors_type_loss_control_and_replay(self):
        game = self.session(70952101)
        observer = self.add(game.engine, self.observer_name)
        room = self.add(game.engine, "Bottomless Pool // Locker Room", zone="hand")
        disguise = self.add(game.engine, "Song of the Dryads", zone="hand")
        rescue = self.add(game.engine, "Naturalize", zone="hand")
        donate = self.add(game.engine, "Donate", zone="hand")
        secret = self.add(game.engine, self.private_name, seat="C", zone="hand", ref="private-room-count-unused")
        for card in (observer, room, disguise, rescue, donate):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 12, "U": 3, "G": 2})
        self.checkpoint(game)
        self.assertEqual(self.base_power, self.power(game, observer))
        self.cast_room_half(game, room, "Locker Room", reserve={"U": 2, "G": 2})
        current = game.state.cards[room.object_id]
        self.assertEqual(("Locker Room",), current.shared_type_doors.unlocked)
        self.assertEqual(self.base_power, self.power(game, observer))

        unlock = self.room_unlock_offer(game, current, "Bottomless Pool")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": unlock["id"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": unlock["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_room_abilities(game, "pilot:A")
        self.assertEqual(("Bottomless Pool", "Locker Room"),
                         game.state.cards[room.object_id].shared_type_doors.unlocked)
        self.assertEqual(self.boosted_power, self.power(game, observer), "two doors qualify on one Room")
        game = self.replay(game, load=True)
        self.assertEqual(self.boosted_power, self.power(game, observer))

        self.cast_at(game, disguise, [room.ref])
        current = game.state.cards[room.object_id]
        effective = game.engine._effective_card_data(current)
        self.assertNotIn("room", game.engine._type_parts(effective["type_line"])[1])
        self.assertEqual(2, len(current.shared_type_doors.unlocked))
        self.assertEqual(self.base_power, self.power(game, observer))
        self.cast_at(game, rescue, [disguise.ref])
        self.assertEqual(self.boosted_power, self.power(game, observer))
        self.cast_at(game, donate, ["B", room.ref])
        self.assertEqual("B", game.state.cards[room.object_id].controller)
        self.assertEqual(self.base_power, self.power(game, observer))
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_room_count_requires_its_typed_runtime_capability(self):
        report, semantics = register_card(self.db, self.registry, self.observer_name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        card = self.db.lookup(self.observer_name, fuzzy=False)
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                    if ROOM_UNLOCKED_COUNT_CAPABILITY in p.capability_dependencies)
        dependencies = tuple(cap for cap in program.capability_dependencies
                             if cap != ROOM_UNLOCKED_COUNT_CAPABILITY)
        closure = self.registry.closure(dependencies, profile="commander_review").to_dict()
        checked = bind_semantic_program_runtime(
            replace(program, coverage=[], capability_dependencies=dependencies, capability_closure=closure),
            capability_registry=self.registry, profile="commander_review",
        )
        self.assertFalse(checked["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + ROOM_UNLOCKED_COUNT_CAPABILITY,
                      checked["blockers"])

    def test_counting_one_per_room_loses_the_live_bonus(self):
        from quorune import card_program_faces

        original = card_program_faces.controlled_unlocked_room_door_count

        def cap_at_one(state, controller, public_object):
            count = original(state, controller, public_object)
            return min(count, 1) if count is not None else None

        witness = type(self)("test_live_two_doors_type_loss_control_and_replay")
        result = unittest.TestResult()
        with patch.object(card_program_faces, "controlled_unlocked_room_door_count", side_effect=cap_at_one):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("two doors qualify on one Room", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
