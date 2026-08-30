from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session
from quorune.errors import GameRuleError
from quorune.semantics import SemanticProgram
from quorune.zone_trigger_events import (
    ZoneChangeOccurrence,
    ZoneTransitionKind,
    ZoneTriggerEventError,
    normalized_library_position,
    normalized_zone_trigger_events,
)


def occurrence(**changes) -> ZoneChangeOccurrence:
    values = {
        "object_id": "object-1",
        "card_ref": "A01",
        "owner": "A",
        "origin": "hand",
        "destination": "battlefield",
        "previous_controller": "A",
        "current_controller": "A",
        "previous_logical_object_id": "object-1:0",
        "current_logical_object_id": "object-1:1",
        "zone_change_counter": 1,
        "token": False,
        "card_object": True,
        "previous_characteristics": {
            "type_line": "Creature — Human Cleric",
            "mana_value": 2,
        },
        "current_characteristics": {
            "type_line": "Creature — Human Cleric",
            "mana_value": 2,
        },
        "previous_attachments": (),
        "cause": "test",
    }
    values.update(changes)
    return ZoneChangeOccurrence(**values)


class ZoneTriggerEventModelTests(unittest.TestCase):
    def test_library_position_normalization_is_owned_by_zone_transitions(self):
        self.assertIsNone(normalized_library_position("graveyard", True))
        self.assertEqual("top", normalized_library_position("library", " TOP "))
        self.assertEqual("bottom", normalized_library_position("library", "bottom"))
        self.assertEqual(3, normalized_library_position("library", 3))
        for malformed in (True, 0, -1, "middle"):
            with self.subTest(malformed=malformed), self.assertRaises(GameRuleError):
                normalized_library_position("library", malformed)

    def test_derives_exact_enter_and_death_events(self):
        entered = normalized_zone_trigger_events(occurrence())
        self.assertEqual(
            ["permanent.enter", "creature.enter"],
            [event.kind for event in entered],
        )
        self.assertTrue(all(event.source_timing == "after" for event in entered))

        died = normalized_zone_trigger_events(
            occurrence(
                origin="battlefield",
                destination="graveyard",
                previous_controller="B",
                current_controller="A",
                previous_characteristics={
                    "type_line": "Legendary Artifact Creature — Golem",
                    "mana_value": 4,
                },
            )
        )
        self.assertEqual(
            [
                "permanent.leave",
                "creature.dies",
                "artifact.graveyard",
                "permanent.graveyard",
            ],
            [event.kind for event in died],
        )
        self.assertTrue(all(event.source_timing == "before" for event in died))
        self.assertTrue(
            all(event.context["controller"] == "B" for event in died)
        )
        self.assertEqual(
            ["artifact", "creature"],
            list(died[0].context["types"]),
        )

    def test_noncreature_kindred_subtype_has_graveyard_but_not_death_event(self):
        departed = normalized_zone_trigger_events(
            occurrence(
                origin="battlefield",
                destination="graveyard",
                previous_characteristics={
                    "type_line": "Kindred Enchantment — Goblin",
                    "mana_value": 2,
                },
            )
        )

        self.assertEqual(
            ["permanent.leave", "permanent.graveyard"],
            [event.kind for event in departed],
        )
        self.assertEqual(["goblin"], list(departed[-1].context["subtypes"]))
        self.assertIn("kindred", departed[-1].context["types"])
        self.assertNotIn("creature", departed[-1].context["types"])

    def test_occurrence_rejects_malformed_and_non_event_moves_are_empty(self):
        with self.assertRaisesRegex(
            ZoneTriggerEventError, "zone_change_counter"
        ):
            occurrence(zone_change_counter=True)
        with self.assertRaisesRegex(ZoneTriggerEventError, "nonempty string"):
            occurrence(previous_controller="")
        with self.assertRaisesRegex(ZoneTriggerEventError, "duplicates"):
            occurrence(previous_attachments=("A02", "A02"))
        with self.assertRaisesRegex(ZoneTriggerEventError, "must be an object"):
            occurrence(previous_characteristics=[])
        with self.assertRaisesRegex(ZoneTriggerEventError, "not canonical"):
            occurrence(current_characteristics={"invalid": {1, 2}})

        moved = occurrence(origin="library", destination="hand")
        self.assertEqual((), normalized_zone_trigger_events(moved))

    def test_occurrence_deep_freezes_input_and_has_canonical_fingerprint(self):
        previous = {
            "type_line": "Creature — Human",
            "colors": ["W"],
        }
        current = {
            "colors": ["W"],
            "type_line": "Creature — Human",
        }
        first = occurrence(
            previous_characteristics=previous,
            current_characteristics=current,
        )
        second = occurrence(
            previous_characteristics={
                "colors": ["W"],
                "type_line": "Creature — Human",
            },
            current_characteristics={
                "type_line": "Creature — Human",
                "colors": ["W"],
            },
        )
        previous["colors"].append("U")
        current["type_line"] = "Land"

        self.assertEqual(("W",), first.previous_characteristics["colors"])
        self.assertEqual("Creature — Human", first.current_characteristics["type_line"])
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_private_library_to_hand_move_produces_no_public_trigger_event(self):
        moved = occurrence(
            origin="library",
            destination="hand",
            previous_characteristics={"type_line": "Creature — Human"},
            current_characteristics={"type_line": "Creature — Human"},
        )

        self.assertEqual((), normalized_zone_trigger_events(moved))

    def test_countered_physical_spell_derives_exact_normalized_events(self):
        countered = occurrence(
            origin="stack",
            destination="graveyard",
            previous_characteristics={"type_line": "Instant"},
            current_characteristics={"type_line": "Instant"},
            transition_kind=ZoneTransitionKind.COUNTERED_SPELL,
        )

        events = normalized_zone_trigger_events(countered)

        self.assertEqual(
            ["spell.countered", "card.graveyard"],
            [event.kind for event in events],
        )
        self.assertEqual(
            ["before", "after"],
            [event.source_timing for event in events],
        )
        self.assertEqual(
            ZoneTransitionKind.COUNTERED_SPELL.value,
            countered.to_dict()["transition_kind"],
        )

    def test_countered_spell_replaced_to_exile_has_no_graveyard_event(self):
        countered = occurrence(
            origin="stack",
            destination="exile",
            previous_characteristics={"type_line": "Sorcery"},
            current_characteristics={"type_line": "Sorcery"},
            transition_kind=ZoneTransitionKind.COUNTERED_SPELL,
        )

        events = normalized_zone_trigger_events(countered)

        self.assertEqual(["spell.countered"], [event.kind for event in events])
        self.assertEqual("before", events[0].source_timing)

    def test_counter_transition_kind_is_closed_and_typed(self):
        with self.assertRaisesRegex(ZoneTriggerEventError, "supported typed value"):
            occurrence(transition_kind="countered_spell")

    def test_sacrifice_transition_is_closed_and_emits_typed_event(self):
        value = occurrence(
            origin="battlefield",
            destination="graveyard",
            transition_kind=ZoneTransitionKind.SACRIFICE,
        )

        self.assertEqual(
            "sacrifice", value.to_dict()["transition_kind"]
        )
        self.assertEqual(
            "permanent.sacrificed",
            normalized_zone_trigger_events(value)[0].kind,
        )


class ZoneTriggerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_source_that_leaves_still_observes_simultaneous_death(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=6036001,
        )
        keep_all(session)
        engine = session.engine
        creatures = [
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and "creature"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
            and not card.is_commander
        ][:2]
        self.assertEqual(2, len(creatures))
        source, other = creatures
        for card in creatures:
            engine.move_card(card.object_id, "battlefield", controller="A")
        engine.semantics.put(
            SemanticProgram(
                key=f"{source.oracle_id}:test:all-dies",
                label="Observe each death",
                oracle_id=source.oracle_id,
                ability_id="test:all-dies",
                active_zone="battlefield",
                event="creature.dies",
                effects=[],
            )
        )

        engine._move_cards_simultaneously(
            ((source.object_id, "graveyard"), (other.object_id, "graveyard")),
            reason="simultaneous death fixture",
        )

        items = [
            item
            for batch in engine.state.pending_trigger_batches
            for item in batch.items
            if item.label == "Observe each death"
        ]
        self.assertEqual(2, len(items))
        self.assertEqual(
            {source.ref, other.ref},
            {item["context"]["card"] for item in items},
        )


if __name__ == "__main__":
    unittest.main()
