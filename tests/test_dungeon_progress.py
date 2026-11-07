"""CR 309 and 701.49: source-pinned dungeon paths and completion timing."""

from dataclasses import replace
from hashlib import sha256
import json
import re
import unittest

from common import ROOT
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.dungeon_catalog import DUNGEON_CATALOG, DungeonId, DungeonRoom, VentureMode
from quorune.dungeon_progress import (
    DungeonJourney,
    DungeonRoomRef,
    DungeonVisit,
    VentureOffer,
    accept_venture,
    complete_finished_dungeon,
    prepare_venture,
)


class DungeonProgressTests(unittest.TestCase):
    def test_pinned_room_names_and_edges_match_the_printed_sources(self):
        payload = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())
        cards = {row["name"]: row for row in payload["cards"]}
        self.assertEqual({definition.source_name for definition in DUNGEON_CATALOG.values()}, set(cards))
        for definition in DUNGEON_CATALOG.values():
            with self.subTest(dungeon=definition.dungeon):
                text = cards[definition.source_name]["oracle_text"]
                self.assertEqual(definition.source_oracle_hash, sha256(text.encode("utf-8")).hexdigest())
                printed = []
                for line in text.split("\n//\n", 1)[0].splitlines():
                    if " — " in line:
                        label, body = line.split(" — ", 1)
                        edges = re.search(r" \(Leads to: ([^)]+)\)$", body)
                        printed.append((label, tuple(edges[1].split(", ")) if edges else ()))
                graph = [(room.label, tuple(definition.rooms[index].label for index in room.exits))
                         for room in definition.rooms]
                self.assertEqual(printed, graph)

    def test_cradle_lowers_the_named_legendary_token_with_its_printed_ability(self):
        payload = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())
        tomb = next(card for card in payload["cards"] if card["name"] == "Tomb of Annihilation")
        body, = (line.split(" — ", 1)[1] for line in tomb["oracle_text"].splitlines()
                 if line.startswith("Cradle of the Death God — "))
        compiled = fixed_token_creation_effect_template(body)
        self.assertIsNotNone(compiled)
        self.assertIsNone(compiled.target_schema)
        self.assertEqual({
            "op": "create_token", "controller": "$controller", "name": "The Atropal", "quantity": 1,
            "characteristics": {"type_line": "Token Legendary Creature — God Horror",
                                "literal_token_name_version": 1, "colors": ["B"],
                                "power": "4", "toughness": "4", "keywords": ["Deathtouch"]},
        }, compiled.effect)
        unsupported = body.replace("deathtouch", "deathtouch and all triggered abilities of the exiled cards")
        self.assertIsNone(fixed_token_creation_effect_template(unsupported))

    def test_catalog_rejects_backward_edges_and_unreachable_rooms(self):
        definition = DUNGEON_CATALOG[DungeonId.LOST_MINE]
        with self.assertRaisesRegex(ValueError, "below the current room"):
            replace(definition, rooms=(DungeonRoom("Start", (0,)), DungeonRoom("Finish")))
        with self.assertRaisesRegex(ValueError, "reachable"):
            replace(definition, rooms=(DungeonRoom("Start", (2,)), DungeonRoom("Isolated", (2,)), DungeonRoom("Finish")))

    def test_entry_modes_choose_distinct_pools_and_advance_every_printed_edge(self):
        fresh = DungeonJourney()
        ordinary, completed = prepare_venture("A", fresh, VentureMode.ORDINARY)
        undercity, _ = prepare_venture("A", fresh, VentureMode.UNDERCITY)
        self.assertIsNone(completed)
        self.assertEqual(tuple(DungeonRoomRef(dungeon, 0) for dungeon in (
            DungeonId.LOST_MINE, DungeonId.MAD_MAGE, DungeonId.TOMB,
        )), ordinary.destinations)
        self.assertEqual((DungeonRoomRef(DungeonId.UNDERCITY, 0),), undercity.destinations)
        with self.assertRaisesRegex(ValueError, "not offered"):
            accept_venture("A", fresh, ordinary, undercity.destinations[0])
        for dungeon, definition in DUNGEON_CATALOG.items():
            for index, room in enumerate(definition.rooms[:-1]):
                journey = DungeonJourney(DungeonVisit(DungeonRoomRef(dungeon, index), 1))
                expected = tuple(DungeonRoomRef(dungeon, successor) for successor in room.exits)
                for mode in VentureMode:
                    with self.subTest(dungeon=dungeon, room=index, mode=mode):
                        offer, completed = prepare_venture("B", journey, mode)
                        self.assertIsNone(completed)
                        self.assertEqual(expected, offer.destinations)
                        for destination in expected:
                            updated = accept_venture("B", journey, offer, destination)
                            self.assertEqual(DungeonJourney(DungeonVisit(destination, 1)), updated)

    def test_completion_waits_for_room_abilities_from_the_current_incarnation(self):
        bottom = DungeonRoomRef(DungeonId.LOST_MINE, 6)
        original = DungeonVisit(bottom, 1)
        journey = DungeonJourney(original)
        self.assertEqual((journey, None), complete_finished_dungeon(journey, (1, 1)))
        self.assertEqual((DungeonJourney(completed=1), original), complete_finished_dungeon(journey, ()))

        offer, completed = prepare_venture("C", journey, VentureMode.ORDINARY)
        self.assertEqual(original, completed)
        self.assertEqual(DungeonJourney(completed=1), offer.journey)
        reentry = accept_venture("C", offer.journey, offer, DungeonRoomRef(DungeonId.LOST_MINE, 0))
        self.assertEqual(DungeonJourney(DungeonVisit(DungeonRoomRef(DungeonId.LOST_MINE, 0), 2), 1), reentry)
        current_last = DungeonVisit(bottom, 2)
        last_journey = DungeonJourney(current_last, 1)
        self.assertEqual((last_journey, None), complete_finished_dungeon(last_journey, (1, 2)))
        self.assertEqual((DungeonJourney(completed=2), current_last), complete_finished_dungeon(last_journey, (1,)))

    def test_choice_round_trip_rejects_stale_owner_position_and_forged_edges(self):
        first = DungeonRoomRef(DungeonId.MAD_MAGE, 1)
        journey = DungeonJourney(DungeonVisit(first, 1))
        offer, _ = prepare_venture("D", journey, VentureMode.ORDINARY)
        wire = offer.to_dict()
        restored = VentureOffer.from_dict(json.loads(json.dumps(wire)))
        self.assertEqual(offer, restored)
        destination = restored.destinations[0]
        self.assertEqual(journey, DungeonJourney.from_dict(restored.journey.to_dict()))
        with self.assertRaisesRegex(ValueError, "owner and dungeon position"):
            accept_venture("A", journey, restored, destination)
        advanced = accept_venture("D", journey, restored, destination)
        with self.assertRaisesRegex(ValueError, "owner and dungeon position"):
            accept_venture("D", advanced, restored, destination)
        with self.assertRaisesRegex(ValueError, "not offered"):
            accept_venture("D", journey, restored, DungeonRoomRef(DungeonId.MAD_MAGE, 0))
        with self.assertRaisesRegex(ValueError, "pinned graph"):
            VentureOffer.from_dict({**wire, "destinations": [destination.to_dict()]})
        with self.assertRaisesRegex(ValueError, "existing room"):
            VentureOffer.from_dict({**wire, "destinations": [{**destination.to_dict(), "index": True}]})
        with self.assertRaisesRegex(ValueError, "version 1"):
            VentureOffer.from_dict({**wire, "schema_version": True})
        self.assertEqual(journey, restored.journey)
