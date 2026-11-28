from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.trigger_discovery import _source_may_observe_event, dispatch_semantic_event


class SemanticEventInterestTests(unittest.TestCase):
    def program(self, event='activate', condition=None, trust='trusted'):
        # The discovery owner pairs each program with an optional attachment payment.
        return SimpleNamespace(event=event, event_condition=condition, trust_level=trust), None

    def test_only_unconditional_unrelated_subscriptions_are_rejected(self):
        source = SimpleNamespace(annotations={})
        self.assertFalse(_source_may_observe_event(source, 'card.drawn', ()))
        self.assertFalse(_source_may_observe_event(source, 'card.drawn', (
            self.program(), self.program('characteristics.evaluate'),
        )))
        for program in (self.program('card.drawn'), self.program('card.drawn.self')):
            self.assertTrue(_source_may_observe_event(source, 'card.drawn', (program,)))

    def test_conditions_and_uncertain_metadata_keep_the_existing_path(self):
        source = SimpleNamespace(annotations={})
        for program in (
            self.program(condition={}),
            self.program(condition={'kind': 'fixed_event_subscriptions', 'subscriptions': []}),
            self.program(trust='unresolved'),
            self.program(event=None),
        ):
            with self.subTest(program=program):
                self.assertTrue(_source_may_observe_event(source, 'card.drawn', (program,)))

    @staticmethod
    def source_tap_subscriber():
        from quorune.triggered_mana_model import (
            SOURCE_MANA_TAP_CAPABILITY, SOURCE_MANA_TAP_DAMAGE_MECHANIC, SOURCE_MANA_TAP_EVENT,
            source_mana_tap_condition,
        )

        return SimpleNamespace(
            event=SOURCE_MANA_TAP_EVENT, active_zone="battlefield", trust_level="trusted",
            coverage=(SOURCE_MANA_TAP_DAMAGE_MECHANIC,), capability_dependencies=(SOURCE_MANA_TAP_CAPABILITY,),
            event_condition=source_mana_tap_condition(),
        )

    def test_exact_source_mana_interest_keeps_matching_and_uncertain_occurrences(self):
        source = SimpleNamespace(annotations={}, zone="battlefield", logical_object_id="druid@2", controller="D")
        program = self.source_tap_subscriber()
        matching = {"card_object_identity": "druid@2", "player": "D"}
        nonmatching = {"card_object_identity": "one-of-four-lands@1", "player": "B"}

        def interested(context):
            return _source_may_observe_event(source, program.event, ((program, None),), context=context)

        self.assertTrue(interested(matching))
        self.assertFalse(interested(nonmatching))
        self.assertFalse(interested({**matching, "card_object_identity": "druid@1"}))
        self.assertFalse(interested({**matching, "player": "B"}))
        self.assertTrue(interested(None))
        self.assertTrue(interested({"player": "D"}))
        self.assertTrue(interested({**nonmatching, "intervening_condition_controller": "D"}))

        ordinary = self.program(program.event)
        self.assertTrue(_source_may_observe_event(
            source, program.event, ((program, None), ordinary), context=nonmatching,
        ))
        state_trigger = self.program("state.condition")
        self.assertTrue(_source_may_observe_event(
            source, program.event, ((program, None), state_trigger), context=nonmatching,
        ))
        program.trust_level = "unresolved"
        self.assertTrue(interested(nonmatching))

    def test_unrelated_exact_mana_tap_keeps_one_read_frame_through_observations(self):
        from quorune.characteristic_read_frames import memoized_characteristic_read
        from quorune.trigger_discovery import _iter_event_observers

        host = SimpleNamespace()
        druid = SimpleNamespace(annotations={}, zone="battlefield", logical_object_id="druid@2", controller="D")
        other = SimpleNamespace(annotations={})
        program = self.source_tap_subscriber()
        sources = (other, druid, other)
        builds = []

        def read_programs(source):
            memoized_characteristic_read(host, ("observer-probe",), lambda: builds.append("read"))
            return "battlefield", {}, ((program, None),) if source is druid else ()

        current = {"card_object_identity": "land@1", "player": "B"}
        self.assertEqual(
            [], list(_iter_event_observers(host, sources, program.event, read_programs, context=current)),
        )
        self.assertEqual(["read"], builds)
        builds.clear()
        self.assertEqual(
            [druid], [source for source, _ in _iter_event_observers(host, sources, program.event, read_programs)],
        )
        self.assertEqual(["read", "read"], builds)

    def test_shared_keyword_paths_are_never_filtered(self):
        self.assertTrue(_source_may_observe_event(SimpleNamespace(annotations={}), 'spell.cast', ()))
        for annotations in ({'evoked': True}, {'fixed_blitz_designation': {}}, {'fixed_blitz_designation': False}):
            with self.subTest(annotations=annotations):
                self.assertTrue(_source_may_observe_event(SimpleNamespace(annotations=annotations), 'permanent.enter', ()))

    def test_unrelated_source_dispatch_allocates_nothing_and_skips_participation(self):
        source = SimpleNamespace(annotations={})
        batch = []
        with patch('quorune.trigger_discovery._ordered_event_sources', return_value=[source]), \
             patch('quorune.trigger_discovery._event_programs_for_source',
                   return_value=('battlefield', {}, [self.program()])), \
             patch('quorune.rules.trigger_participation_runtime.typed_trigger_participations',
                   side_effect=AssertionError('Unrelated source reached participation')):
            self.assertEqual([], dispatch_semantic_event(SimpleNamespace(), 'card.drawn', {}, trigger_batch=batch))
        self.assertEqual([], batch)
        self.assertEqual({}, source.annotations)
