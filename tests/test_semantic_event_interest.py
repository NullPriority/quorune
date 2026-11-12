from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.trigger_discovery import _source_may_observe_event, dispatch_semantic_event


class SemanticEventInterestTests(unittest.TestCase):
    def program(self, event='activate', condition=None, trust='trusted'):
        return SimpleNamespace(event=event, event_condition=condition, trust_level=trust)

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
