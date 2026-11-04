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
    def test_composition_separates_one_permanent_departure_from_each_physical_arrival(self):
        from quorune.composition_departure import CompositionZoneFact,CompositionZoneRole
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        parent=ContinuousObjectIdentity('object-1','object-1@2')
        departure=occurrence(origin='battlefield',destination='graveyard',
            previous_logical_object_id='object-1@2',current_logical_object_id='object-1@3',
            previous_characteristics={'type_line':'Artifact Creature — Beast'},
            composition=CompositionZoneFact(parent,CompositionZoneRole.PERMANENT,2))
        kinds=[event.kind for event in normalized_zone_trigger_events(departure)]
        self.assertEqual(1,kinds.count('permanent.leave'));self.assertEqual(1,kinds.count('creature.dies'))
        self.assertNotIn('card.enter_graveyard',kinds)
        for index,type_line in enumerate(('Creature — Beast','Artifact')):
            component=occurrence(object_id='part-'+str(index),card_ref='PART'+str(index),
                origin='battlefield',destination='graveyard',previous_characteristics={'type_line':'Creature'},
                current_characteristics={'type_line':type_line},
                composition=CompositionZoneFact(parent,CompositionZoneRole.COMPONENT,2))
            kinds=[event.kind for event in normalized_zone_trigger_events(component)]
            self.assertEqual(['card.enter_graveyard'],kinds)

    def test_composition_token_parent_and_card_parts_keep_independent_histories(self):
        from types import SimpleNamespace
        from quorune.composition_departure import CompositionZoneFact,CompositionZoneRole
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.zone_trigger_processing import record_zone_change_history
        parent=ContinuousObjectIdentity('object-1','object-1@2');recorded=[]
        host=SimpleNamespace(state=SimpleNamespace(turn_history=SimpleNamespace(arrival_history_version=1,
            graveyard_type_history_version=1,departure_history_version=1)),
            _record_turn_history=lambda kind,**data:recorded.append((kind,data)))
        whole=occurrence(origin='battlefield',destination='graveyard',token=True,card_object=False,
            previous_logical_object_id='object-1@2',current_logical_object_id='object-1@3',
            previous_characteristics={'type_line':'Creature'},
            composition=CompositionZoneFact(parent,CompositionZoneRole.PERMANENT,2))
        card=occurrence(object_id='card-part',origin='battlefield',destination='graveyard',
            previous_characteristics={'type_line':'Artifact Creature'},current_characteristics={'type_line':'Artifact'},
            composition=CompositionZoneFact(parent,CompositionZoneRole.COMPONENT,2))
        token=occurrence(object_id='token-part',origin='battlefield',destination='graveyard',token=True,card_object=False,
            previous_characteristics={'type_line':'Creature'},
            composition=CompositionZoneFact(parent,CompositionZoneRole.COMPONENT,2))
        for item in (whole,card,token):record_zone_change_history(host,item)
        kinds=[kind for kind,_data in recorded]
        self.assertEqual(1,kinds.count('permanent_left'));self.assertEqual(1,kinds.count('creature_died'))
        self.assertEqual(1,kinds.count('card_arrived_graveyard'))
        arrival=next(data for kind,data in recorded if kind=='card_arrived_graveyard')
        self.assertEqual({'artifact'},arrival['types'])

    def test_composition_event_wire_is_additive_and_rejects_contradictory_scope(self):
        from dataclasses import replace
        from quorune.composition_departure import CompositionZoneFact,CompositionZoneRole
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        ordinary=occurrence();self.assertNotIn('composition',ordinary.to_dict())
        parent=ContinuousObjectIdentity('object-1','object-1@2')
        fact=CompositionZoneFact(parent,CompositionZoneRole.PERMANENT,2)
        self.assertEqual(fact,CompositionZoneFact.from_dict(fact.to_dict()))
        valid=occurrence(origin='battlefield',destination='graveyard',previous_logical_object_id='object-1@2',
            composition=fact.to_dict())
        self.assertEqual(fact.to_dict(),valid.to_dict()['composition'])
        with self.assertRaises(ValueError):replace(valid,previous_logical_object_id='object-1@4')
        with self.assertRaises(ValueError):replace(valid,origin='hand')
        with self.assertRaises(ValueError):replace(valid,destination='battlefield')
        component=replace(valid,composition=CompositionZoneFact(parent,CompositionZoneRole.COMPONENT,2))
        with self.assertRaises(ValueError):replace(component,transition_kind=ZoneTransitionKind.SACRIFICE)
        for changed in ({**fact.to_dict(),'member_count':True},{**fact.to_dict(),'role':'both'},
                        {**fact.to_dict(),'extra':True}):
            with self.assertRaises(ValueError):CompositionZoneFact.from_dict(changed)

    def test_entry_cast_information_is_additive_and_bound_to_the_committed_entry(self):
        from quorune.source_entry_cast_information import EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        ordinary=occurrence()
        self.assertNotIn('source_entry_cast_information',ordinary.to_dict())
        self.assertTrue(all('source_entry_cast_information' not in event.context
            for event in normalized_zone_trigger_events(ordinary)))
        info=EntryCastInformation(ContinuousObjectIdentity(ordinary.object_id,'object-1@1'),None)
        current=occurrence(previous_logical_object_id='object-1@0',current_logical_object_id='object-1@1',
            source_entry_cast_information=info.to_dict())
        self.assertEqual(info.to_dict(),current.to_dict()['source_entry_cast_information'])
        self.assertTrue(any(event.context.get('source_entry_cast_information')==info.to_dict()
            for event in normalized_zone_trigger_events(current)))
        with self.assertRaises(ValueError):
            occurrence(source_entry_cast_information=EntryCastInformation(
                ContinuousObjectIdentity('other','other@1'),None).to_dict())

    def test_anywhere_graveyard_event_reads_destination_facts_and_observers(self):
        for origin, transition_kind in (
            ('battlefield', ZoneTransitionKind.ORDINARY),
            ('library', ZoneTransitionKind.ORDINARY),
            ('hand', ZoneTransitionKind.DISCARD),
            ('stack', ZoneTransitionKind.COUNTERED_SPELL),
        ):
            with self.subTest(origin=origin):
                entered = [event for event in normalized_zone_trigger_events(occurrence(
                    origin=origin, destination='graveyard', transition_kind=transition_kind,
                    previous_controller='B', current_controller='A',
                    previous_characteristics={'type_line': 'Artifact Creature — Golem', 'colors': ['U']},
                    current_characteristics={'type_line': 'Land — Forest', 'colors': []},
                    previous_copy_snapshot={'before': True}, current_copy_snapshot={'after': True},
                )) if event.kind == 'card.enter_graveyard']
                self.assertEqual(1, len(entered))
                event = entered[0]
                self.assertEqual('after', event.source_timing)
                self.assertEqual('A', event.context['owner'])
                self.assertEqual('A', event.context['controller'])
                self.assertEqual(['land'], list(event.context['types']))
                self.assertEqual(['forest'], list(event.context['subtypes']))
                self.assertEqual([], list(event.context['colors']))
                self.assertEqual('object-1:1', event.context['card_object_identity'])
                self.assertEqual({'after': True}, dict(event.context['copiable_snapshot']))
        for changes in ({'token': True}, {'card_object': False}, {'destination': 'exile'}, {'origin': 'graveyard'}):
            with self.subTest(excluded=changes):
                events = normalized_zone_trigger_events(occurrence(
                    **{'origin': 'battlefield', 'destination': 'graveyard', **changes},
                ))
                self.assertFalse(any(event.kind == 'card.enter_graveyard' for event in events))

    def test_copiable_event_snapshots_use_entry_or_departure_incarnation(self):
        entered = normalized_zone_trigger_events(occurrence(
            previous_copy_snapshot={"marker":"before"}, current_copy_snapshot={"marker":"after"}))
        for event in entered:
            self.assertEqual("object-1:1", event.context["card_object_identity"])
            self.assertEqual("after", event.context["copiable_snapshot"]["marker"])
        departed = normalized_zone_trigger_events(occurrence(origin="battlefield", destination="graveyard",
            previous_copy_snapshot={"marker":"before"}, current_copy_snapshot=None))
        for event in departed:
            if event.kind == 'card.enter_graveyard':
                self.assertEqual('object-1:1', event.context['card_object_identity'])
                self.assertNotIn('copiable_snapshot', event.context)
                continue
            self.assertEqual("object-1:0", event.context["card_object_identity"])
            self.assertEqual("before", event.context["copiable_snapshot"]["marker"])

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
                "card.enter_graveyard",
            ],
            [event.kind for event in died],
        )
        self.assertEqual('after', died[-1].source_timing)
        self.assertTrue(all(event.source_timing == "before" for event in died[:-1]))
        self.assertTrue(
            all(event.context["controller"] == "B" for event in died[:-1])
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
            ["permanent.leave", "permanent.graveyard", "card.enter_graveyard"],
            [event.kind for event in departed],
        )
        self.assertEqual(["goblin"], list(departed[-2].context["subtypes"]))
        self.assertEqual(["cleric", "human"], list(departed[-1].context["subtypes"]))
        self.assertIn("kindred", departed[-2].context["types"])
        self.assertNotIn("creature", departed[-2].context["types"])

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

        self.assertEqual('card.enter_graveyard', events[-1].kind)
        self.assertEqual('after', events[-1].source_timing)
        historical_events = [event for event in events if event.kind != 'card.enter_graveyard']

        self.assertEqual(
            ["spell.countered", "card.graveyard"],
            [event.kind for event in historical_events],
        )
        self.assertEqual(
            ["before", "after"],
            [event.source_timing for event in historical_events],
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
        self.assertEqual("before", events[-1].source_timing)

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
